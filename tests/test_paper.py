import os
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from main import LOCAL_TOKEN
from mathbank.database import Paper, PaperQuestion, Question

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VIRTUAL_CHECK_JS = Path(__file__).resolve().parent / "js" / "paper_virtual_adaptive_check.js"
PAPER_JS = PROJECT_ROOT / "static" / "js" / "paper.js"
#: 断言条数下限 —— 夹具被误删/掏空时这里先红，不必等某项断言失效。
VIRTUAL_CHECK_MIN_ITEMS = 30

def test_paper_api_flow(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    
    # 1. Create a question
    q_data = {
        "content": "已知函数 $f(x) = x^2 + 2x + 1$，求 $f(1)$ 的值。\n\\begin{choices}\n\\item 1\n\\item 2\n\\item 4\n\\item 8\n\\end{choices}",
        "question_type": "single_choice",
        "category_compulsory": "必修第一册",
        "category_chapter": "第一章 集合与常用逻辑用语",
        "category_knowledge": "集合的概念",
        "difficulty": "easy",
        "source": "2026月考",
        "answer_markdown": "解：$f(1) = 1^2 + 2(1) + 1 = 4$。故选 C。",
        "review": "易错点：计算细节",
        "tags": "函数,计算",
        "related_question_id": "",
        "image_paths": "[]"
    }
    create_res = client.post("/api/questions", data=q_data, headers=headers)
    assert create_res.status_code == 200
    q_id = create_res.json()["question"]["id"]
    
    # 2. Batch fetch paper questions
    fetch_res = client.get(f"/api/paper/questions?ids={q_id}")
    assert fetch_res.status_code == 200
    assert len(fetch_res.json()["data"]) == 1
    assert fetch_res.json()["data"][0]["id"] == q_id
    
    # 3. Save Paper & verify usage_count increment
    paper_payload = {
        "title": "测试高中数学单元测试卷",
        "subtitle": "满分150分",
        "paper_type": "exam",
        "questions": [{"id": q_id, "score": 5, "order": 1}]
    }
    save_res = client.post("/api/paper/save", json=paper_payload, headers=headers)
    print("SAVE_RES:", save_res.json())
    assert save_res.status_code == 200
    assert save_res.json()["status"] == "success"
    
    # Check question usage count
    get_q_res = client.get(f"/api/questions/{q_id}")
    assert get_q_res.status_code == 200
    assert get_q_res.json()["usage_count"] == 1

    # 4. Export LaTeX TEX Zip
    tex_res = client.post("/api/paper/export/tex", json=paper_payload, headers=headers)
    assert tex_res.status_code == 200
    assert tex_res.headers.get("content-type") == "application/zip"

    # 4b. Export Full Bundle Zip (TeX + PDF)
    # 该端点在服务端真实调用 xelatex 编译 PDF。未安装排版工具链（或宏包不全）时，
    # 端点按设计**优雅降级**为结构化提示而非 500（见 main.py 的 export_paper_bundle）。
    # 两种结果都做断言：有工具链必须是 zip，没有则必须是 warning 提示。
    bundle_res = client.post("/api/paper/export/bundle", json=paper_payload, headers=headers)
    assert bundle_res.status_code == 200
    if bundle_res.headers.get("content-type") != "application/zip":
        body = bundle_res.json()
        assert body.get("status") == "warning", f"缺少排版工具链时应优雅降级为 warning，实际: {body}"
        assert "xelatex" in body.get("message", "")

    # 5. AI Paper Selection
    ai_res = client.post("/api/paper/ai-select", json={"prompt": "函数", "limit": 5}, headers=headers)
    assert ai_res.status_code == 200
    assert ai_res.json()["status"] == "success"
    assert len(ai_res.json()["data"]) >= 1

    # 6. Saved Papers List & Detail & Delete
    list_res = client.get("/api/papers")
    assert list_res.status_code == 200
    papers_list = list_res.json()["data"]
    assert len(papers_list) >= 1
    paper_id = papers_list[0]["id"]

    detail_res = client.get(f"/api/papers/{paper_id}")
    assert detail_res.status_code == 200
    assert detail_res.json()["data"]["title"] == "测试高中数学单元测试卷"
    assert len(detail_res.json()["data"]["questions"]) == 1

    del_res = client.delete(f"/api/papers/{paper_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"


def test_ai_paper_selection_uses_shared_provider_and_clean_effort(
    client, db_session
):
    question = Question(
        content="求函数的导数",
        question_type="detailed_answer",
        difficulty="medium",
    )
    excluded_question = Question(
        content="不应越过类型筛选的选择题",
        question_type="single_choice",
        difficulty="medium",
    )
    db_session.add_all([question, excluded_question])
    db_session.commit()
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "selected_ids": [
                                excluded_question.id,
                                question.id,
                                question.id,
                            ],
                            "ai_analysis": "选取一道导数题",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
    provider_env = {
        "PREFER_SOLVE_MODEL": "ZHONGZHAN_GPT/gpt-5.6-sol:high",
        "ZHONGZHAN_GPT_API_KEY": "transit-key",
        "ZHONGZHAN_GPT_BASE_URL": (
            "https://transit.example/v1/chat/completions/"
        ),
    }

    with patch.dict(os.environ, provider_env, clear=False), patch(
        "mathbank.ai_http.robust_request_post", return_value=response
    ) as mock_post:
        result = client.post(
            "/api/paper/ai-select",
            json={
                "prompt": "请选 1 道函数题",
                "question_type": "detailed_answer",
                "limit": 1,
            },
            headers={"X-Local-Token": LOCAL_TOKEN},
        )

    assert result.status_code == 200
    assert result.json()["fallback"] is False
    assert [item["id"] for item in result.json()["data"]] == [question.id]
    args, kwargs = mock_post.call_args
    assert args[0] == "https://transit.example/v1/chat/completions"
    assert kwargs["json"]["model"] == "gpt-5.6-sol"
    assert kwargs["json"]["reasoning_effort"] == "high"
    assert kwargs["json"]["enable_thinking"] is True


@pytest.mark.parametrize(
    "questions",
    [
        [{"id": 999999, "score": 5}],
        [{"id": 1, "score": 5}, {"id": 1, "score": 5}],
        [{"id": 1, "score": -1}],
    ],
)
def test_invalid_paper_items_are_rejected_without_partial_rows(
    client, db_session, questions
):
    question = Question(content="组卷事务题", question_type="single_choice")
    db_session.add(question)
    db_session.commit()
    # Parameter values use the first SQLite ID in this isolated fixture.
    for item in questions:
        if item["id"] == 1:
            item["id"] = question.id

    result = client.post(
        "/api/paper/save",
        json={"title": "无效试卷", "paper_type": "exam", "questions": questions},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )

    assert result.status_code == 400
    assert db_session.query(Paper).count() == 0
    assert db_session.query(PaperQuestion).count() == 0
    db_session.refresh(question)
    assert question.usage_count in {None, 0}

def test_exam_19_and_answer_sheet_generation(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    
    from mathbank.paper_helper import build_answer_sheet_latex
    
    questions_data = [
        {
            "question": {
                "id": 101,
                "question_type": "detailed_answer",
                "content": "求证：$\\sin^2 x + \\cos^2 x = 1$。\n\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}"
            },
            "score": 13
        }
    ]
    
    sheet_tex = build_answer_sheet_latex("2026年模拟考试试卷", "", questions_data)
    assert "\\documentclass" in sheet_tex
    assert "2026年模拟考试试卷" in sheet_tex
    assert "15.（13分）" in sheet_tex
    assert "\\begin{tikzpicture}" in sheet_tex
    
    # Test export endpoints for exam_19
    paper_payload = {
        "title": "2026年高考模拟试卷",
        "subtitle": "数学",
        "paper_type": "exam_19",
        "questions": []
    }
    
    export_res = client.post("/api/paper/export/tex", json=paper_payload, headers=headers)
    assert export_res.status_code == 200
    assert export_res.headers.get("content-type") == "application/zip"

    pdf_sheet_payload = {
        "title": "2026年高考模拟试卷",
        "subtitle": "数学",
        "paper_type": "exam_19",
        "target": "sheet",
        "questions": []
    }
    # The endpoint contract should not depend on XeLaTeX being installed on
    # the CI runner; compilation behavior has separate focused tests.
    with patch(
        "main.compile_tex_to_pdf",
        return_value=(b"%PDF-1.7\n% deterministic test payload", ""),
    ):
        sheet_pdf_res = client.post(
            "/api/paper/export/pdf", json=pdf_sheet_payload, headers=headers
        )
    assert sheet_pdf_res.status_code == 200
    assert sheet_pdf_res.headers.get("content-type") == "application/pdf"

def test_solution_space_latex_generation():
    from mathbank.paper_helper import build_latex_document
    questions_data_65 = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "6.5"
    }]
    questions_data_zero = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "0.0"
    }]
    
    # 1. Non-exam_19 paper mode -> should inject \vspace*{6.5cm}
    tex = build_latex_document("单元测试", "试卷", "exam", questions_data_65, include_answers=False)
    assert r"\vspace*{6.5cm}" in tex
    
    # 2. exam_19 paper mode with 3.0cm solution space -> should inject \vspace*{3.0cm}
    questions_data_3 = [{
        "question": {
            "id": 99,
            "question_type": "detailed_answer",
            "content": "已知函数 $f(x) = ax^2 + bx + c$，求导数 $f'(x)$。",
            "figure_align": "right"
        },
        "score": 12,
        "solution_space": "3.0"
    }]
    tex_19_three = build_latex_document("高考模拟", "试卷", "exam_19", questions_data_3, include_answers=False)
    assert r"\vspace*{3.0cm}" in tex_19_three

    # 3. exam_19 paper mode with 0.0cm solution space -> should not inject \vspace
    tex_19_zero = build_latex_document("高考模拟", "试卷", "exam_19", questions_data_zero, include_answers=False)
    assert r"\vspace*" not in tex_19_zero


