"""错题重做闭环的接口级回归（真实路由 + 真实 DB 会话）。

覆盖三件容易「看起来对了、实际错了」的事：

1. **入库即入池**：错题入库那一刻要种下第一笔「错」（``wrong_count = 1``）、
   把建议重做日排到最近的周六，并顺手抽出选择题的正确答案字母。
2. **改判不是加做一遍**：掌握度按 ``redo_attempts`` 历史**重放**推导，所以同一题
   先点错、再改点对，账要退回去 —— 如果这里退了不该退的、或者没退该退的，
   学生复习时看到的错次就是假的。这条是本次实现的判据。
3. **导出即变换、库内不动**：选项按遍数变换只发生在导出与录入视图上。

用真实端点而不是直接调函数，是因为「入库 → 入池 → 出卷 → 录入 → 回写」这条链路
横跨 5 个入口，函数级测试证明不了它们串起来还对。
"""

import datetime
import io
import zipfile

import pytest

from mathbank import redo_schedule
from mathbank.database import (
    MistakeBatch,
    MistakeRecord,
    Paper,
    PaperQuestion,
    Question,
    RedoAttempt,
    Student,
)

MCQ_CONTENT = (
    "已知 $f(x)=x^2-2x$，则 $f(3)=$（　　）\n"
    "\\begin{choices}\n\\item 3\n\\item 6\n\\item 9\n\\item 0\n\\end{choices}"
)
MCQ_ANSWER = "由 $f(3)=9-6=3$，故选 A."


def _auth():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def _make_record(db_session, **overrides):
    """建一条「已识别、已点选为错」的错题记录（入库端点的输入）。"""

    student = db_session.query(Student).first()
    if student is None:
        student = Student(name="测试同学", grade="高一")
        db_session.add(student)
        db_session.flush()
    batch = MistakeBatch(
        student_id=student.id,
        subject="math",
        title=overrides.pop("batch_title", "重做闭环测试批次"),
    )
    db_session.add(batch)
    db_session.flush()
    record = MistakeRecord(
        batch_id=batch.id,
        student_id=student.id,
        subject="math",
        page_no=1,
        block_index=0,
        question_no=overrides.pop("question_no", "7"),
        content=overrides.pop("content", MCQ_CONTENT),
        question_type=overrides.pop("question_type", "single_choice"),
        answer_markdown=overrides.pop("answer_markdown", MCQ_ANSWER),
        category_compulsory="必修一",
        category_chapter="3. 函数的概念与性质",
        category_knowledge="3.1 函数的概念",
        difficulty="中",
        source="E2E-redo",
        grad_status=overrides.pop("grad_status", "incorrect"),
        recognize_status="done",
        include_in_handout=True,
        **overrides,
    )
    db_session.add(record)
    db_session.commit()
    return record


