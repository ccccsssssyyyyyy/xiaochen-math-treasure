"""切块元数据（``analysis.json``）完整性回归。

**为什么单独一个文件**：这个文件是错题工作台里唯一「既当输入又当输出」的状态。
所有改页元数据的操作都是「读回整份 → 改点名键 → 整份写回」（``_update_mistake_page_meta``），
所以只要「读」这一步把「坏了」和「还没有」混为一谈，写回去的那一刻就把整批人工框选、
人工合并、删除名单抹平了 —— 文件还在、没有报错、界面照常，用户只会得出「我画的框全没了」。

本文件锁死三件事：

1. **损坏必抛**，不得降级成空壳（``MistakeAnalysisError``）；
2. 损坏时三个写入口（框选 / 合并 / 删除）**全部拒绝**，且 ``analysis.json`` 字节不变
   —— 这是「覆盖」回归的判别式，不是「有没有报错」；
3. **「没有」与「坏了」必须给出不同的信号**：文件不存在／为空 → 400「还没有切块结果」；
   文件在但解析不出来 → 409「已损坏」。两者都成了 400 或都成了 409，说明状态被合并了。
"""

import json

import pytest
from PIL import Image, ImageDraw

from mathbank.database import MistakeBatch, MistakeRecord, Student

PAGE_W, PAGE_H = 600, 1200

BLOCKS = [
    {"block_index": 0, "y_start": 0.05, "y_end": 0.14, "x_start": 0.0, "x_end": 0.49, "column_index": 1},
    {"block_index": 1, "y_start": 0.08, "y_end": 0.17, "x_start": 0.51, "x_end": 1.0, "column_index": 2},
]

#: 一份「有内容」的合法元数据：两页两个块，已有人工框与删除名单。
#: 用它当被破坏的对象 —— 空壳与它的差别足够大，覆盖与否一眼可辨。
HEALTHY_ANALYSIS = {
    "version": 5,
    "pages": [
        {
            "page_no": 1,
            "width": PAGE_W,
            "height": PAGE_H,
            "layout": {"mode": "double", "boundary": 0.5, "source": "visual", "confidence": 1.0},
            "column_boundary": 0.5,
            "blocks": BLOCKS,
            "gap_candidates": [],
            "snap_points": [],
            "column_snap_points": {},
            "column_gap_candidates": {},
            "manual_boxes": [[0.1, 0.2, 0.3, 0.4]],
            "manual_merges": [],
            "hidden_blocks": [[0.0, 0.60, 0.49, 0.70]],
        },
        {
            "page_no": 2,
            "width": PAGE_W,
            "height": PAGE_H,
            "layout": {"mode": "single", "boundary": 1.0, "source": "visual", "confidence": 1.0},
            "column_boundary": 1.0,
            "blocks": [dict(BLOCKS[0], block_index=0)],
            "gap_candidates": [],
            "snap_points": [],
            "column_snap_points": {},
            "column_gap_candidates": {},
            "manual_boxes": [],
            "manual_merges": [{"id": "m1", "rects": [[0.0, 0.05, 0.49, 0.14]], "direction": "v", "primary": 0}],
            "hidden_blocks": [],
        },
    ],
}


def _write_page_image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(image)
    for index in range(4):
        top = 80 + index * 240
        draw.rectangle([40, top, 260, top + 60], fill=0)
        draw.rectangle([320, top, PAGE_W - 40, top + 60], fill=0)
    image.save(path, format="PNG")


