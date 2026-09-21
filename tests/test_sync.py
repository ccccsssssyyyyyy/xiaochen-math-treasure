import os
import json
import time
import pytest
from unittest.mock import patch
from mathbank.database import Question
from mathbank.sync_helper import clean_latex_to_markdown_for_ai, export_database_to_files
from main import clean_orphaned_images

def test_clean_latex_to_markdown_for_ai():
    # Test itemize list cleaning
    latex_text = "\\begin{itemize}\n\\item 第一项\n\\item[A.] 选项 A\n\\end{itemize}"
    cleaned = clean_latex_to_markdown_for_ai(latex_text)
    # Normalize whitespaces for robust assertion
    normalized = " ".join(cleaned.split())
    assert "- 第一项" in normalized
    assert "- A. 选项 A" in normalized
    assert "\\begin{itemize}" not in cleaned
    assert "\\end{itemize}" not in cleaned

    # Test underline/blank cleaning
    latex_underline = "请在 \\underline{\\hspace{2cm}} 处填空，或者使用 \\underline{自定义内容}"
    cleaned_ul = clean_latex_to_markdown_for_ai(latex_underline)
    assert "_______" in cleaned_ul
    assert "\\underline" not in cleaned_ul

    # Test double backslashes formatting
    latex_newline = "第一行\\\\第二项\\\\\\第三行"
    cleaned_nl = clean_latex_to_markdown_for_ai(latex_newline)
    assert "\n" in cleaned_nl
    assert "\\\\" not in cleaned_nl

    # Test formula preservation: Standard formulas inside $...$ or $$...$$ must be kept!
    latex_formula = "设函数 $f(x) = x^2 + 2x$ 在区间 $[0, 1]$ 上的最大值为 $$M$$"
    cleaned_formula = clean_latex_to_markdown_for_ai(latex_formula)
    assert "$f(x) = x^2 + 2x$" in cleaned_formula
    assert "$$M$$" in cleaned_formula

    # Test choices environment cleaning
    latex_choices = "\\begin{choices}\n\\item 选项一\n\\item 选项二\n\\end{choices}"
    cleaned_choices = clean_latex_to_markdown_for_ai(latex_choices)
    normalized_choices = " ".join(cleaned_choices.split())
    assert "- A. 选项一" in normalized_choices
    assert "- B. 选项二" in normalized_choices
    assert "\\begin{choices}" not in cleaned_choices
    assert "\\end{choices}" not in cleaned_choices