def _import_to_bank(client, db_session, record):
    resp = client.post(
        f"/api/mistakes/records/{record.id}/import-to-bank",
        json={},
        headers=_auth(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["question_id"]


def _seed_pooled_question(client, db_session, **overrides):
    record = _make_record(db_session, **overrides)
    return _import_to_bank(client, db_session, record), record


# ---------------------------------------------------------------- 1. 入库即入池


def test_import_seeds_pool_and_extracts_correct_answer(client, db_session):
    question_id, record = _seed_pooled_question(client, db_session)
    question = db_session.query(Question).filter(Question.id == question_id).one()

    assert question.correct_answer == "A", "选择题的正确定盘字母必须在入库时抽出"
    assert question.wrong_count == 1, "成为错题的那一刻就该记 1 次错"
    assert question.mastery_status == redo_schedule.PENDING
    assert question.redo_count == 0
    assert question.next_redo_due is not None
    assert question.next_redo_due.weekday() == 5, "建议重做日必须落在周六（学生只有周末能打印）"

    db_session.refresh(record)
    assert record.mastery_status == redo_schedule.PENDING, "错题记录里的掌握度是同一概念的镜像"


def test_non_mcq_import_does_not_fake_correct_answer(client, db_session):
    question_id, _record = _seed_pooled_question(
        client,
        db_session,
        content="求 $x$：$2x+1=5$。",
        question_type="detailed_answer",
        answer_markdown="解得 $x=2$。",
    )
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.correct_answer == ""
    assert question.wrong_count == 1


def test_unmarked_record_is_not_seeded_into_pool(client, db_session):
    """未批（unknown）的题也允许入库，但不该被算成错题。"""

    question_id, _record = _seed_pooled_question(client, db_session, grad_status="unknown")
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.wrong_count == 0
    assert question.next_redo_due is None


# ---------------------------------------------------------------- 2. 完整闭环


def test_redo_round_trip_transforms_grades_and_masters(client, db_session):
    question_id, _record = _seed_pooled_question(client, db_session)
    today = datetime.date.today()

    created = client.post(
        "/api/redo/papers",
        json={"scope": "all", "limit": 5, "title": "E2E 重做卷"},
        headers=_auth(),
    )
    assert created.status_code == 200, created.text
    paper_id = created.json()["paper_id"]
    assert created.json()["redo_round"] == 1

    # —— 第 1 遍：原序，库内原题不动 ——
    detail = client.get(f"/api/redo/papers/{paper_id}").json()
    item = next(q for q in detail["questions"] if q["id"] == question_id)
    assert item["attempt_no"] == 1
    assert item["option_mode"] == "plain"
    assert "\\begin{choices}" in item["display_content"]
    assert item["recorded"] is None

    graded = client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": question_id, "result": "correct"}]},
        headers=_auth(),
    )
    assert graded.status_code == 200, graded.text
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.redo_count == 1
    assert question.redo_correct_streak == 1
    assert question.wrong_count == 1, "做对不该增加错次"
    assert question.mastery_status == redo_schedule.REDONE
    assert question.next_redo_due == redo_schedule.snap_to_weekend(
        today + datetime.timedelta(days=14)
    )

    # —— 第 2 遍：打乱选项，答案键必须同步重映射 ——
    detail2 = client.get(f"/api/redo/papers/{paper_id}").json()
    item2 = next(q for q in detail2["questions"] if q["id"] == question_id)
    assert item2["attempt_no"] == 2
    assert item2["option_mode"] == "shuffled"

    # 同一天再判对：不算跨日，连对不推进（防一天猛做两遍假装掌握）
    same_day = client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": question_id, "result": "correct"}]},
        headers=_auth(),
    )
    assert same_day.status_code == 200
    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.redo_correct_streak == 1
    assert question.mastery_status == redo_schedule.REDONE
    assert db_session.query(RedoAttempt).filter(RedoAttempt.paper_id == paper_id).count() == 1, \
        "同一份卷上同一题重复提交＝改判，不是多了一条记录"

    # —— 跨日、换一张卷再判对：连对 2 次 → 已掌握、不再排重做 ——
    # 跨日连对必然来自**不同轮次**的卷：同一份卷上每题只有一条判定（改判是覆盖），
    # 所以「一天之内把同一份卷做两遍」结构上就攒不出连对。
    second = client.post(
        "/api/redo/papers",
        json={"scope": "all", "title": "E2E 重做卷 · 第 2 轮"},
        headers=_auth(),
    )
    assert second.status_code == 200, second.text
    second_paper_id = second.json()["paper_id"]
    later = (today + datetime.timedelta(days=14)).isoformat()
    mastered = client.post(
        f"/api/redo/papers/{second_paper_id}/grade",
        json={
            "results": [{"question_id": question_id, "result": "correct"}],
            "attempted_on": later,
        },
        headers=_auth(),
    )
    assert mastered.status_code == 200, mastered.text
    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.redo_correct_streak == 2
    assert question.mastery_status == redo_schedule.MASTERED
    assert question.next_redo_due is None
    assert question.redo_count == 2