@pytest.fixture
def integrity_env(tmp_path, monkeypatch, db_session):
    """一个「已经切好块」的批次：页图与元数据都在，源文件在（供重切任务用）。"""

    import main

    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))

    student = Student(name="元数据完整性", grade="高一")
    db_session.add(student)
    db_session.commit()

    batch = MistakeBatch(
        student_id=student.id,
        subject="physics",
        title="元数据完整性",
        page_count=2,
        status="reviewing",
    )
    db_session.add(batch)
    db_session.commit()

    batch_dir = tmp_path / "uploads" / "mistakes" / str(batch.id)
    _write_page_image(batch_dir / "pages" / "page_1.png")
    _write_page_image(batch_dir / "pages" / "page_2.png")
    (batch_dir / "blocks").mkdir(parents=True, exist_ok=True)
    # 源文件只要存在即可（重切任务的前置探针在渲染之前就返回）
    (batch_dir / "source.pdf").write_bytes(b"%PDF-1.4\n% stub\n")
    (batch_dir / "analysis.json").write_text(
        json.dumps(HEALTHY_ANALYSIS, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for block in BLOCKS:
        db_session.add(
            MistakeRecord(
                batch_id=batch.id,
                student_id=student.id,
                subject="physics",
                page_no=1,
                block_index=block["block_index"],
                block_y_start=block["y_start"],
                block_y_end=block["y_end"],
                block_x_start=block["x_start"],
                block_x_end=block["x_end"],
                column_index=block["column_index"],
                image_block=f"/static/test_uploads/mistakes/{batch.id}/blocks/p001_b{block['block_index']:02d}.png",
                recognize_status="pending",
                grad_status="unknown",
            )
        )
    db_session.commit()

    return {"batch": batch, "dir": batch_dir, "analysis": batch_dir / "analysis.json"}


def _token():
    import main

    return {"X-Local-Token": main.LOCAL_TOKEN}


def _corrupt(env, payload="{ this is not json"):
    """把 analysis.json 写成损坏内容，返回写入后的字节（供比对是否被覆盖）。"""

    env["analysis"].write_text(payload, encoding="utf-8")
    return env["analysis"].read_bytes()


def _boxes(client, batch_id, page_no=1, boxes=None):
    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/blocks",
        json={"boxes": boxes if boxes is not None else [[0.1, 0.2, 0.3, 0.4]]},
        headers=_token(),
    )


def _merges(client, batch_id, page_no=1):
    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/merges",
        json={"merges": [{"rects": [[0.0, 0.05, 0.49, 0.14]]}]},
        headers=_token(),
    )


def _hidden(client, batch_id, page_no=1):
    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/hidden",
        json={"rects": [[0.0, 0.05, 0.49, 0.14]]},
        headers=_token(),
    )


def _detail(client, batch_id):
    return client.get(f"/api/mistakes/batches/{batch_id}", headers=_token())


# ------------------------------------------------------------------ 读函数三态


def test_read_returns_shell_only_when_file_absent(integrity_env, monkeypatch):
    """文件不存在＝合法的「还没切过」，返回空壳而不是报错。

    空壳里必须带顶层 ``cross_page_merges``（v6 新增）：跨页合并组不属于任何一页，
    它挂在文件顶层。空壳漏掉这个键，第一次「读回 → 改键 → 整份写回」就会把用户
    合好的跨页题从文件里抹掉。
    """

    import main

    integrity_env["analysis"].unlink()
    data = main._read_mistake_analysis(integrity_env["batch"].id)
    assert data == {"version": 6, "cross_page_merges": [], "pages": []}


def test_read_treats_blank_file_as_absent(integrity_env):
    """空文件里没有任何数据可丢，按「还没有」处理比报错有用。"""

    import main

    _corrupt(integrity_env, "   \n")
    assert main._read_mistake_analysis(integrity_env["batch"].id) == {
        "version": 6,
        "cross_page_merges": [],
        "pages": [],
    }


