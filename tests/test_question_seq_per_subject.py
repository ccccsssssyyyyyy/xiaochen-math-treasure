"""题目落库编号按科目独立（2026-09-19 需求）。

题库卡片上那个 ``#1399`` 不是主键 id，而是服务端算出来的展示编号
（``get_seq_mapping``）：改造前 = 「全库第几道题」，所以物理第一题接着数学的号
往下排（#1380 起）。需求改成按科目各自从 1 起：数学 #1..#N、物理 #1..#M。

这里锁定四件事：
1. 跨科目重置、科内仍按 id 升序取位置；
2. 科目口径与 ``normalize_subject`` 一致（空值 / 未知值归数学），不会凭空多出一门课；
3. 编号是**位置型**展示口径、不落库 —— 删题后该科后续编号前移，与改造前同性质；
4. 接口层真的把新编号吐给前端（单题路由与列表路由各一路）。
"""

from mathbank.database import Question
from main import get_seq_mapping


def _add(db, qid, subject, content="题干"):
    db.add(Question(id=qid, subject=subject, content=content))
    db.flush()


def test_mixed_subject_restarts_numbering(db_session):
    """数学 3 题 + 物理 2 题：物理从 #1 起，不接数学的 #4。"""
    for qid in (1, 2, 3):
        _add(db_session, qid, "math")
    for qid in (100, 101):
        _add(db_session, qid, "physics")
    db_session.commit()

    seq = get_seq_mapping(db_session, [1, 2, 3, 100, 101])
    assert seq == {1: 1, 2: 2, 3: 3, 100: 1, 101: 2}


def test_physics_id_magnitude_does_not_leak_into_number(db_session):
    """物理题 id 从 1000 起也无所谓 —— 编号与 id 数值彻底解耦。"""
    _add(db_session, 1000, "physics")
    _add(db_session, 1001, "physics")
    _add(db_session, 2000, "math")
    db_session.commit()

    assert get_seq_mapping(db_session, [1000, 1001, 2000]) == {1000: 1, 1001: 2, 2000: 1}


def test_single_id_lookup_is_subject_local(db_session):
    """单题查询走的就是 ``[id]`` 这一路（GET /api/questions/{id}）。"""
    _add(db_session, 5, "math")
    _add(db_session, 6, "math")
    _add(db_session, 900, "physics")
    db_session.commit()

    assert get_seq_mapping(db_session, [900]) == {900: 1}
    assert get_seq_mapping(db_session, [6]) == {6: 2}


def test_chemistry_is_its_own_sequence(db_session):
    _add(db_session, 1, "math")
    _add(db_session, 2, "physics")
    _add(db_session, 3, "chemistry")
    _add(db_session, 4, "chemistry")
    db_session.commit()

    assert get_seq_mapping(db_session, [1, 2, 3, 4]) == {1: 1, 2: 1, 3: 1, 4: 2}


def test_unknown_or_empty_subject_falls_into_math(db_session):
    """口径必须与 normalize_subject 一致，否则同一道题在两处属于不同「科目」。"""
    _add(db_session, 1, "math")
    _add(db_session, 2, None)
    _add(db_session, 3, "")
    _add(db_session, 4, "other")
    _add(db_session, 5, "Math")
    db_session.commit()

    seq = get_seq_mapping(db_session, [1, 2, 3, 4, 5])
    assert seq == {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}


def test_full_scan_branch_uses_same_rule(db_session):
    """``question_ids=None`` 的全量分支必须与指定 id 分支同一口径。"""
    for qid, subject in ((1, "math"), (2, "physics"), (3, "math"), (4, "physics")):
        _add(db_session, qid, subject)
    db_session.commit()

    assert get_seq_mapping(db_session) == {1: 1, 3: 2, 2: 1, 4: 2}


def test_empty_id_set_returns_empty_mapping(db_session):
    _add(db_session, 1, "math")
    db_session.commit()

    assert get_seq_mapping(db_session, []) == {}
    assert get_seq_mapping(db_session, None) == {1: 1}


def test_numbering_is_positional_after_delete(db_session):
    """位置型口径：删掉中间的题，该科后面的编号前移（与改造前同性质，非回归）。"""
    for qid in (1, 2, 3):
        _add(db_session, qid, "math")
    _add(db_session, 10, "physics")
    db_session.commit()

    db_session.query(Question).filter(Question.id == 2).delete()
    db_session.commit()

    assert get_seq_mapping(db_session, [1, 3, 10]) == {1: 1, 3: 2, 10: 1}


def test_question_api_exposes_subject_local_seq(client, db_session):
    """接口层回归：物理题通过 HTTP 拿到的就是本科目编号。"""
    _add(db_session, 1, "math")
    _add(db_session, 7, "physics")
    db_session.commit()

    resp = client.get("/api/questions/7")
    assert resp.status_code == 200
    assert resp.json()["seq_num"] == 1


def test_question_list_api_exposes_subject_local_seq(client, db_session):
    """列表路由（题库工作台首屏走它）同样按科内编号。"""
    _add(db_session, 1, "math")
    _add(db_session, 2, "math")
    _add(db_session, 3, "physics")
    db_session.commit()

    resp = client.get("/api/questions")
    assert resp.status_code == 200
    payload = resp.json()
    items = payload["questions"] if isinstance(payload, dict) else payload
    seq_by_id = {item["id"]: item["seq_num"] for item in items}
    assert seq_by_id[3] == 1
    assert seq_by_id[2] == 2
