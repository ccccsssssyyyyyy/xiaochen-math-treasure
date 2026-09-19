"""错题工作台「应用本页框选」接口测试（``POST /api/mistakes/batches/{id}/pages/{n}/blocks``）。

用内存库 + 临时上传目录，页图是合成图。断言的全是接口契约：框怎么落进元数据、
记录怎么随框重建、块图有没有孤儿、作答状态继承的边界在哪。

这里刻意把「清除框能恢复自动块」也锁死 —— 它是「人工框只是叠在自动基线之上的
一层遮蔽物」这个设计的全部价值所在，一旦有人图省事把合并结果写回 blocks，
这条会立刻红。
"""

import json

import pytest
from PIL import Image, ImageDraw

from mathbank.database import MistakeBatch, MistakeRecord, Student

PAGE_W, PAGE_H = 600, 1200

#: 合成页图上的三条横向黑带，对应元数据里的三个自动块
AUTO_SPANS = [(0.05, 0.12), (0.15, 0.22), (0.60, 0.70)]


def _write_page_image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(image)
    for index in range(4):
        top = 80 + index * 240
        draw.rectangle([60, top, PAGE_W - 60, top + 60], fill=0)
    image.save(path, format="PNG")


def _apply(client, batch_id, page_no, boxes):
    """调接口。写操作必须带本地令牌，否则会被安全中间件挡在 403。"""

    import main

    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/blocks",
        json={"boxes": boxes},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )


