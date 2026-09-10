#!/usr/bin/env python3
"""题目来源（questions.source）一次性归一化。

规约（详见 mathbank/source_normalize.py 模块 docstring）：
- 校内考试 -> {学段} · {考试类型} · {学校} · {学年}
- 高考真题 -> {年份} · {卷种} · 高考真题（分隔符带空格/不带空格均合规）
- 模拟     -> {年份} · {地区} · 模拟
- 联考     -> {年份} · {考试名} · 联考
- 专题汇编 -> 高考 · 专题汇编 · {专题}
- 教辅自编 -> 教辅 · {名称}
- 空值     -> 未知

新值一律由 ``normalize_source()`` 计算，与写库入口的实时钩子同源（单一事实源），
保证「事后归一」与「录入即归一」语义完全一致。遇到映射表未覆盖的新来源时，
函数会自动走结构兜底（周练 / 模拟联考识别、噪声剥离、分隔符归一），
不再要求每个来源都预先登记——故不再存在「FATAL 未覆盖而中止」。

用法：
  python scripts/normalize_sources.py            # 执行（事务内 UPDATE，自动备份）
  python scripts/normalize_sources.py --dry-run  # 仅打印改动预览，不改库、不备份

可重复执行（幂等）：已符合规约的行不会被改动。
回滚：用同目录 math_question_bank.db.bak-YYYYMMDD-sources 覆盖即可。
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 让脚本能 import 项目包（脚本在 scripts/ 下，包在上级）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mathbank.source_normalize import (  # noqa: E402
    is_canonical_source,
    normalize_source,
)

DB_PATH = ROOT / "math_question_bank.db"


def main():
    dry = "--dry-run" in sys.argv
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT source, COUNT(*) FROM questions GROUP BY source ORDER BY COUNT(*) DESC")
    rows = cur.fetchall()

    updates = [(normalize_source(old), old) for (old, _) in rows]
    changed = [(new, old) for new, old in updates if new != old]
    counts = {old: cnt for old, cnt in rows}

    print(f"待处理来源种类: {len(rows)}  实际将改动种类: {len(changed)}"
          f"（涉及 {sum(counts[old] for _, old in changed)} 题）")
    print(f"模式: {'DRY-RUN（不改库、不备份）' if dry else 'EXECUTE（事务内 UPDATE，先备份）'}\n")

    if changed:
        print("改动预览:")
        for new, old in sorted(changed, key=lambda x: -counts[x[1]]):
            print(f"  {counts[old]:>4}题  {old!r}\n         ->  {new!r}")

    # 归一后仍游离于规约之外的来源：不阻断，但要显式报告，便于补映射或补规约
    residual = [(new, old) for new, old in updates if not is_canonical_source(new)]
    if residual:
        print(f"\n[WARN] 归一后仍有 {len(residual)} 种来源不符合规约（需补映射或扩展规约）:")
        for new, old in sorted(residual, key=lambda x: -counts[x[1]]):
            print(f"  {counts[old]:>4}题  {old!r}  ->  {new!r}")
    else:
        print("\n[OK] 归一后全部来源均符合规约。")

    if dry:
        con.close()
        return

    if not changed:
        print("\n无需改动，跳过备份与写入。")
        con.close()
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ROOT / f"math_question_bank.db.bak-{stamp}-sources"
    shutil.copy2(str(DB_PATH), str(backup))
    print(f"\n已备份数据库 -> {backup.name}")

    try:
        cur.execute("BEGIN")
        cur.executemany(
            "UPDATE questions SET source=? WHERE source=?",
            [(new, old) for new, old in changed],
        )
        con.commit()
        print(f"已提交：更新 {len(changed)} 个来源种类"
              f"（{sum(counts[old] for _, old in changed)} 题）。")
    except Exception as e:
        con.rollback()
        sys.exit(f"[ROLLBACK] 执行失败，已回滚：{e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
