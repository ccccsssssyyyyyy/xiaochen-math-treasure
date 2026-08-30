"""批量补齐「未分类」题目的学段与章节归属。

设计原则：只使用题库里**已经存在**的 (学段, 章节) 组合，绝不臆造新章节，
避免污染受控词表（历史上出过标签混乱问题）。

分三层判定，逐层降级，置信度递减：
  T1 标签唯一定位 —— 该题的 knowledge_list 标签在已分类题库中只对应一个章节。最可靠。
  T2 标签多数表决 —— 标签对应多个章节，但最高票数 ≥ DOMINANCE_RATIO 倍于次高。次可靠。
  T3 题干关键词   —— 标签没命中时，用高置信特征词匹配题干，且必须**唯一**命中一个章节
                     （命中多个即判定为歧义，宁可放过也不错分）。
  未命中          —— 留给 AI 分类或人工处理。

安全性：
  * 默认只演练（dry-run），打印汇总并写出 CSV 清单供人工审阅；
  * 必须显式传 --apply 才写库，写库前自动备份 DB 并同步 data_backup。

用法：
    cd <项目根>
    venv/bin/python scripts/classify_uncategorized.py            # 演练
    venv/bin/python scripts/classify_uncategorized.py --apply    # 落库
"""

import collections
import csv
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.database import Question, SessionLocal  # noqa: E402
from mathbank.paths import DATABASE_FILE  # noqa: E402

# T2 多数表决的置信门槛：最高票数需达到次高的多少倍
DOMINANCE_RATIO = 2.0

# T3 关键词表：(学段, 章节) -> 高置信特征词
# 仅收录「出现即基本可定性」的强特征，宁缺勿滥；命中多个章节即视为歧义放过。
KEYWORD_RULES = [
    (("必修一", "1. 集合与常用逻辑用语"), [
        "充要条件", "充分条件", "必要条件", "全称量词", "存在量词",
        "命题的否定", "补集", "子集", "交集", "并集",
    ]),
    (("必修一", "2. 一元二次函数、方程和不等式"), [
        "基本不等式", "一元二次不等式", "均值不等式",
    ]),
    (("必修一", "3. 函数的概念与性质"), [
        "奇偶性", "单调性", "分段函数", "函数的定义域", "函数的值域",
        "单调递增", "单调递减",
    ]),
    (("必修一", "4. 指数函数与对数函数"), [
        "对数函数", "指数函数", "\\log_", "对数",
    ]),
    (("必修一", "5. 三角函数"), [
        "三角函数", "正弦函数", "余弦函数", "诱导公式", "正弦定理", "余弦定理",
    ]),
    (("必修二", "6. 平面向量及其应用"), [
        "平面向量", "数量积", "共线向量", "向量的模",
    ]),
    (("必修二", "7. 复数"), [
        "复数", "虚部", "实部", "共轭复数", "虚数单位",
    ]),
    (("必修二", "8. 立体几何初步"), [
        "三视图", "直观图", "线面平行", "线面垂直", "面面垂直",
        "四棱锥", "三棱柱",
    ]),
    (("必修二", "10. 概率"), [
        "古典概型", "互斥事件", "对立事件", "相互独立",
    ]),
    (("选修一", "1. 空间向量与立体几何"), [
        "空间向量", "二面角", "异面直线", "法向量", "外接球",
    ]),
    (("选修一", "2. 直线和圆的方程"), [
        "直线与圆", "圆的方程", "点到直线的距离",
    ]),
    (("选修一", "3. 圆锥曲线的方程"), [
        "椭圆", "双曲线", "抛物线", "离心率", "准线", "焦距",
    ]),
    (("选修二", "4. 数列"), [
        "等差数列", "等比数列", "通项公式", "前n项和", "前 n 项和",
    ]),
    (("选修二", "5. 一元函数的导数及其应用"), [
        "导数", "极值点", "单调递增区间", "切线方程", "导函数",
    ]),
    (("选修三", "6. 计数原理"), [
        "二项式定理", "排列数", "组合数",
    ]),
    (("选修三", "7. 随机变量及其分布"), [
        "随机变量", "分布列", "数学期望", "二项分布", "正态分布",
    ]),
    (("选修三", "8. 成对数据的统计分析"), [
        "相关系数", "列联表", "独立性检验", "残差",
    ]),
]


def build_tag_evidence(session):
    """知识点标签 -> {(学段, 章节): 出现次数}，只统计已分类题目。"""
    evidence = collections.defaultdict(collections.Counter)
    rows = (
        session.query(Question.knowledge_list, Question.category_compulsory, Question.category_chapter)
        .filter(Question.category_chapter.isnot(None))
        .filter(Question.category_chapter != "")
        .filter(Question.category_chapter != "未分类")
        .all()
    )
    for knowledge_list, compulsory, chapter in rows:
        if not knowledge_list:
            continue
        for tag in [t.strip() for t in knowledge_list.split(",") if t.strip()]:
            evidence[tag][(compulsory or "", chapter)] += 1
    return evidence


