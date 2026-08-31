#!/usr/bin/env python3
"""题目来源（questions.source）一次性归一化。

规约：
- 校内考试 -> {学段} · {考试类型} · {学校} · {学年}
- 高考真题 -> {年份} · {卷种} · 高考真题
- 专题汇编 -> 高考 · 专题汇编 · {专题}
- 空值 -> 未知

映射表与实时钩子共用 ``mathbank.source_normalize.CANONICAL_MAP``（单一事实源），
保证「事后归一」与「录入即归一」语义完全一致。

用法：
  python scripts/normalize_sources.py            # 执行（事务内 UPDATE）
  python scripts/normalize_sources.py --dry-run  # 仅打印影响行数，不改库

可重复执行（幂等）：已符合新值的行不会被改动。
回滚：用同目录 math_question_bank.db.bak-YYYYMMDD-sources 覆盖即可。
"""
import os
import sqlite3
import sys
from pathlib import Path

# 让脚本能 import 项目包（脚本在 scripts/ 下，包在上级）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mathbank.source_normalize import CANONICAL_MAP as M  # noqa: E402

DB_PATH = ROOT / "math_question_bank.db"


def main():
    dry = '--dry-run' in sys.argv
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("SELECT source, COUNT(*) FROM questions GROUP BY source")
    rows = cur.fetchall()
    db_vals = {r[0] for r in rows}
    missing = db_vals - set(M.keys())
    if missing:
        sys.exit(f"[FATAL] 以下来源未覆盖，已中止（避免部分归一导致混乱）:\n{missing}")

    updates = [(M[old], old) for (old, _) in rows]
    affected = sum(1 for new, old in updates if new != old)
    print(f"待处理来源种类: {len(rows)}  实际将改动行: {affected}（其余已符合，跳过）")
    print(f"模式: {'DRY-RUN（不改库）' if dry else 'EXECUTE（事务内 UPDATE）'}")

    if dry:
        for new, old in updates:
            if new != old:
                print(f"  {old!r}  ->  {new!r}")
        con.close()
        return

    try:
        cur.execute("BEGIN")
        cur.executemany("UPDATE questions SET source=? WHERE source=?", updates)
        con.commit()
        print(f"已提交：更新 {affected} 个来源种类对应的题目。")
    except Exception as e:
        con.rollback()
        sys.exit(f"[ROLLBACK] 执行失败，已回滚：{e}")
    finally:
        con.close()


if __name__ == '__main__':
    main()