@pytest.fixture
def box_env(tmp_path, monkeypatch, db_session):
    import main

    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))

    student = Student(name="测试学生", grade="高一")
    db_session.add(student)
    db_session.commit()

    batch = MistakeBatch(
        student_id=student.id,
        subject="physics",
        title="框选测试",
        page_count=1,
        status="reviewing",
    )
    db_session.add(batch)
    db_session.commit()

    batch_dir = tmp_path / "uploads" / "mistakes" / str(batch.id)
    _write_page_image(batch_dir / "pages" / "page_1.png")
    (batch_dir / "blocks").mkdir(parents=True, exist_ok=True)
    (batch_dir / "analysis.json").write_text(
        json.dumps(
            {
                "version": 3,
                "pages": [
                    {
                        "page_no": 1,
                        "width": PAGE_W,
                        "height": PAGE_H,
                        "line_count": 3,
                        "layout": {
                            "mode": "single",
                            "boundary": 1.0,
                            "source": "visual",
                            "confidence": 1.0,
                        },
                        "column_boundary": 1.0,
                        "blocks": [
                            {
                                "block_index": index,
                                "y_start": y_start,
                                "y_end": y_end,
                                "x_start": 0.0,
                                "x_end": 1.0,
                                "column_index": 0,
                            }
                            for index, (y_start, y_end) in enumerate(AUTO_SPANS)
                        ],
                        "gap_candidates": [],
                        "snap_points": [],
                        "column_snap_points": {},
                        "column_gap_candidates": {},
                        "manual_boxes": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    for index, (y_start, y_end) in enumerate(AUTO_SPANS):
        db_session.add(
            MistakeRecord(
                batch_id=batch.id,
                student_id=student.id,
                subject="physics",
                page_no=1,
                block_index=index,
                block_y_start=y_start,
                block_y_end=y_end,
                block_x_start=0.0,
                block_x_end=1.0,
                column_index=0,
                image_block=(
                    f"/static/test_uploads/mistakes/{batch.id}/blocks/"
                    f"p001_b{index:02d}.png"
                ),
                recognize_status="pending",
                grad_status="unknown",
            )
        )
    db_session.commit()

    # 块 0 预置作答状态，用来验证「框重建后状态有没有跟过来」
    first = (
        db_session.query(MistakeRecord)
        .filter(MistakeRecord.batch_id == batch.id, MistakeRecord.block_index == 0)
        .one()
    )
    first.grad_status = "incorrect"
    first.error_reason = "概念不清"
    first.answer_markdown = "【答案】A"
    db_session.commit()

    return {"batch": batch, "dir": batch_dir}


def _page_records(db_session, batch_id):
    return (
        db_session.query(MistakeRecord)
        .filter(MistakeRecord.batch_id == batch_id, MistakeRecord.page_no == 1)
        .order_by(MistakeRecord.block_index.asc())
        .all()
    )


def _block_images(batch_dir):
    return sorted(path.name for path in (batch_dir / "blocks").glob("*.png"))


def _analysis(batch_dir):
    return json.loads((batch_dir / "analysis.json").read_text(encoding="utf-8"))


def test_apply_boxes_merges_fragments_and_rebuilds_records(
    client, db_session, box_env
):
    """一个框盖住两个碎片 → 本页只剩「框 + 没被压到的块」，块图同步换掉。"""

    batch_id = box_env["batch"].id
    response = _apply(client, batch_id, 1, [[0.0, 0.04, 1.0, 0.24]])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["block_count"] == 2
    assert payload["box_count"] == 1
    assert payload["removed"] == 3

    records = _page_records(db_session, batch_id)
    assert [
        (r.block_index, round(r.block_y_start, 3), round(r.block_y_end, 3))
        for r in records
    ] == [(0, 0.04, 0.24), (1, 0.6, 0.7)]
    # 块图与记录一一对应（序号变少时旧图要被清掉，不留孤儿）
    assert _block_images(box_env["dir"]) == ["p001_b00.png", "p001_b01.png"]
    # 人工框落进元数据，且自动基线原样保留（这是「删框能恢复」的前提）
    entry = _analysis(box_env["dir"])["pages"][0]
    assert entry["manual_boxes"] == [[0.0, 0.04, 1.0, 0.24]]
    assert len(entry["blocks"]) == 3


def test_apply_boxes_carries_over_marks(client, db_session, box_env):
    """框压到的旧块，其作答状态要跟到新块上 —— 重算不该清空用户点过的对错。"""

    batch_id = box_env["batch"].id
    response = _apply(client, batch_id, 1, [[0.0, 0.04, 1.0, 0.135]])
    assert response.status_code == 200, response.text

    box_record = _page_records(db_session, batch_id)[0]
    assert (
        round(box_record.block_y_start, 3),
        round(box_record.block_y_end, 3),
    ) == (0.04, 0.135)
    assert box_record.grad_status == "incorrect"
    assert box_record.error_reason == "概念不清"
    assert box_record.answer_markdown == "【答案】A"


def test_clearing_boxes_restores_auto_blocks_and_marks(client, db_session, box_env):
    """空框列表＝清除人工框：自动块原样回来，作答状态也跟着回来。

    这条锁的是「人工框只是遮蔽层」的设计。若把合并结果写回 blocks，
    第 0 块会被永久换成那个框的 y 范围，这条立刻失败。
    """

    batch_id = box_env["batch"].id
    assert (
        _apply(client, batch_id, 1, [[0.0, 0.04, 1.0, 0.135]]).status_code == 200
    )

    response = _apply(client, batch_id, 1, [])
    assert response.status_code == 200, response.text
    assert response.json()["block_count"] == 3

    records = _page_records(db_session, batch_id)
    assert [
        (round(r.block_y_start, 3), round(r.block_y_end, 3)) for r in records
    ] == AUTO_SPANS
    assert records[0].grad_status == "incorrect"
    assert records[0].error_reason == "概念不清"
    assert _block_images(box_env["dir"]) == [
        "p001_b00.png",
        "p001_b01.png",
        "p001_b02.png",
    ]


def test_apply_boxes_keeps_uncovered_fragment(client, db_session, box_env):
    """框只压住下半个块 → 上半作为残段留下，正文不会被静默吃掉。"""

    batch_id = box_env["batch"].id
    response = _apply(client, batch_id, 1, [[0.0, 0.16, 1.0, 0.40]])
    assert response.status_code == 200, response.text

    spans = {
        (round(r.block_y_start, 3), round(r.block_y_end, 3))
        for r in _page_records(db_session, batch_id)
    }
    assert (0.05, 0.12) in spans, "块 0 没被压到，应原样保留"
    assert (0.15, 0.16) in spans, "块 1 被啃掉下半，剩的残段要留下"
    assert (0.6, 0.7) in spans, "块 2 没被压到，应原样保留"
    assert (0.16, 0.4) in spans, "框自身成为题块"


def test_apply_boxes_drops_degenerate_input(client, box_env):
    """脏参数不该让整页重算失败，也不该凭空多出框。"""

    batch_id = box_env["batch"].id
    response = _apply(
        client,
        batch_id,
        1,
        [
            [0.2, 0.5, 0.1, 0.5],  # 零宽
            ["x", 0.1, 0.5, 0.2],  # 非数字
            [0.0, 0.1, 0.5],  # 少一个坐标
            [0.0, 0.04, 1.0, 0.135],  # 合法
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["box_count"] == 1


def test_apply_boxes_rejects_missing_page(client, db_session, box_env):
    """页图不存在 → 400，且不该动到已有记录。"""

    batch_id = box_env["batch"].id
    response = _apply(client, batch_id, 9, [[0.0, 0.1, 1.0, 0.2]])
    assert response.status_code == 400
    assert "页图不存在" in response.json()["message"]
    assert len(_page_records(db_session, batch_id)) == 3


def test_apply_boxes_rejects_page_without_metadata(client, box_env):
    """页图在但元数据里没这一页 → 400（不能凭空造块）。"""

    batch_id = box_env["batch"].id
    data = _analysis(box_env["dir"])
    data["pages"][0]["page_no"] = 7
    (box_env["dir"] / "analysis.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    response = _apply(client, batch_id, 1, [[0.0, 0.1, 1.0, 0.2]])
    assert response.status_code == 400
    assert "切块结果" in response.json()["message"]


def test_apply_boxes_rejects_while_cutting(client, db_session, box_env):
    """正在切题时不许改 —— 否则两套重建会互相覆盖。"""

    batch_id = box_env["batch"].id
    batch = db_session.query(MistakeBatch).filter(MistakeBatch.id == batch_id).one()
    batch.status = "cutting"
    db_session.commit()

    assert _apply(client, batch_id, 1, [[0.0, 0.1, 1.0, 0.2]]).status_code == 409


def test_apply_boxes_rejects_missing_batch(client):
    assert _apply(client, 999999, 1, [[0.0, 0.1, 1.0, 0.2]]).status_code == 404