def test_regrade_incorrect_then_correct_does_not_double_count(client, db_session):
    """改判必须重放历史：先点错再改成对，错次要退回去。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = client.post(
        "/api/redo/papers", json={"scope": "all"}, headers=_auth()
    ).json()["paper_id"]

    client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": question_id, "result": "incorrect"}]},
        headers=_auth(),
    )
    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.wrong_count == 2, "首次错 1 + 重做判错 1"
    assert question.mastery_status == redo_schedule.PENDING

    client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": question_id, "result": "correct"}]},
        headers=_auth(),
    )
    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.wrong_count == 1, "改判为对之后，那一笔错要退回去"
    assert question.mastery_status == redo_schedule.REDONE


def test_grade_rejects_unknown_question_and_bad_result(client, db_session):
    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = client.post(
        "/api/redo/papers", json={"scope": "all"}, headers=_auth()
    ).json()["paper_id"]

    resp = client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={
            "results": [
                {"question_id": question_id, "result": "maybe"},
                {"question_id": 10 ** 9, "result": "correct"},
            ]
        },
        headers=_auth(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["graded"] == 0
    assert len(body["rejected"]) == 2, "不合法项要逐条回传原因，不能静默丢弃"


# ---------------------------------------------------------------- 3. 导出与列表


def test_export_tex_carries_transformed_choices_only_in_the_package(client, db_session):
    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = client.post(
        "/api/redo/papers", json={"scope": "all", "title": "导出用重做卷"}, headers=_auth()
    ).json()["paper_id"]

    resp = client.post(
        f"/api/redo/papers/{paper_id}/export",
        json={"format": "tex"},
        headers=_auth(),
    )
    assert resp.status_code == 200, resp.text
    assert "application/zip" in resp.headers["content-type"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        names = archive.namelist()
        assert any(name.endswith(".tex") for name in names)
        joined = "".join(
            archive.read(name).decode("utf-8", "replace")
            for name in names
            if name.endswith(".tex")
        )
    assert "\\begin{choices}" in joined, "第 1 遍导出必须带选项"

    # 库内原题不受导出影响
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert "\\begin{choices}" in question.content
    assert question.correct_answer == "A"


def test_redo_paper_is_hidden_from_paper_history_and_delete_recomputes(client, db_session):
    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = client.post(
        "/api/redo/papers", json={"scope": "all"}, headers=_auth()
    ).json()["paper_id"]

    # 重做卷**列**在组卷历史里（靠 is_redo 打标签区分），而不是藏起来 —— 老师要能
    # 在同一处找到自己排过的每一份卷；认领进来的那张尤其如此（它本来就在那儿）。
    listed = {item["id"]: item for item in client.get("/api/papers").json()["data"]}
    assert paper_id in listed, "重做卷也列在组卷历史里（打标签区分）"
    assert listed[paper_id]["is_redo"] is True
    assert listed[paper_id]["is_adopted_redo"] is False

    tasks = client.get("/api/redo/tasks").json()["tasks"]
    assert [task["id"] for task in tasks] == [paper_id]
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["question_count"] == 1

    client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": question_id, "result": "incorrect"}]},
        headers=_auth(),
    )
    db_session.expire_all()
    assert db_session.query(Question).filter(Question.id == question_id).one().wrong_count == 2

    deleted = client.delete(f"/api/redo/papers/{paper_id}", headers=_auth())
    assert deleted.status_code == 200, deleted.text
    db_session.expire_all()
    assert db_session.query(Paper).filter(Paper.id == paper_id).first() is None
    assert db_session.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).count() == 0
    assert db_session.query(RedoAttempt).filter(RedoAttempt.paper_id == paper_id).count() == 0
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.wrong_count == 1, "删掉卷之后不能留下凭空的错次"
    assert question.redo_count == 0


def test_pool_sync_backfills_historical_mistakes(client, db_session):
    """功能上线前入库的错题不在池里，同步接口要能补进来（且幂等）。"""

    question_id, record = _seed_pooled_question(client, db_session)
    question = db_session.query(Question).filter(Question.id == question_id).one()
    # 手工退回「上线前」的状态
    question.wrong_count = 0
    question.mastery_status = redo_schedule.PENDING
    question.next_redo_due = None
    db_session.commit()

    overview = client.get("/api/redo/overview").json()
    assert overview["stats"]["pool_total"] == 0
    assert overview["stats"]["backfill_pending"] == 1

    synced = client.post("/api/redo/pool/sync", headers=_auth())
    assert synced.status_code == 200, synced.text
    assert synced.json()["seeded"] == 1

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.wrong_count == 1
    assert question.next_redo_due.weekday() == 5

    again = client.post("/api/redo/pool/sync", headers=_auth())
    assert again.json()["seeded"] == 0, "幂等：再同步不该重复种"


def test_overview_and_question_filters_expose_pool(client, db_session):
    question_id, _record = _seed_pooled_question(client, db_session)

    overview = client.get("/api/redo/overview").json()
    assert overview["stats"]["pool_total"] == 1
    assert overview["stats"]["pending"] == 1
    assert overview["stats"]["due_now"] == 0, "刚入库的题排到本周末，今天不该是「已到期」"
    assert overview["knowledge"][0]["knowledge"] == "3.1 函数的概念"

    suggestions = client.get("/api/redo/suggestions?scope=all").json()
    assert [item["id"] for item in suggestions["items"]] == [question_id]
    assert suggestions["items"][0]["redo"]["wrong_count"] == 1

    due_now = client.get("/api/redo/suggestions").json()
    assert due_now["count"] == 0

    filtered = client.get("/api/questions?wrong_only=true&mastery=not_mastered").json()
    items = filtered if isinstance(filtered, list) else filtered["items"]
    target = next(item for item in items if item["id"] == question_id)
    assert target["redo"]["mastery_status"] == redo_schedule.PENDING
    assert target["redo"]["next_redo_due"] is not None

    mastered = client.get("/api/questions?mastery=mastered").json()
    assert (mastered if isinstance(mastered, list) else mastered["items"]) == []


def test_empty_pool_returns_actionable_error(client, db_session):
    resp = client.post("/api/redo/papers", json={"scope": "due"}, headers=_auth())
    assert resp.status_code == 400
    assert "同步历史错题" in resp.json()["message"]


# ---------------------------------------------------------------- 6. 人工标注掌握度
#
# 语义是**事件归并**：标注不是直接写字段，而是按日期插进重做历史的一条事件。
# 所以「标注之后又做错」要能推翻它，而「补录一张日期更早的旧卷」不能。


def _new_paper(client, **overrides):
    body = {"scope": "all", "limit": 5}
    body.update(overrides)
    resp = client.post("/api/redo/papers", json=body, headers=_auth())
    assert resp.status_code == 200, resp.text
    return resp.json()["paper_id"]


def _grade(client, paper_id, question_id, result, attempted_on=None):
    payload = {"results": [{"question_id": question_id, "result": result}]}
    if attempted_on:
        payload["attempted_on"] = attempted_on
    resp = client.post(
        f"/api/redo/papers/{paper_id}/grade", json=payload, headers=_auth()
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mark(client, question_id, mastered):
    return client.post(
        f"/api/redo/questions/{question_id}/mastery",
        json={"mastered": mastered},
        headers=_auth(),
    )


def _days_from_today(delta):
    return (datetime.date.today() + datetime.timedelta(days=delta)).isoformat()


def test_manual_mastery_takes_effect_without_faking_a_redo(client, db_session):
    """人工标注「已掌握」：移出重做池，但**不能**把自己算成重做了一遍。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _new_paper(client)
    _grade(client, paper_id, question_id, "correct")
    db_session.expire_all()
    before = db_session.query(Question).filter(Question.id == question_id).one()
    assert before.redo_count == 1
    assert before.mastery_status == redo_schedule.REDONE

    resp = _mark(client, question_id, True)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mastered"] is True and body["override_active"] is True
    assert body["redo"]["mastery_status"] == redo_schedule.MASTERED
    assert body["redo"]["next_redo_due"] is None
    assert body["redo"]["mastery_override"] == redo_schedule.MASTERED

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.mastery_override == redo_schedule.MASTERED
    assert question.mastery_override_at is not None
    # 判据：标注不是做题。redo_count 一旦被推高，导出时的 attempt_no 就跟着涨，
    # 第 3 遍的「无选项」档会错位成第 4 遍 —— 而那是印在纸面上的。
    assert question.redo_count == 1, "人工标注不能算作一次重做"
    assert (
        db_session.query(RedoAttempt)
        .filter(RedoAttempt.question_id == question_id)
        .count()
        == 1
    ), "人工标注不该往 redo_attempts 里插行"

    # 镜像到错题记录：两处不能各说各话
    record = (
        db_session.query(MistakeRecord)
        .filter(MistakeRecord.question_id == question_id)
        .one()
    )
    assert record.mastery_status == redo_schedule.MASTERED

    # 重做池里不再有它
    stats = client.get("/api/redo/overview").json()["stats"]
    assert stats["mastered"] == 1
    assert stats["mastered_manual"] == 1, "人工标注的那部分要能单独数出来"
    assert stats["pending"] == 0
    assert client.get("/api/redo/suggestions?scope=all").json()["count"] == 0


