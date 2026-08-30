import re
from unittest.mock import patch

import pytest

from mathbank.content_locks import (
    ContentLockIntegrityError,
    lock_visible_math,
    restore_visible_math,
)


def test_visible_math_lock_keeps_formula_inline_and_restores_exact_source():
    source = r"已知 $f(x)=\dfrac{x+1}{x-1}$，价格为 \$5，且 $$A=\begin{matrix}1&2\\3&4\end{matrix}$$。"
    locked, locks = lock_visible_math(source, "task-1")
    assert len(locks) == 2
    assert r"$f(x)=\dfrac{x+1}{x-1}$" in locked
    assert r"$$A=\begin{matrix}1&2\\3&4\end{matrix}$$" in locked
    assert r"价格为 \$5" in locked

    questions = [{
        "content": f"已知 [[{locks[0].lock_id}]]，且 [[{locks[1].lock_id}]]。",
        "answer_markdown": "",
    }]
    report = restore_visible_math(questions, locks)
    assert report["math_locks_restored"] == 2
    assert questions[0]["content"] == (
        r"已知 $f(x)=\dfrac{x+1}{x-1}$，且 $$A=\begin{matrix}1&2\\3&4\end{matrix}$$。"
    )


def test_parse_chunk_progress_callback_reports_every_chunk():
    """长卷分块解析时，每段完成（或失败跳过）都要回报一次进度。

    这是前端「静默超时」判定的依据：只要回调持续触发，任务就不该被判死。
    """
    from main import _parse_chunks_to_questions

    reported = []
    chunks_seen = []

    def fake_split(document_text):
        return ["段落一", "段落二", "段落三"]

    def fake_parse_single(user_content, decision, system_instructions, max_tokens, timeout, paid_timeout=None, chunk_markdown=None):
        chunks_seen.append(user_content)
        # 第二段模拟失败，验证失败路径同样会回报进度
        if len(chunks_seen) == 2:
            raise RuntimeError("模拟该段解析失败")
        return [{"content": f"题目{len(chunks_seen)}", "answer_markdown": ""}]

    with patch("main.split_markdown_into_question_chunks", side_effect=fake_split), \
         patch("main._parse_single_chunk", side_effect=fake_parse_single):
        result = _parse_chunks_to_questions(
            "任意试卷文本",
            {"provider": None},
            "系统提示",
            False,
            progress_callback=lambda done, total: reported.append((done, total)),
        )

    assert reported == [(1, 3), (2, 3), (3, 3)]
    assert len(result) == 2


def test_parse_chunk_progress_callback_never_breaks_parsing():
    """回调自身抛异常时不得影响解析主流程。"""
    from main import _parse_chunks_to_questions

    def fake_split(document_text):
        return ["段落一", "段落二"]

    seq = {"n": 0}

    def fake_parse_single(user_content, decision, system_instructions, max_tokens, timeout, paid_timeout=None, chunk_markdown=None):
        seq["n"] += 1
        # 题干必须各不相同，否则末尾的 dedupe_questions 会把两段合并成一条
        return [{"content": f"题目{seq['n']}", "answer_markdown": ""}]

    with patch("main.split_markdown_into_question_chunks", side_effect=fake_split), \
         patch("main._parse_single_chunk", side_effect=fake_parse_single):
        result = _parse_chunks_to_questions(
            "任意试卷文本",
            {"provider": None},
            "系统提示",
            False,
            progress_callback=lambda done, total: (_ for _ in ()).throw(RuntimeError("回调爆炸")),
        )

    assert len(result) == 2


def test_visible_math_lock_overwrites_model_modified_formula_inside_tag():
    locked, locks = lock_visible_math(r"求 $x^2+1$ 的最小值。", "task2")
    modified = locked.replace("x^2+1", "x^2-1")
    questions = [{"content": modified, "answer_markdown": ""}]
    report = restore_visible_math(questions, locks)
    assert questions[0]["content"] == r"求 $x^2+1$ 的最小值。"
    assert report["math_locks_overwritten"] == 1