@pytest.mark.parametrize(
    "payload",
    [
        "{ this is not json",           # JSON 语法错
        "[1, 2, 3]",                    # 顶层不是对象
        '{"version": 5}',               # 缺 pages
        '{"version": 5, "pages": {}}',  # pages 不是列表
        '"just a string"',              # 顶层是标量
    ],
)
def test_read_raises_on_corruption_never_degrades(integrity_env, payload):
    """**核心判别式**：损坏一律抛异常。降级成空壳就是数据丢失的前一步。"""

    import main

    _corrupt(integrity_env, payload)
    with pytest.raises(main.MistakeAnalysisError):
        main._read_mistake_analysis(integrity_env["batch"].id)


def test_read_backfills_missing_lists_without_touching_content(integrity_env):
    """旧版本文件缺 manual_* 就地补空列表，但已有内容一个不动。"""

    import main

    trimmed = json.loads(json.dumps(HEALTHY_ANALYSIS))
    for page in trimmed["pages"]:
        page.pop("manual_boxes", None)
        page.pop("manual_merges", None)
        page.pop("hidden_blocks", None)
    _corrupt(integrity_env, json.dumps(trimmed, ensure_ascii=False))

    data = main._read_mistake_analysis(integrity_env["batch"].id)
    assert data["pages"][0]["manual_boxes"] == []
    assert data["pages"][0]["blocks"] == BLOCKS
    assert data["pages"][1]["manual_merges"] == []


# ------------------------------------------------------ 三个写入口：拒绝 + 不覆盖


@pytest.mark.parametrize("call", [_boxes, _merges, _hidden], ids=["boxes", "merges", "hidden"])
def test_corrupt_metadata_rejects_writes_and_never_overwrites(client, integrity_env, call):
    """**最重要的回归**：元数据损坏时三个写入口全部拒绝，且文件字节原封不动。

    只断言「返回 409」是不够的 —— 真正要守住的是**没有写盘**。旧实现会先返回一个看起来
    成功的响应，同时把空壳写进文件；所以这里比对的是字节，不是状态码。
    """

    batch_id = integrity_env["batch"].id
    before = _corrupt(integrity_env)

    response = call(client, batch_id)
    assert response.status_code == 409, response.text
    assert "已损坏" in response.json()["message"]

    assert integrity_env["analysis"].read_bytes() == before, "损坏的元数据被覆盖了"


def test_corrupt_metadata_message_is_actionable(client, integrity_env):
    """报错要能指导下一步：带上文件路径，并提示可先备份再重切。"""

    batch_id = integrity_env["batch"].id
    _corrupt(integrity_env)

    message = _boxes(client, batch_id).json()["message"]
    assert "analysis.json" in message
    assert "重新切题" in message


def test_corrupt_metadata_does_not_touch_database_records(client, db_session, integrity_env):
    """守门人是在做任何 DB 改动**之前**退出的：记录条数与内容都不许变。"""

    batch_id = integrity_env["batch"].id
    before = [
        (r.block_index, r.grad_status, r.block_y_start)
        for r in db_session.query(MistakeRecord)
        .filter(MistakeRecord.batch_id == batch_id)
        .order_by(MistakeRecord.block_index.asc())
        .all()
    ]
    _corrupt(integrity_env)

    assert _boxes(client, batch_id).status_code == 409
    assert _merges(client, batch_id).status_code == 409
    assert _hidden(client, batch_id).status_code == 409

    after = [
        (r.block_index, r.grad_status, r.block_y_start)
        for r in db_session.query(MistakeRecord)
        .filter(MistakeRecord.batch_id == batch_id)
        .order_by(MistakeRecord.block_index.asc())
        .all()
    ]
    assert after == before


# --------------------------------------------------- 读入口：显式报错而不是给空


def test_detail_reports_corruption_instead_of_empty_pages(client, integrity_env):
    """批次详情必须显式报错。返回 200 + 空 pages 会被读成「这个批次没有题块」，
    用户接着就会去重切 —— 正好把原件覆盖掉。"""

    batch_id = integrity_env["batch"].id
    before = _corrupt(integrity_env)

    response = _detail(client, batch_id)
    assert response.status_code == 409, response.text
    assert "已损坏" in response.json()["message"]
    assert integrity_env["analysis"].read_bytes() == before