def test_paper_detail_exposes_manual_override_for_the_badge(client, db_session):
    """录入卡片要靠这个字段显示「人工标注」徽章 —— 与系统判出来的已掌握分开。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _new_paper(client)
    _mark(client, question_id, True)

    detail = client.get(f"/api/redo/papers/{paper_id}").json()
    item = next(q for q in detail["questions"] if q["id"] == question_id)
    assert item["redo"]["mastery_status"] == redo_schedule.MASTERED
    assert item["redo"]["mastery_override"] == redo_schedule.MASTERED

    _mark(client, question_id, False)
    detail = client.get(f"/api/redo/papers/{paper_id}").json()
    item = next(q for q in detail["questions"] if q["id"] == question_id)
    assert item["redo"]["mastery_override"] == ""


def test_manual_mastery_can_be_cancelled(client, db_session):
    """取消标注 = 清空那两列，状态回落到纯历史重放的结果。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    _mark(client, question_id, True)

    resp = _mark(client, question_id, False)
    assert resp.status_code == 200, resp.text
    assert resp.json()["mastered"] is False
    assert resp.json()["override_active"] is False

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.mastery_override == ""
    assert question.mastery_override_at is None
    # 这道题从没重做过，取消标注后应该回到「未掌握 + 排到周六」
    assert question.mastery_status == redo_schedule.PENDING
    assert question.next_redo_due is not None
    assert question.next_redo_due.weekday() == 5
    assert client.get("/api/redo/overview").json()["stats"]["mastered_manual"] == 0