def keyword_hits(content):
    """返回题干命中的章节集合；命中多个说明歧义，交由调用方放过。"""
    text = content or ""
    hits = set()
    for pair, keywords in KEYWORD_RULES:
        for kw in keywords:
            if kw in text:
                hits.add(pair)
                break
    return hits


def propose(question, evidence):
    """返回 (学段, 章节, 判定层, 说明)；无法判定时返回 (None, None, None, 原因)。"""
    tags = [t.strip() for t in (question.knowledge_list or "").split(",") if t.strip()]

    # 汇总该题所有标签指向的章节证据
    votes = collections.Counter()
    for tag in tags:
        for pair, count in evidence.get(tag, {}).items():
            votes[pair] += count

    if len(votes) == 1:
        pair = next(iter(votes))
        return pair[0], pair[1], "T1", "标签唯一定位"

    if len(votes) > 1:
        ranked = votes.most_common()
        top, top_count = ranked[0]
        second_count = ranked[1][1]
        if top_count >= second_count * DOMINANCE_RATIO:
            detail = "多数表决 %d : %d" % (top_count, second_count)
            return top[0], top[1], "T2", detail
        return None, None, None, "标签证据冲突(%s)" % " / ".join(
            "%s-%s" % (p[1], c) for p, c in ranked[:2]
        )

    # 标签完全没命中，退到关键词
    hits = keyword_hits(question.content)
    if len(hits) == 1:
        pair = next(iter(hits))
        return pair[0], pair[1], "T3", "题干关键词命中"
    if len(hits) > 1:
        return None, None, None, "关键词歧义(%d 个章节命中)" % len(hits)
    return None, None, None, "无可用线索"


def run(apply_changes=False):
    session = SessionLocal()
    try:
        evidence = build_tag_evidence(session)
        targets = (
            session.query(Question)
            .filter(
                (Question.category_chapter.is_(None))
                | (Question.category_chapter == "")
                | (Question.category_chapter == "未分类")
            )
            .order_by(Question.id)
            .all()
        )

        print("已分类题库中提取的知识点标签: %d 个" % len(evidence))
        print("待处理未分类题目: %d 道" % len(targets))
        print("")

        stats = collections.Counter()
        rows_out = []
        for q in targets:
            compulsory, chapter, tier, detail = propose(q, evidence)
            if tier:
                stats[tier] += 1
            else:
                stats["未决"] += 1
            rows_out.append({
                "id": q.id,
                "tier": tier or "未决",
                "compulsory": compulsory or "",
                "chapter": chapter or "",
                "detail": detail,
                "knowledge_list": (q.knowledge_list or "")[:80],
                "content": (q.content or "").replace("\n", " ")[:100],
            })

        total = len(targets)
        resolved = stats["T1"] + stats["T2"] + stats["T3"]
        print("=== 判定结果 ===")
        print("  T1 标签唯一定位 : %3d" % stats["T1"])
        print("  T2 标签多数表决 : %3d" % stats["T2"])
        print("  T3 题干关键词   : %3d" % stats["T3"])
        print("  未决(需AI/人工) : %3d" % stats["未决"])
        print("  ------------------------")
        print("  规则可定         : %3d / %d (%.0f%%)" % (resolved, total, resolved / total * 100 if total else 0))
        print("")

        # 章节分布预览
        dist = collections.Counter((r["compulsory"], r["chapter"]) for r in rows_out if r["tier"] != "未决")
        print("=== 拟分配章节分布 ===")
        for (comp, chap), n in dist.most_common():
            print("  %-6s | %-24s | %3d 题" % (comp, chap, n))
        print("")

        report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "classify_uncategorized_report.csv")
        with open(report_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()) if rows_out else
                                    ["id", "tier", "compulsory", "chapter", "detail", "knowledge_list", "content"])
            writer.writeheader()
            writer.writerows(rows_out)
        print("清单已写出: %s" % report_path)

        if not apply_changes:
            print("")
            print("⚠️  当前为演练模式，未修改任何数据。确认无误后加 --apply 落库。")
            return

        # ---------- 落库 ----------
        db_path = str(DATABASE_FILE)
        backup_path = "%s.bak_classify_%s" % (db_path, time.strftime("%Y%m%d_%H%M%S"))
        print("")
        print("正在备份数据库 -> %s" % backup_path)
        for suffix in ("", "-wal", "-shm"):
            src = db_path + suffix
            if os.path.exists(src):
                shutil.copy2(src, backup_path + suffix)

        updated = 0
        for q, r in zip(targets, rows_out):
            if r["tier"] == "未决":
                continue
            q.category_compulsory = r["compulsory"]
            q.category_chapter = r["chapter"]
            updated += 1
        session.commit()
        print("已更新 %d 道题目的学段与章节。" % updated)

        from mathbank.sync_helper import export_database_to_files
        print("正在同步 data_backup 备份文件...")
        export_database_to_files()
        print("完成。")
    except Exception as e:
        session.rollback()
        print("处理发生异常，已回滚: %s" % e)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    run(apply_changes="--apply" in sys.argv)
