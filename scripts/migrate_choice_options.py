"""迁移脚本：将题库中选择题的错误选项排列归一化为 exam-zh `choices` 网格环境。

覆盖两种历史情况：
1. 选项以内联形式书写（如 `A. $...$ B. $...$ C. $...$ D. $...$`）；
2. 已使用 `\\begin{choices}` 但 \\item 内仍残留显式 A./B./C./D. 标号（会与渲染器自动编号重复）。

仅作用于选择题（question_type 为 single_choice / multi_choice）或 content 中已含
`\\begin{choices}` 的题目，其余题型内容保持不变。
"""

from mathbank.database import Question, SessionLocal
from mathbank.sync_helper import export_database_to_files
from mathbank.latex_normalize import normalize_choice_options_to_latex


def run_migration():
    session = SessionLocal()
    updated = 0
    try:
        questions = session.query(Question).all()
        for q in questions:
            is_choice = q.question_type in ("single_choice", "multi_choice")
            content = q.content or ""
            if not (is_choice or r"\begin{choices}" in content):
                continue
            new_content = normalize_choice_options_to_latex(content)
            if new_content != content:
                q.content = new_content
                updated += 1
        session.commit()
        print(f"已将 {updated} 道选择题的选项错误排列归一化为 choices 网格环境。")
        print("正在同步更新 data_backup 备份文件...")
        export_database_to_files()
        print("备份文件同步更新完毕！")
    except Exception as e:
        session.rollback()
        print(f"迁移处理发生异常: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    run_migration()
