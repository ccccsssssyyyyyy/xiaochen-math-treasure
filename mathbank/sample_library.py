"""内置示例题库：首次启动即可试用的最小可用数据集。

设计要点
--------
* 数据文件：``mathbank/resources/sample_questions.json``（随包分发，无题目版权风险，全部为自编基础题）。
* 幂等：以 ``source == marker_source``（``教辅 · 内置示例题目``）作为"已导入"标记，
  默认情况下只要库内存在该来源的题目就整体跳过，重复点击不会重复入库。
* 归一：与正式入库走同一套函数（``normalize_choice_options_to_latex`` / ``normalize_source`` /
  ``normalize_tag_list`` / ``normalize_question_content``），避免示例数据成为"绕过规约"的旁门。
* 该模块不导入 ``main``：可脱离 FastAPI 应用单独测试，也不需要读取 ``.env``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from mathbank.curriculums import normalize_tag_list
from mathbank.database import Question, QuestionCurriculum, normalize_question_content
from mathbank.latex_normalize import normalize_choice_options_to_latex
from mathbank.source_normalize import normalize_source

SAMPLE_RESOURCE_PATH = Path(__file__).resolve().parent / "resources" / "sample_questions.json"
DEFAULT_MARKER_SOURCE = "教辅 · 内置示例题目"

_REQUIRED_FIELDS = ("content", "question_type")


class SampleLibraryError(RuntimeError):
    """示例题库文件缺失或结构不合法。"""


def load_sample_library(path: Path | str | None = None) -> dict:
    """读取并校验示例题库文件，返回 ``{"marker_source": str, "questions": list[dict]}``。"""

    target = Path(path) if path is not None else SAMPLE_RESOURCE_PATH
    if not target.exists():
        raise SampleLibraryError(f"未找到内置示例题库文件：{target}")

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SampleLibraryError(f"内置示例题库文件无法解析：{exc}") from exc

    if not isinstance(payload, dict):
        raise SampleLibraryError("内置示例题库文件根节点必须是对象")

    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise SampleLibraryError("内置示例题库文件缺少非空的 questions 数组")

    for index, item in enumerate(questions):
        if not isinstance(item, dict):
            raise SampleLibraryError(f"第 {index + 1} 条示例题目不是对象")
        missing = [field for field in _REQUIRED_FIELDS if not str(item.get(field) or "").strip()]
        if missing:
            raise SampleLibraryError(f"第 {index + 1} 条示例题目缺少字段：{', '.join(missing)}")

    marker = str(payload.get("marker_source") or DEFAULT_MARKER_SOURCE).strip()
    return {"marker_source": marker, "questions": questions}


def sample_question_count() -> int:
    """返回内置示例题目的条数（文件异常时返回 0，供 UI 展示用，不抛错）。"""

    try:
        return len(load_sample_library()["questions"])
    except SampleLibraryError:
        return 0


def _existing_imported_count(db, marker_source: str) -> int:
    return (
        db.query(Question)
        .filter(Question.source == marker_source)
        .count()
    )


def import_sample_questions(
    db,
    active_version_code: str,
    *,
    extra_content_normalizer: Callable[[str], str] | None = None,
    force: bool = False,
    library_path: Path | str | None = None,
) -> dict:
    """把内置示例题目写入题库。

    参数
    ----
    db
        SQLAlchemy Session。
    active_version_code
        当前教材版本代号（写入 ``question_curriculums`` 镜像表用）。
    extra_content_normalizer
        可选：调用方补充的题干归一（如 main.py 的 ``normalize_fillin_macro`` /
        ``_strip_leading_question_number``），保证示例数据与正式入库同管线。
    force
        为 True 时忽略"已导入"标记，强制再写一遍（默认 False，保证幂等）。
    """

    library = load_sample_library(library_path)
    marker = library["marker_source"]
    items = library["questions"]

    existing = _existing_imported_count(db, marker)
    if existing and not force:
        return {
            "status": "skipped",
            "imported": 0,
            "existing": existing,
            "total": len(items),
            "source": marker,
            "message": f"示例题目已存在（{existing} 道），无需重复导入。",
        }

    created_ids: list[int] = []
    for item in items:
        content = str(item.get("content") or "")
        content = normalize_choice_options_to_latex(content)
        if extra_content_normalizer is not None:
            content = extra_content_normalizer(content)

        question_type = str(item.get("question_type") or "detailed_answer")
        compulsory = str(item.get("category_compulsory") or "")
        chapter = str(item.get("category_chapter") or "")
        knowledge = str(item.get("category_knowledge") or "") or chapter

        knowledge_list = normalize_tag_list(item.get("knowledge_list"), field="knowledge_list")
        if not knowledge_list and knowledge:
            knowledge_list = normalize_tag_list(knowledge, field="knowledge_list")

        record = Question(
            content=content,
            content_fingerprint=normalize_question_content(content),
            question_type=question_type,
            category_compulsory=compulsory,
            category_chapter=chapter,
            category_knowledge=knowledge,
            difficulty=str(item.get("difficulty") or "normal"),
            source=normalize_source(marker),
            answer_markdown=str(item.get("answer_markdown") or ""),
            review=str(item.get("review") or ""),
            tags=str(item.get("tags") or ""),
            knowledge_list=knowledge_list,
            solve_method=normalize_tag_list(item.get("solve_method"), field="solve_method"),
            related_curriculums="[]",
        )
        db.add(record)
        db.flush()

        db.add(
            QuestionCurriculum(
                question_id=record.id,
                version_code=active_version_code,
                compulsory=compulsory,
                chapter=chapter,
                knowledge=knowledge,
            )
        )
        created_ids.append(record.id)

    db.commit()

    return {
        "status": "success",
        "imported": len(created_ids),
        "existing": existing,
        "total": len(items),
        "question_ids": created_ids,
        "source": marker,
        "message": f"已载入 {len(created_ids)} 道示例题目，可直接去「组卷工作台」体验排版与导出。",
    }


def iter_sample_titles(library_path: Path | str | None = None) -> Iterable[str]:
    """返回示例题目题型列表，便于前端展示预览（不触碰数据库）。"""

    for item in load_sample_library(library_path)["questions"]:
        yield str(item.get("question_type") or "")
