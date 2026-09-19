"""入库查重的唯一权威实现。

同一份判重规则此前只内联在 ``main.py`` 的 ``create_question`` 里。错题工作台的
「数学错题批量入库」需要完全相同的判重行为（同一个 0.92 阈值、同一套轻量预筛），
故抽到这里，两处调用同一实现，避免出现「手动入库判为重复、错题入库却放行」
这种难以察觉的不一致。

查重指纹本身由 ``mathbank.database.normalize_question_content`` 生成 —— 那是
另一份唯一权威实现，本模块只负责「拿指纹怎么比」。
"""

from __future__ import annotations

import difflib
from typing import Optional

#: 判定为重复的相似度阈值（与手动入库保持一致）
DUP_THRESHOLD = 0.92


def find_duplicate_question(
    db,
    content: str,
    question_type: str,
    subject: Optional[str] = None,
) -> tuple[Optional[int], float]:
    """在**同学科同题型**题内查找与 ``content`` 高度相似的题。

    返回 ``(existing_question_id, similarity)``；未命中时 id 为 ``None``、
    similarity 为 ``0.0``。

    - 只在同题型内比较：选择题与解答题的题面天然不像，跨题型比较既费时又容易
      产生「填空题被判与解答题重复」这类无意义告警。
    - ``subject`` 非空时把候选集再按学科收窄：不同学科题干文字高度撞车很常见
      （同一个物理情境被改写成化学题、数学题与物理题共用一段文字描述），不隔离
      就会出现「录一道物理题被告知与某道数学题重复」的误判。
    - ``subject`` 留空时不加学科过滤，保持对旧调用方的兼容行为。
    """

    # 延迟导入：database 会连带初始化引擎，模块级导入会让轻量调用方也背上这个成本
    from mathbank.database import Question, normalize_question_content

    fingerprint = normalize_question_content(content or "")
    if not fingerprint:
        return None, 0.0

    # 只取 (id, 预存指纹)，不加载全文；扫描同题型全部候选，不做 limit，
    # 避免题库规模变大后出现「超出扫描窗口即漏查」的静默错误。
    candidate_query = db.query(Question.id, Question.content_fingerprint).filter(
        Question.question_type == question_type
    )
    subject_value = str(subject or "").strip().lower()
    if subject_value:
        candidate_query = candidate_query.filter(Question.subject == subject_value)
    candidates = candidate_query.all()

    best_similarity = 0.0
    best_match_id: Optional[int] = None
    for candidate_id, candidate_fingerprint in candidates:
        if not candidate_fingerprint:
            continue
        # 精确相等短路：指纹完全一致即判重，跳过代价更高的深度比较
        if candidate_fingerprint == fingerprint:
            return candidate_id, 1.0
        # 轻量预筛：长度差超过 50% 不可能达到 0.92 相似度
        if (
            abs(len(candidate_fingerprint) - len(fingerprint)) * 2
            > len(candidate_fingerprint) + len(fingerprint)
        ):
            continue
        similarity = difflib.SequenceMatcher(
            None, candidate_fingerprint, fingerprint
        ).ratio()
        if similarity > best_similarity:
            best_similarity = similarity
            best_match_id = candidate_id

    if best_match_id is not None and best_similarity >= DUP_THRESHOLD:
        return best_match_id, best_similarity
    return None, 0.0


def duplicate_warning_payload(db, existing_id: int, similarity: float) -> dict:
    """把命中结果整理成与 ``create_question`` 同构的返回体，供前端复用同一套提示。"""

    from mathbank.database import Question

    existing = db.query(Question).filter(Question.id == existing_id).first()
    preview = (existing.content or "")[:120] if existing else ""
    return {
        "status": "duplicate_warning",
        "message": f"该题与库中第 {existing_id} 题高度相似（相似度 {similarity:.0%}），疑似重复入库。",
        "existing_question_id": existing_id,
        "similarity": round(similarity, 4),
        "existing_preview": preview,
    }