# ------------------------------------------- 「没有」与「坏了」必须是两种信号


def test_absent_and_corrupt_are_distinguishable(client, integrity_env):
    """两种状态的信号必须不同，否则前端分不清「去切题」还是「要修文件」。"""

    batch_id = integrity_env["batch"].id

    # 还没有元数据：400「还没有切块结果」
    integrity_env["analysis"].unlink()
    absent = _boxes(client, batch_id)
    assert absent.status_code == 400, absent.text
    assert "还没有切块结果" in absent.json()["message"]

    # 元数据损坏：409「已损坏」
    _corrupt(integrity_env)
    corrupt = _boxes(client, batch_id)
    assert corrupt.status_code == 409, corrupt.text
    assert "还没有" not in corrupt.json()["message"]


def test_healthy_metadata_still_writes_normally(client, integrity_env):
    """反向对照：元数据正常时一切照旧，别把守门人做成一律拒绝。"""

    batch_id = integrity_env["batch"].id
    response = _hidden(client, batch_id)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"

    written = json.loads(integrity_env["analysis"].read_text(encoding="utf-8"))
    assert written["pages"][0]["manual_boxes"] == HEALTHY_ANALYSIS["pages"][0]["manual_boxes"]
    assert written["pages"][1]["manual_merges"] == HEALTHY_ANALYSIS["pages"][1]["manual_merges"]


# --------------------------------------------------------------- 重切：前置探针


def test_recut_aborts_before_overwriting_corrupt_metadata(client, db_session, integrity_env):
    """重切要把人工框带过去，读不出来时只能拿空壳顶上 —— 所以必须在动手前退出。

    判别式有两条：任务落在 error 态（不是 completed），以及 analysis.json 字节不变。
    只查状态码的话，旧实现会「跑完并成功」却把框全抹了。
    """

    import main

    batch_id = integrity_env["batch"].id
    before = _corrupt(integrity_env)

    task_id = f"cut-integrity-{batch_id}"
    main.DOCUMENT_TASKS.create(
        task_id,
        status="pending",
        log="测试用切题任务",
        document_type="mistake_cut",
        batch_id=batch_id,
        temp_assets=[],
    )
    main.DOCUMENT_TASKS.init_steps(task_id, main.MISTAKE_CUT_STEPS)

    main.run_mistake_cut_task(task_id, batch_id)

    snapshot = main.DOCUMENT_TASKS.snapshot(task_id)
    assert snapshot.get("status") == "error", snapshot
    assert "已损坏" in (snapshot.get("error") or "")

    db_session.expire_all()
    batch = db_session.query(MistakeBatch).filter(MistakeBatch.id == batch_id).one()
    assert batch.status == "cut_failed"

    assert integrity_env["analysis"].read_bytes() == before, "重切把损坏的元数据覆盖了"


def test_recut_early_probe_runs_before_rendering(integrity_env, monkeypatch):
    """探针必须在渲染页图**之前** —— 否则损坏的批次要白跑几分钟才报错。"""

    import main

    batch_id = integrity_env["batch"].id
    _corrupt(integrity_env)

    def _explode(*args, **kwargs):
        raise AssertionError("元数据已损坏，不该走到渲染这一步")

    monkeypatch.setattr(main, "render_source_to_pages", _explode)

    task_id = f"cut-early-{batch_id}"
    main.DOCUMENT_TASKS.create(
        task_id,
        status="pending",
        log="测试用切题任务",
        document_type="mistake_cut",
        batch_id=batch_id,
        temp_assets=[],
    )
    main.DOCUMENT_TASKS.init_steps(task_id, main.MISTAKE_CUT_STEPS)

    main.run_mistake_cut_task(task_id, batch_id)
    assert main.DOCUMENT_TASKS.snapshot(task_id).get("status") == "error"
