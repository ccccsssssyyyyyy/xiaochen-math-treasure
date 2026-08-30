"""用系统自带的 AI 分类能力，批量补齐「未分类」题目的学段与章节。

为什么不用纯规则：实测规则法在解析几何等题型上误判严重（把椭圆/抛物线题分到
「平面向量」、把二项式题分到「函数」），抽查 6 例错 4~5 例；而同样这几题，
AI 分类（免费模型）全部判对。故改用 AI 为主。

实现要点：
  * 通过本地 HTTP 调用 /api/ai/classify（服务已在跑，直接 import main 会撞上运行时锁）；
    需带 X-Local-Token 请求头，token 读取自 .system_generated/local_token。
  * 返回值必须落在受控词表内（题库里已存在的 (学段, 章节) 组合），越界一律拒绝，
    防止污染标签体系。
  * 默认只演练（dry-run）；必须显式 --apply 才写库，写库前自动备份 DB 并同步 data_backup。
  * 单题失败不影响整体，失败题目会记入报告的 fail 列，可下轮重跑。

用法：
    cd <项目根>
    venv/bin/python scripts/classify_uncategorized_ai.py                 # 演练全量
    venv/bin/python scripts/classify_uncategorized_ai.py --limit 12      # 先小样本验证
    venv/bin/python scripts/classify_uncategorized_ai.py --apply         # 全量落库
    venv/bin/python scripts/classify_uncategorized_ai.py --free-model false --apply
"""

import argparse
import csv
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mathbank.database import Question, SessionLocal  # noqa: E402
from mathbank.paths import DATABASE_FILE, SYSTEM_GENERATED_DIR  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"


def load_token():
    token_file = os.path.join(str(SYSTEM_GENERATED_DIR), "local_token")
    with open(token_file, "r", encoding="utf-8") as fh:
        return fh.read().strip()


def load_vocabulary(session):
    """受控词表 = **官方课程树**，与 main.get_current_curriculum() 同源。

    ⚠️ 不要改成「从库里已用过的章节反推」：那样会漏掉尚未使用但合法的章节
    （首轮就因此误拒了 必修二/9. 统计 —— 它是课程树里的正式章节，只是库里还没题用过）。
    """
    from mathbank.curriculums import load_curriculum
    from mathbank.paths import DATA_BACKUP_DIR

    curriculum = None
    meta_path = os.path.join(str(DATA_BACKUP_DIR), "custom_metadata.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                curriculum = json.load(fh).get("curriculum")
        except Exception:
            curriculum = None
    if not curriculum:
        curriculum = load_curriculum("A")

    vocab = set()
    for compulsory, chapters in (curriculum or {}).items():
        for chapter in (chapters or {}):
            vocab.add((compulsory, chapter))
    return vocab


def call_classify(content, token, use_free=True, timeout=180):
    body = urllib.parse.urlencode({
        "content": content[:1500],
        "use_free_model": "true" if use_free else "false",
    }).encode()
    req = urllib.request.Request(BASE_URL + "/api/ai/classify", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Local-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只演练）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 道，用于小样本验证")
    ap.add_argument("--free-model", default="true", help="是否走免费模型路由")
    ap.add_argument("--sleep", type=float, default=0.3, help="每次请求间隔秒数，避免打爆服务商")
    args = ap.parse_args()

    use_free = args.free_model.lower() in ("true", "1", "yes")
    token = load_token()
    session = SessionLocal()

    try:
        vocab = load_vocabulary(session)
        print("受控词表：(学段, 章节) 组合 %d 个" % len(vocab))

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
        if args.limit:
            targets = targets[: args.limit]
        print("待处理：%d 道（免费模型=%s，落库=%s）" % (len(targets), use_free, args.apply))
        print("")

        ok = rejected = failed = 0
        rows_out = []
        for idx, q in enumerate(targets, 1):
            label = "#%s (%d/%d)" % (q.id, idx, len(targets))
            try:
                d = call_classify(q.content or "", token, use_free=use_free)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                failed += 1
                print("%s 请求失败: %s" % (label, str(e)[:100]))
                rows_out.append({"id": q.id, "result": "fail", "compulsory": "", "chapter": "",
                                 "note": str(e)[:120], "content": (q.content or "")[:80]})
                continue

            if d.get("status") != "success":
                failed += 1
                note = str(d.get("message") or d.get("detail") or d)[:120]
                print("%s 分类失败: %s" % (label, note))
                rows_out.append({"id": q.id, "result": "fail", "compulsory": "", "chapter": "",
                                 "note": note, "content": (q.content or "")[:80]})
                continue

            comp = (d.get("compulsory") or "").strip()
            chap = (d.get("chapter") or "").strip()
            if (comp, chap) not in vocab:
                rejected += 1
                note = "越界(%s / %s)" % (comp, chap)
                print("%s %s" % (label, note))
                rows_out.append({"id": q.id, "result": "reject", "compulsory": comp, "chapter": chap,
                                 "note": note, "content": (q.content or "")[:80]})
            else:
                ok += 1
                print("%s -> %s / %s" % (label, comp, chap))
                rows_out.append({"id": q.id, "result": "ok", "compulsory": comp, "chapter": chap,
                                 "note": "", "content": (q.content or "")[:80]})
            time.sleep(args.sleep)

        print("")
        print("=== 汇总 ===")
        print("  成功   : %3d" % ok)
        print("  越界拒绝: %3d" % rejected)
        print("  失败   : %3d" % failed)

        report_path = os.path.join(ROOT, "classify_uncategorized_ai_report.csv")
        with open(report_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["id", "result", "compulsory", "chapter", "note", "content"])
            w.writeheader()
            w.writerows(rows_out)
        print("清单已写出: %s" % report_path)

        if not args.apply:
            print("")
            print("⚠️  演练模式，未修改任何数据。确认后加 --apply 落库。")
            return

        db_path = str(DATABASE_FILE)
        backup_path = "%s.bak_aiclassify_%s" % (db_path, time.strftime("%Y%m%d_%H%M%S"))
        print("")
        print("备份数据库 -> %s" % backup_path)
        for suffix in ("", "-wal", "-shm"):
            src = db_path + suffix
            if os.path.exists(src):
                shutil.copy2(src, backup_path + suffix)

        updated = 0
        for q, r in zip(targets, rows_out):
            if r["result"] != "ok":
                continue
            q.category_compulsory = r["compulsory"]
            q.category_chapter = r["chapter"]
            updated += 1
        session.commit()
        print("已更新 %d 道题目。" % updated)

        from mathbank.sync_helper import export_database_to_files
        print("正在同步 data_backup 备份文件...")
        export_database_to_files()
        print("完成。")
    except Exception as e:
        session.rollback()
        print("异常，已回滚: %s" % e)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