def test_paper_virtual_stream_stability_runtime():
    """组卷选题流的虚拟滚动稳定性：真跑 paper.js，断言滚动帧不整块重建视口。

    2026-09-20 修「滚动时画面疯狂上下跳」的回归网。根因是滚动帧里
    ``viewport.innerHTML = html`` 无条件整块重建，紧接着实测回填又改写 scrollTop，
    而改 scrollTop 会再派发 scroll 事件 —— 两者互相触发形成跨帧闭环。

    静态断言拦不住这类回退：读源码看不出它会跳。夹具会真跑一遍并断言
    「窗口未变时卡片元素引用逐个不变」「一次窗口变化只做一次 DOM 同步」
    「程序派发的 scroll 不被当成用户滚动」。

    夹具此前是裸脚本、pytest 从不执行它 —— 这里一并补上包装。
    """
    assert VIRTUAL_CHECK_JS.is_file(), f"夹具缺失: {VIRTUAL_CHECK_JS}"
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(VIRTUAL_CHECK_JS), str(PAPER_JS)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "虚拟滚动稳定性实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # 断言数骤降 = 夹具被掏空，先在这里报警
    match = re.search(r"通过 (\d+) 项", result.stdout)
    assert match, f"夹具没报出通过项数:\n{result.stdout}"
    assert int(match.group(1)) >= VIRTUAL_CHECK_MIN_ITEMS, (
        f"断言数骤降到 {match.group(1)}，夹具可能被掏空"
    )