def test_later_incorrect_grade_overturns_manual_mastery(client, db_session):
    """事件归并：标注之后**更晚**的录入重新接管 —— 又做错了就回到未掌握。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    # 卷必须先出：已掌握的题不会再被排进新卷，所以「推翻」只能发生在已存在的卷上。
    paper_id = _new_paper(client)
    _mark(client, question_id, True)

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.mastery_status == redo_schedule.MASTERED

    _grade(client, paper_id, question_id, "incorrect", attempted_on=_days_from_today(7))

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.mastery_status == redo_schedule.PENDING, "更晚的录入要能推翻标注"
    assert question.wrong_count == 2, "确实又错了一次，账照记"
    assert question.next_redo_due is not None
    # 被推翻的标注就地清掉，否则卡片会在「未掌握」的题上挂「人工标注」徽章
    assert question.mastery_override == ""
    assert question.mastery_override_at is None
    assert client.get("/api/redo/overview").json()["stats"]["mastered_manual"] == 0


def test_backfilled_older_paper_does_not_overturn_manual_mastery(client, db_session):
    """补录一张日期**早于**标注的旧卷：不该把标注推翻，否则老师标了也白标。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _new_paper(client)
    _mark(client, question_id, True)

    _grade(client, paper_id, question_id, "incorrect", attempted_on=_days_from_today(-3))

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.mastery_status == redo_schedule.MASTERED, "早于标注的补录不能推翻它"
    assert question.mastery_override == redo_schedule.MASTERED
    assert question.wrong_count == 2, "错次照记 —— 她确实错过了，只是掌握度由老师说了算"
    assert question.next_redo_due is None