@pytest.mark.parametrize("content", ["公式被删除", "[[{id}]] 和 [[{id}]]"])
def test_visible_math_lock_rejects_missing_or_duplicate_ids(content):
    _locked, locks = lock_visible_math(r"计算 $1+1$。", "task3")
    questions = [{"content": content.format(id=locks[0].lock_id), "answer_markdown": ""}]
    with pytest.raises(ContentLockIntegrityError):
        restore_visible_math(questions, locks)


def test_docx_task_restores_formula_before_returning_questions():
    from main import DOCUMENT_TASKS, DOCX_DECOMPOSE_STEPS, run_docx_parsing_task

    task_id = "word-lock-integration"
    extracted = {
        "success": True,
        "markdown": r"1. 已知 $f(x)=x^2+1$，求最小值。",
        "image_count": 0,
        "image_paths": [],
        "diagnostics": {
            "omml_converted": 1,
            "mtef_converted": 0,
            "review_required": 0,
        },
    }

    def fake_parse(locked_text, _generate_answers, separated_mode=None, progress_callback=None, **kwargs):
        assert '<mathbank-math id="' in locked_text
        assert r"$f(x)=x^2+1$" in locked_text
        lock_id = re.search(r'id="(M\d+)"', locked_text).group(1)
        return [{
            "content": f"1. 已知 [[{lock_id}]]，求最小值。",
            "answer_markdown": "",
            "question_type": "detailed_answer",
            "referenced_images": [],
        }]

    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
    DOCUMENT_TASKS.init_steps(task_id, DOCX_DECOMPOSE_STEPS)
    with patch("main.extract_docx_markdown", return_value=extracted):
        with patch("main.parse_paper_text_internal", side_effect=fake_parse):
            run_docx_parsing_task(task_id, b"fake-docx", "公式锁定测试.docx")

    task = DOCUMENT_TASKS.snapshot(task_id)
    DOCUMENT_TASKS.remove(task_id)
    assert task["status"] == "completed"
    # post_process_pdf_parsed_questions 会剥离题号前缀（"1. "），故最终内容无前缀
    assert task["data"][0]["content"] == r"已知 $f(x)=x^2+1$，求最小值。"
    assert task["diagnostics"]["math_locks_restored"] == 1


def test_docx_task_progress_callback_fires_during_ai_split():
    """端到端：run_docx_parsing_task 在 ai_split 阶段每完成一段就调一次
    progress_callback，并把 progress/log 写回 DOCUMENT_TASKS。

    这是前端「静默超时」判定的依据——若该链路不通，长卷拆解仍会被误判超时。
    """
    from main import DOCUMENT_TASKS, DOCX_DECOMPOSE_STEPS, run_docx_parsing_task

    task_id = "word-progress-integration"
    extracted = {
        "success": True,
        "markdown": r"1. 已知 $f(x)=x^2+1$，求最小值。",
        "image_count": 0,
        "image_paths": [],
        "diagnostics": {
            "omml_converted": 1,
            "mtef_converted": 0,
            "review_required": 0,
        },
    }

    progress_calls = []

    def fake_parse(locked_text, _generate_answers, separated_mode=None, progress_callback=None, **kwargs):
        # 模拟 LLM 解析 3 段：每段完成都通过 progress_callback 回报。
        # 注意：parse_paper_text_internal 内部把"分块"包了一程，
        # 但我们这里直接 stub 整个 parse，模拟它跑了 3 段。
        for i in range(1, 4):
            if progress_callback:
                progress_callback(i, 3)
                progress_calls.append((i, 3))
        # 返回 3 道题
        return [
            {"content": f"题{i}：求导数。", "answer_markdown": "", "question_type": "detailed_answer", "referenced_images": []}
            for i in range(1, 4)
        ]

    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
    # run_docx_parsing_task 内部调 step_start，需要先注册 steps
    DOCUMENT_TASKS.init_steps(task_id, DOCX_DECOMPOSE_STEPS)

    with patch("main.extract_docx_markdown", return_value=extracted), \
         patch("main.parse_paper_text_internal", side_effect=fake_parse), \
         patch("main._find_soffice", return_value=None):
        run_docx_parsing_task(task_id, b"fake-docx", "progress-test.docx")

    task = DOCUMENT_TASKS.snapshot(task_id)
    DOCUMENT_TASKS.remove(task_id)

    assert task["status"] == "completed", f"任务未成功完成：{task.get('error', task.get('status'))}"
    # 进度回调应被调用 3 次（每段一次）
    assert progress_calls == [(1, 3), (2, 3), (3, 3)]
    # 进度数字最终应是 100
    assert task["progress"] == 100
    # 步骤链应当完整
    step_keys = [s.get("key") for s in (task.get("steps") or [])]
    assert "extract_docx" in step_keys
    assert "ai_split" in step_keys
    assert "post_process" in step_keys
    # 最终 log 应是「完成！」
    assert "完成" in task.get("log", "")


