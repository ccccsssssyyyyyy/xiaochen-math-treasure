"""内置示例题库（首次启动引导的数据来源）回归测试。

覆盖两层：
1. `mathbank.sample_library` 的纯逻辑（可脱离 FastAPI 单独跑）；
2. 三个新端点的行为（/api/sample-questions、/api/sample-questions/import、
   /api/environment），走 `client` fixture 与内存库。
"""

import json
from pathlib import Path

import pytest

from mathbank.sample_library import (
    DEFAULT_MARKER_SOURCE,
    SAMPLE_RESOURCE_PATH,
    SampleLibraryError,
    import_sample_questions,
    load_sample_library,
    sample_question_count,
)


# ----------------------------------------------------------------------
# 1. 纯逻辑
# ----------------------------------------------------------------------


def test_shipped_sample_library_is_valid():
    library = load_sample_library()
    assert library["marker_source"] == DEFAULT_MARKER_SOURCE
    assert len(library["questions"]) >= 6
    for item in library["questions"]:
        assert item["content"].strip()
        assert item["question_type"] in {
            "single_choice",
            "multi_choice",
            "fill_in_blank",
            "detailed_answer",
        }
        assert item.get("answer_markdown", "").strip(), "示例题必须自带解析，否则导出答案页是空的"


def test_choice_samples_use_canonical_choices_environment():
    """选择题必须用 \\begin{choices} 且 \\item 内不得再写 A./B. 显式标号。"""
    for item in load_sample_library()["questions"]:
        if item["question_type"] not in {"single_choice", "multi_choice"}:
            continue
        content = item["content"]
        assert "\\begin{choices}" in content and "\\end{choices}" in content
        for line in content.splitlines():
            if line.strip().startswith("\\item"):
                body = line.strip()[len("\\item"):].strip()
                assert not body[:3].lower() in {"a.", "b.", "c.", "d."}, body


def test_sample_library_reports_missing_file(tmp_path):
    with pytest.raises(SampleLibraryError):
        load_sample_library(tmp_path / "not-there.json")


def test_sample_library_reports_broken_json(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(SampleLibraryError):
        load_sample_library(broken)


def test_sample_library_rejects_empty_and_incomplete_entries(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"questions": []}), encoding="utf-8")
    with pytest.raises(SampleLibraryError):
        load_sample_library(empty)

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(
        json.dumps({"questions": [{"content": "题干", "question_type": ""}]}), encoding="utf-8"
    )
    with pytest.raises(SampleLibraryError):
        load_sample_library(incomplete)


def test_sample_question_count_degrades_to_zero(monkeypatch):
    monkeypatch.setattr(
        "mathbank.sample_library.SAMPLE_RESOURCE_PATH", Path("/definitely/not/here.json")
    )
    assert sample_question_count() == 0


def test_import_sample_questions_is_idempotent(db_session):
    first = import_sample_questions(db_session, "A")
    assert first["status"] == "success"
    assert first["imported"] == first["total"] == len(load_sample_library()["questions"])

    second = import_sample_questions(db_session, "A")
    assert second["status"] == "skipped"
    assert second["imported"] == 0
    assert second["existing"] == first["imported"]


def test_import_sample_questions_normalizes_and_mirrors(db_session):
    from mathbank.database import Question, QuestionCurriculum

    result = import_sample_questions(db_session, "A", extra_content_normalizer=lambda t: t)
    rows = db_session.query(Question).all()
    assert len(rows) == result["imported"]

    for row in rows:
        # 来源必须落在受控词表的「教辅 · {名称}」上，否则会污染来源统计
        assert row.source == DEFAULT_MARKER_SOURCE
        assert row.content_fingerprint, "指纹缺失会让查重失效"
        assert row.difficulty in {"easy_error", "normal", "challenge", "qiangji"}
        assert row.category_compulsory and row.category_chapter

    mirrors = db_session.query(QuestionCurriculum).all()
    assert len(mirrors) == len(rows)
    assert all(m.version_code == "A" for m in mirrors)


def test_import_sample_questions_applies_extra_normalizer(db_session):
    calls = []

    def normalizer(text):
        calls.append(text)
        return text.replace("计算：", "计算（已归一）：")

    import_sample_questions(db_session, "A", extra_content_normalizer=normalizer)
    assert calls, "调用方传入的补充归一必须被逐题执行"
    from mathbank.database import Question

    assert any("计算（已归一）" in q.content for q in db_session.query(Question).all())


def test_import_sample_questions_force_writes_again(db_session):
    import_sample_questions(db_session, "A")
    forced = import_sample_questions(db_session, "A", force=True)
    assert forced["status"] == "success"
    assert forced["imported"] == forced["total"]
    assert forced["existing"] == forced["total"]


def test_shipped_resource_path_points_at_packaged_file():
    assert SAMPLE_RESOURCE_PATH.is_file()
    assert SAMPLE_RESOURCE_PATH.parent.name == "resources"


# ----------------------------------------------------------------------
# 2. 端点
# ----------------------------------------------------------------------


def test_sample_questions_info_endpoint(client):
    response = client.get("/api/sample-questions")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["available"] >= 6
    assert data["imported"] == 0
    assert data["source"] == DEFAULT_MARKER_SOURCE


def test_sample_questions_import_endpoint_is_token_protected(client):
    """写库端点必须受本地令牌保护，否则任意网页都能往题库里塞数据。"""
    response = client.post("/api/sample-questions/import")
    assert response.status_code == 403


def test_sample_questions_import_endpoint_writes_questions(client):
    from main import LOCAL_TOKEN

    headers = {"X-Local-Token": LOCAL_TOKEN}
    response = client.post("/api/sample-questions/import", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["imported"] >= 6

    listing = client.get("/api/questions").json()
    assert len(listing) == data["imported"]

    # 第二次调用应当幂等跳过
    again = client.post("/api/sample-questions/import", headers=headers).json()
    assert again["status"] == "skipped"
    assert len(client.get("/api/questions").json()) == data["imported"]


def test_environment_endpoint_shape(client):
    response = client.get("/api/environment")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["platform"]
    for key in ("latex", "libreoffice", "pandoc", "pdf_inspector"):
        assert key in data
        assert isinstance(data[key]["available"], bool)
    # latex 多了 engine/path 两个字段，前端据此展示"已就绪 · xelatex"
    assert "engine" in data["latex"]