def test_manual_mastery_is_idempotent_and_replaces_the_mark(client, db_session):
    """重复标注是**替换**最近一次标注，不是追加。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    _mark(client, question_id, True)
    db_session.expire_all()
    first = db_session.query(Question).filter(Question.id == question_id).one()
    first_at = first.mastery_override_at
    assert first.redo_count == 0

    _mark(client, question_id, True)
    db_session.expire_all()
    second = db_session.query(Question).filter(Question.id == question_id).one()
    assert second.mastery_status == redo_schedule.MASTERED
    assert second.redo_count == 0, "重复标注也不能攒出重做次数"
    assert second.mastery_override_at >= first_at


def test_manual_mastery_rejects_outside_pool_and_bad_payload(client, db_session):
    pooled_id, _record = _seed_pooled_question(client, db_session)

    missing_field = client.post(
        f"/api/redo/questions/{pooled_id}/mastery", json={}, headers=_auth()
    )
    assert missing_field.status_code == 400
    assert "mastered" in missing_field.json()["message"]

    unknown = _mark(client, 999999, True)
    assert unknown.status_code == 404

    # 从没错过的题说「已掌握」没有意义，还会把掌握度统计搅浑。
    # 注意换一道题面：同一份卷面重复入库会被查重拦下（409）。
    never_wrong_id, _record2 = _seed_pooled_question(
        client,
        db_session,
        grad_status="unknown",
        question_no="8",
        content="下列说法正确的是（　　）\n\\begin{choices}\n\\item 甲\n\\item 乙\n\\end{choices}",
        answer_markdown="由定义知甲正确，故选 A.",
    )
    outside = _mark(client, never_wrong_id, True)
    assert outside.status_code == 400
    assert "重做池" in outside.json()["message"]


# ---------------------------------------------------------------- 9. 认领组卷台那张卷
#
# 断点：错题闭环的第 1 遍练习几乎必然发生在**组卷台**那条通道（入库即送组卷 → 排版 →
# 印出来），可那张卷不带 is_redo，它的做题结果没有任何入口写回 redo_attempts —— 只能
# 去重做台重新生成一张，题序与手上纸卷对不上，轮次还会被少算一遍。下面这些用例钉住
# 「认领」这座桥。


def _save_normal_paper(client, question_ids, title="高一上第3周错题练习"):
    """在组卷台存一份普通卷（= 老师排版好、印给学生做的那一份）。"""

    payload = {
        "title": title,
        "subtitle": "错题练习",
        "paper_type": "exam",
        "show_secret": True,
        "show_notice": True,
        "questions": [{"id": qid, "score": 5} for qid in question_ids],
    }
    res = client.post("/api/paper/save", json=payload, headers=_auth())
    assert res.status_code == 200, res.text
    return res.json()["paper_id"]


def _add_bank_question(db_session, content="下列说法正确的是（　　）"):
    """建一道**从没错过**的题库题（origin=bank）—— 组卷时一起混进来的那种。"""

    question = Question(content=content, question_type="detailed_answer", origin="bank")
    db_session.add(question)
    db_session.commit()
    return question.id


def _adopt(client, payload):
    return client.post("/api/redo/adopt", json=payload, headers=_auth())


def _eligibility(client, payload):
    return client.post("/api/redo/eligibility", json=payload, headers=_auth())


def test_adopt_regular_paper_opens_redo_loop(client, db_session):
    """认领一张已保存的普通卷：进重做任务、可录入、呈现锁原序。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _save_normal_paper(client, [question_id])

    adopted = _adopt(client, {"paper_id": paper_id})
    assert adopted.status_code == 200, adopted.text
    body = adopted.json()
    assert body["created"] is False, "认领已保存的卷不该再新建一张"
    assert body["redo_round"] == 1
    assert body["pooled_count"] == 1

    tasks = client.get("/api/redo/tasks").json()["tasks"]
    task = [item for item in tasks if item["id"] == paper_id]
    assert task, "认领之后要出现在重做任务里"
    assert task[0]["adopted"] is True, "要能跟错题台生成的重做卷区分开"

    detail = client.get(f"/api/redo/papers/{paper_id}").json()
    assert detail["paper"]["adopted"] is True
    # 纸卷已经做完，重排/打乱都会让老师对着手上那张点错题。
    assert detail["questions"][0]["option_mode"] == "plain"
    assert detail["questions"][0]["attempt_no"] == 1