def test_find_source_page_by_overlap_returns_minus_one_on_no_match():
    """无文本重合时应返回 -1（哨兵），以便调用方区分“无匹配”与“命中第 0 页”。"""
    from main import find_source_page_by_overlap

    assert find_source_page_by_overlap("", ["第1页内容"]) == -1
    assert find_source_page_by_overlap("题目文本", []) == -1
    assert find_source_page_by_overlap("完全不相关xyz", ["苹果香蕉橘子"]) == -1


def test_assign_source_pages_by_overlap_maps_each_question_to_page():
    """assign_source_pages_by_overlap 应基于 3-shingle 重合度把每题定位到对应页（0-based）。"""
    from main import assign_source_pages_by_overlap

    page_texts = [
        "第一页专属内容：求导数题与切线",
        "第二页专属内容：求积分题与面积",
    ]
    questions = [
        {"content": "求导数题", "answer_markdown": ""},
        {"content": "求积分题", "answer_markdown": ""},
        {"content": "孤立无匹配文本zzz", "answer_markdown": ""},
    ]
    assign_source_pages_by_overlap(questions, page_texts)

    assert questions[0]["source_page"] == 0  # 求导题 -> 第1页
    assert questions[1]["source_page"] == 1  # 求积分题 -> 第2页
    # 无匹配的题目不写 source_page
    assert "source_page" not in questions[2]