def test_export_database_to_files(db_session, tmp_path):
    # Setup mock Question
    q = Question(
        content="这只是一道测试同步导出的题目 $x+y=2$",
        question_type="single_choice",
        category_compulsory="必修一",
        category_chapter="第一章",
        category_knowledge="知识点A",
        difficulty="easy"
    )
    db_session.add(q)
    db_session.commit()

    # Re-route backup paths in sync_helper module to tmp_path to avoid writing to real data_backup directory during tests
    from mathbank import sync_helper
    original_backup_dir = sync_helper.BACKUP_DIR
    original_json_path = sync_helper.JSON_BACKUP_PATH
    original_md_path = sync_helper.MD_BACKUP_PATH

    sync_helper.BACKUP_DIR = str(tmp_path)
    sync_helper.JSON_BACKUP_PATH = os.path.join(sync_helper.BACKUP_DIR, "questions_backup.json")
    sync_helper.MD_BACKUP_PATH = os.path.join(sync_helper.BACKUP_DIR, "questions_library.md")

    try:
        # Run export
        result = export_database_to_files(db=db_session)
        assert result["status"] == "success"
        assert result["question_count"] == 1

        # Check JSON export
        assert os.path.exists(sync_helper.JSON_BACKUP_PATH)
        with open(sync_helper.JSON_BACKUP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert len(data) == 1
            assert data[0]["content"] == q.content

        # Check MD export
        assert os.path.exists(sync_helper.MD_BACKUP_PATH)
        with open(sync_helper.MD_BACKUP_PATH, "r", encoding="utf-8") as f:
            md_content = f.read()
            assert "这只是一道测试同步导出的题目" in md_content
            assert "$x+y=2$" in md_content
            assert "必修一" in md_content
    finally:
        # Restore paths
        sync_helper.BACKUP_DIR = original_backup_dir
        sync_helper.JSON_BACKUP_PATH = original_json_path
        sync_helper.MD_BACKUP_PATH = original_md_path


def test_export_failure_preserves_last_good_files(db_session, tmp_path):
    from mathbank import sync_helper

    q = Question(content="测试原子导出", question_type="single_choice")
    db_session.add(q)
    db_session.commit()
    json_path = tmp_path / "questions_backup.json"
    markdown_path = tmp_path / "questions_library.md"
    json_path.write_text("last-good-json", encoding="utf-8")
    markdown_path.write_text("last-good-markdown", encoding="utf-8")
    original_json_path = sync_helper.JSON_BACKUP_PATH
    original_md_path = sync_helper.MD_BACKUP_PATH
    sync_helper.JSON_BACKUP_PATH = str(json_path)
    sync_helper.MD_BACKUP_PATH = str(markdown_path)

    try:
        with patch(
            "mathbank.sync_helper.generate_markdown_library",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(RuntimeError, match="导出题目文件失败"):
                export_database_to_files(db=db_session)
        assert json_path.read_text(encoding="utf-8") == "last-good-json"
        assert markdown_path.read_text(encoding="utf-8") == "last-good-markdown"
    finally:
        sync_helper.JSON_BACKUP_PATH = original_json_path
        sync_helper.MD_BACKUP_PATH = original_md_path


def test_clean_orphaned_images(db_session):
    import main
    upload_dir = main.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)

    # 1. Create a question referencing one image
    ref_image_path = f"{main.UPLOAD_DIR_REL}/test_referenced_old_image.png"
    q = Question(
        content="测试图片引用",
        question_type="single_choice"
    )
    q.image_paths = ["/" + ref_image_path] # standard absolute path starting with slash
    db_session.add(q)
    db_session.commit()

    # 2. Create the physical files
    ref_full_path = os.path.join(upload_dir, "test_referenced_old_image.png")
    old_orphan_path = os.path.join(upload_dir, "test_orphan_old_image.png")
    new_orphan_path = os.path.join(upload_dir, "test_orphan_new_image.png")

    for p in [ref_full_path, old_orphan_path, new_orphan_path]:
        with open(p, "w") as f:
            f.write("test_image_data")

    # Set file modification times (mtimes)
    now = time.time()
    two_hours_ago = now - 7200
    
    os.utime(ref_full_path, (two_hours_ago, two_hours_ago))     # Old but Referenced
    os.utime(old_orphan_path, (two_hours_ago, two_hours_ago))   # Old and Orphaned
    os.utime(new_orphan_path, (now, now))                       # New and Orphaned

    try:
        # Run clean_orphaned_images
        clean_orphaned_images()

        # Check outcomes based on three safety guardrails:
        # - Old and Referenced: MUST BE KEPT!
        assert os.path.exists(ref_full_path)

        # - Old and Orphaned: MUST BE DELETED!
        assert not os.path.exists(old_orphan_path)

        # - New and Orphaned: MUST BE KEPT (due to 1-hour safety grace period)!
        assert os.path.exists(new_orphan_path)

    finally:
        # Clean up remaining created files
        for p in [ref_full_path, old_orphan_path, new_orphan_path]:
            if os.path.exists(p):
                os.remove(p)


def test_clean_orphaned_images_keeps_inline_referenced_figures(db_session):
    """只被题干/解析**内联引用**的图不能被当孤儿删掉。

    回归：旧实现只从 ``questions.image_paths`` 收集引用，正文里的
    ``![...](/static/uploads/x.png)`` 不在集合里，超过 1 小时就被清掉 ——
    表现为「积累的题目配图凭空消失」。
    """
    import main
    upload_dir = main.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)

    rel_dir = main.UPLOAD_DIR_REL
    inline_name = "test_inline_fig_content.png"
    answer_name = "test_inline_fig_answer.png"
    orphan_name = "test_inline_orphan.png"

    q = Question(
        content=f"如图 ![](/{rel_dir}/{inline_name}) 求阴影面积",
        question_type="detailed_answer",
        answer_markdown=f"解析见下图 ![](/{rel_dir}/{answer_name})",
    )
    q.image_paths = []  # 关键：结构化字段为空，引用只在正文/解析里
    db_session.add(q)
    db_session.commit()

    paths = {
        name: os.path.join(upload_dir, name)
        for name in (inline_name, answer_name, orphan_name)
    }
    for p in paths.values():
        with open(p, "w") as f:
            f.write("test_image_data")
    two_hours_ago = time.time() - 7200
    for p in paths.values():
        os.utime(p, (two_hours_ago, two_hours_ago))

    try:
        clean_orphaned_images()

        assert os.path.exists(paths[inline_name]), "题干内联图被误删"
        assert os.path.exists(paths[answer_name]), "解析内联图被误删"
        assert not os.path.exists(paths[orphan_name]), "真正的孤儿图应被删除"
    finally:
        for p in paths.values():
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                # 沙箱可能拦截 unlink；断言已跑完，清理失败不影响结论
                pass


def test_clean_orphaned_images_keeps_mistake_record_figures(db_session):
    """错题记录引用的块图/截图也不能被当孤儿删掉。"""
    import main
    from mathbank.database import MistakeBatch, MistakeRecord, Student

    upload_dir = main.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    rel_dir = main.UPLOAD_DIR_REL

    student = Student(name="测试学生")
    db_session.add(student)
    db_session.flush()
    batch = MistakeBatch(student_id=student.id, subject="math", title="批")
    db_session.add(batch)
    db_session.flush()

    block_name = "test_mistake_block.png"
    record = MistakeRecord(
        batch_id=batch.id,
        subject="math",
        image_block=f"/{rel_dir}/{block_name}",
    )
    db_session.add(record)
    db_session.commit()

    block_path = os.path.join(upload_dir, block_name)
    with open(block_path, "w") as f:
        f.write("test_image_data")
    two_hours_ago = time.time() - 7200
    os.utime(block_path, (two_hours_ago, two_hours_ago))

    try:
        clean_orphaned_images()
        assert os.path.exists(block_path), "错题块图被误删"
    finally:
        try:
            if os.path.exists(block_path):
                os.remove(block_path)
        except OSError:
            pass


def test_run_daily_backup_once_triggers_scheduler_and_swallows_errors(monkeypatch):
    """长跑服务的滚动备份：敲一次门就走「到期才备份」的策略，失败不抛出。"""
    import main

    calls = []
    monkeypatch.setattr(
        main, "create_full_backup_if_due", lambda: calls.append("called")
    )
    main.run_daily_backup_once()
    assert calls == ["called"]

    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(main, "create_full_backup_if_due", _boom)
    # 备份失败只该被记录，绝不能让后台线程（或测试）崩掉
    main.run_daily_backup_once()