def test_adopt_advances_round_so_next_paper_is_not_round_one(client, db_session):
    """**这条是整座桥的意义**：认领那遍练习必须记账，下一张卷的轮次要往前推。

    不记账时 ``redo_round = max(attempt_no)`` 仍算 1 —— 学生做了一整遍却等于没做，
    第二遍该被打乱的选项也不会打乱。
    """

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _save_normal_paper(client, [question_id])
    assert _adopt(client, {"paper_id": paper_id}).status_code == 200

    graded = client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": question_id, "result": "correct"}]},
        headers=_auth(),
    )
    assert graded.status_code == 200, graded.text

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.redo_count == 1, "认领那遍练习要进重做计数"
    assert question.redo_correct_streak == 1

    nxt = client.post("/api/redo/papers", json={"scope": "all"}, headers=_auth())
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["redo_round"] == 2, "认领那遍没记账的话这里会退回第 1 轮"


def test_adopt_is_idempotent(client, db_session):
    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _save_normal_paper(client, [question_id])

    first = _adopt(client, {"paper_id": paper_id}).json()
    second = _adopt(client, {"paper_id": paper_id}).json()

    assert first["already"] is False
    assert second["already"] is True
    papers = [item for item in client.get("/api/papers").json()["data"] if item["id"] == paper_id]
    assert len(papers) == 1


def test_adopt_draft_persists_paper_without_usage_count(client, db_session):
    """主编辑器导出时卷面还没落库 —— 认领要现场建卷，且不算组卷引用。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    db_session.expire_all()
    before = db_session.query(Question).filter(Question.id == question_id).one().usage_count or 0

    res = _adopt(
        client,
        {
            "title": "导出后认领的卷",
            "paper_type": "exam",
            "questions": [{"id": question_id, "score": 5}],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["created"] is True

    db_session.expire_all()
    after = db_session.query(Question).filter(Question.id == question_id).one().usage_count or 0
    assert after == before, "重做卷不计组卷引用"


def test_adopt_rejects_paper_without_mistakes(client, db_session):
    """卷里一道错题都没有 —— 没有可记的账，明确拒绝而不是留下一张空任务。"""

    question_id, _record = _seed_pooled_question(client, db_session, grad_status="unknown")
    paper_id = _save_normal_paper(client, [question_id])

    res = _adopt(client, {"paper_id": paper_id})
    assert res.status_code == 400
    assert "重做池" in res.json()["message"]


def test_release_returns_paper_to_history_and_undoes_attempts(client, db_session):
    """取消认领：卷**留在**组卷历史里，录入记录撤掉，掌握度按剩余历史重算。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _save_normal_paper(client, [question_id])
    _adopt(client, {"paper_id": paper_id})
    client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": question_id, "result": "incorrect"}]},
        headers=_auth(),
    )
    db_session.expire_all()
    assert db_session.query(Question).filter(Question.id == question_id).one().wrong_count == 2

    released = client.post("/api/redo/release", json={"paper_id": paper_id}, headers=_auth())
    assert released.status_code == 200, released.text

    db_session.expire_all()
    question = db_session.query(Question).filter(Question.id == question_id).one()
    assert question.wrong_count == 1, "撤销录入后不能留下凭空的错次"
    assert question.redo_count == 0
    assert db_session.query(Paper).filter(Paper.id == paper_id).first() is not None, "卷必须还在"
    listed = {item["id"]: item for item in client.get("/api/papers").json()["data"]}
    assert listed[paper_id]["is_redo"] is False, "取消认领后回到普通卷"