def test_docx_task_writes_source_page_for_each_question():
    """方案C 集成：Word 链路转 PDF 后按文本重合度把每题写 source_page，供手动截图跳转。"""
    from main import DOCUMENT_TASKS, DOCX_DECOMPOSE_STEPS, run_docx_parsing_task, TMP_UPLOAD_DIR
    import fitz
    from pathlib import Path

    task_id = "word-source-page-integration"
    # 预置一个 2 页 PDF（与真实 LibreOffice 转换产物同路径），两页正文不同。
    # 注：用 ASCII 文本，因为默认字体的 insert_text 无法嵌入中文字形、
    # get_text() 只能取回占位点；真实链路走 LibreOffice 转 PDF，文本可正常提取。
    pdf_path = Path(TMP_UPLOAD_DIR) / f"{task_id}.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    doc[0].insert_text((72, 72), "DERIVATIVE PAGE ONE content derivative problem")
    doc[1].insert_text((72, 72), "INTEGRAL PAGE TWO content integral problem")
    pdf_path.write_bytes(doc.tobytes())
    doc.close()

    extracted = {
        "success": True,
        "markdown": "1. derivative. 2. integral.",
        "image_count": 0,
        "image_paths": [],
        "diagnostics": {"omml_converted": 0, "mtef_converted": 0, "review_required": 0},
    }

    def fake_parse(locked_text, _ga, separated_mode=None, progress_callback=None, **kwargs):
        return [
            {"content": "derivative problem", "answer_markdown": "", "question_type": "detailed_answer", "referenced_images": []},
            {"content": "integral problem", "answer_markdown": "", "question_type": "detailed_answer", "referenced_images": []},
        ]

    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
    DOCUMENT_TASKS.init_steps(task_id, DOCX_DECOMPOSE_STEPS)

    import subprocess as _real_subprocess
    from unittest.mock import MagicMock

    _orig_run = _real_subprocess.run  # 保存未被 patch 的原始实现，避免递归

    def _fake_run(*args, **kwargs):
        # 仅拦截 LibreOffice 转换调用（不实际启动 soffice）；
        # 其余 subprocess 调用（含沙箱护栏自检）走原始实现，避免护栏误判。
        cmd = args[0] if args else []
        if isinstance(cmd, (list, tuple)) and cmd and "soffice" in str(cmd[0]):
            return MagicMock(returncode=0, stdout="", stderr="")
        return _orig_run(*args, **kwargs)

    with patch("main.extract_docx_markdown", return_value=extracted), \
         patch("main.parse_paper_text_internal", side_effect=fake_parse), \
         patch("main._find_soffice", return_value="/usr/bin/soffice"), \
         patch("subprocess.run", side_effect=_fake_run):
        run_docx_parsing_task(task_id, b"fake-docx", "source-page-test.docx")

    task = DOCUMENT_TASKS.snapshot(task_id)
    DOCUMENT_TASKS.remove(task_id)
    if pdf_path.exists():
        pdf_path.unlink(missing_ok=True)

    assert task["status"] == "completed", task.get("error")
    data = task["data"]
    assert len(task["page_images"]) == 2
    assert data[0]["source_page"] == 0  # derivative problem -> 第1页
    assert data[1]["source_page"] == 1  # integral problem -> 第2页


def test_pdf_task_writes_source_page_per_page():
    """方案B 集成：PDF 链路按 ocr_results 把每题写 source_page（与 page_images 顺序对齐）。"""
    from main import DOCUMENT_TASKS, run_pdf_parsing_task
    import fitz

    doc = fitz.open()
    doc.new_page(width=595, height=842).insert_text((72, 72), "第一页微积分求导题")
    doc.new_page(width=595, height=842).insert_text((72, 72), "第二页积分应用大题")
    pdf_bytes = doc.tobytes()
    doc.close()

    def fake_ocr(image_path):
        # force_ocr 下按文件名末位的页码索引返回对应页 OCR 文本（与线程调度顺序无关）
        m = re.search(r"_(\d+)\.png$", image_path)
        page_num = int(m.group(1)) if m else 0
        return ["第一页微积分求导题", "第二页积分应用大题"][page_num]

    def fake_parse(text, _ga, separated_mode=None, progress_callback=None, **kwargs):
        return [
            {"content": "求导题", "answer_markdown": "", "question_type": "detailed_answer", "referenced_images": []},
            {"content": "积分应用大题", "answer_markdown": "", "question_type": "detailed_answer", "referenced_images": []},
        ]

    task_id = "pdf-source-page-integration"
    if DOCUMENT_TASKS.exists(task_id):
        DOCUMENT_TASKS.remove(task_id)
    DOCUMENT_TASKS.create(task_id, document_type="pdf", temp_assets=[])

    with patch("main.ocr_pdf_page_image", side_effect=fake_ocr), \
         patch("main.parse_paper_text_internal", side_effect=fake_parse):
        run_pdf_parsing_task(
            task_id, pdf_bytes, "source-page-test.pdf",
            page_range=None, pdf_strategy="force_ocr",
        )

    task = DOCUMENT_TASKS.snapshot(task_id)
    DOCUMENT_TASKS.remove(task_id)

    assert task["status"] == "completed", task.get("error")
    data = task["data"]
    assert data[0]["source_page"] == 0  # 求导题 -> 第1页
    assert data[1]["source_page"] == 1  # 积分应用大题 -> 第2页