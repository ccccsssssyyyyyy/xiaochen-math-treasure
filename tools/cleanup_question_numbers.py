"""一次性脚本：清理题库中题干开头残留的原卷顺序题号。

只对 `content` 字段最开头的数字/中文序号前缀做剥离（与 main.py 的
_strip_leading_question_number 逻辑一致），避免组卷时出现重复编号。

用法：
    venv/bin/python tools/cleanup_question_numbers.py            # 试运行（仅打印，不写库）
    venv/bin/python tools/cleanup_question_numbers.py --apply    # 实际写入
"""
import argparse
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "math_question_bank.db"

_LEAD_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"\(?\d{1,3}\)?[\.、\)]|"          # 16.  16)  16、  (16)
    r"[一二三四五六七八九十百]{1,3}[\.、]|"  # 一. 二、
    r"\([一二三四五六七八九十]+\)|"       # （一）
    r"[①②③④⑤⑥⑦⑧⑨⑩]+\s?"             # ①②③
    r")\s*"
)


def strip_leading(content: str) -> str:
    if not content or not isinstance(content, str):
        return content
    cleaned = _LEAD_RE.sub("", content, count=1)
    return cleaned.strip() if cleaned else content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际写入数据库（默认试运行）")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT id, content FROM questions")
    rows = cur.fetchall()

    changed = []
    for qid, content in rows:
        if not content:
            continue
        new_content = strip_leading(content)
        if new_content != content:
            changed.append((qid, content, new_content))

    print(f"共扫描 {len(rows)} 题，命中残留题号 {len(changed)} 题。")
    for qid, old, new in changed[:20]:
        print(f"  [id={qid}] {old[:40]!r}  ->  {new[:40]!r}")

    if not args.apply:
        print("\n（试运行模式，未写入。加 --apply 才会真正更新）")
        conn.close()
        return

    for qid, old, new in changed:
        cur.execute("UPDATE questions SET content = ? WHERE id = ?", (new, qid))
    conn.commit()
    conn.close()
    print(f"\n已更新 {len(changed)} 题。")


if __name__ == "__main__":
    main()