def test_delete_on_adopted_paper_releases_instead_of_destroying(client, db_session):
    """认领卷点「删除」= 取消认领：绝不能顺手毁掉老师在组卷台排过的那份卷。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _save_normal_paper(client, [question_id])
    _adopt(client, {"paper_id": paper_id})

    res = client.delete(f"/api/redo/papers/{paper_id}", headers=_auth())
    assert res.status_code == 200, res.text
    assert "取消认领" in res.json()["message"]

    db_session.expire_all()
    assert db_session.query(Paper).filter(Paper.id == paper_id).first() is not None
    assert db_session.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).count() == 1


def test_eligibility_and_decline(client, db_session):
    """查询资格 → 点过「这是普通卷」之后不再追问。"""

    question_id, _record = _seed_pooled_question(client, db_session)
    paper_id = _save_normal_paper(client, [question_id])

    first = _eligibility(client, {"paper_id": paper_id}).json()
    assert first["eligible"] is True
    assert first["pooled_count"] == 1
    assert first["total"] == 1

    declined = client.post("/api/redo/decline", json={"paper_id": paper_id}, headers=_auth())
    assert declined.status_code == 200
    second = _eligibility(client, {"paper_id": paper_id}).json()
    assert second["declined"] is True
    assert second["eligible"] is False, "说过「这是普通卷」之后不该再问"

    # 未落库的卷面按 question_ids 查 —— 主编辑器导出后走的就是这条路。
    by_ids = _eligibility(client, {"question_ids": [question_id]}).json()
    assert by_ids["eligible"] is True

    # 认领之后也不再 eligible，免得重印一次又建一张重复的卷。
    _adopt(client, {"paper_id": paper_id})
    after_adopt = _eligibility(client, {"paper_id": paper_id}).json()
    assert after_adopt["already_redo"] is True
    assert after_adopt["eligible"] is False


def test_adopted_paper_mixes_non_mistakes(client, db_session):
    """卷里混了非错题：照样能录（做错就进池），但只有池内题算「这道卷含几道错题」。"""

    pooled_id, _record = _seed_pooled_question(client, db_session)
    fresh_id = _add_bank_question(db_session)
    paper_id = _save_normal_paper(client, [pooled_id, fresh_id])

    body = _adopt(client, {"paper_id": paper_id}).json()
    assert body["question_count"] == 2
    assert body["pooled_count"] == 1, "只有一道原本就是错题"

    # 非错题做错 → 自然进池。这正是老师想要的效果，所以要放行而不是拦下。
    graded = client.post(
        f"/api/redo/papers/{paper_id}/grade",
        json={"results": [{"question_id": fresh_id, "result": "incorrect"}]},
        headers=_auth(),
    )
    assert graded.status_code == 200, graded.text
    db_session.expire_all()
    fresh = db_session.query(Question).filter(Question.id == fresh_id).one()
    assert fresh.wrong_count == 1, "做错之后进重做池"
    assert fresh.mastery_status == redo_schedule.PENDING
