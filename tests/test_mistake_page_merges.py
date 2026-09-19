"""错题工作台「人工合并」接口测试（``POST /api/mistakes/batches/{id}/pages/{n}/merges``）。

一道题被分栏切成左右两块时，用户把它们并为一条记录。这里锁死四件事：

1. 合并后**记录数减一**、块图是「成员各一张 + 合成一张」，合成图尺寸随方向变化；
2. 合并组用**几何**落盘（不是块序号）—— 重切导致块序号位移后仍能找回成员；
3. 状态按 ``primary`` 继承（两块批改状态冲突时以用户选的那块为准）；
4. 拆分（提交空列表）能完全还原，且不留孤儿合成图。

合成图与真实卷同源：页图是画出来的双栏 PNG，块图由 ``crop_region`` 真裁。
"""

import json

import pytest
from PIL import Image, ImageDraw

from mathbank.database import MistakeBatch, MistakeRecord, Student

PAGE_W, PAGE_H = 600, 1200

#: 双栏页：左栏两块（0/2）、右栏一块（1）。块 0 与块 1 纵向重叠 —— 就是「同一道
#: 题被分栏切成左右两半」的典型形态，方向应自动判为横向拼。
BLOCKS = [
    {"block_index": 0, "y_start": 0.05, "y_end": 0.14, "x_start": 0.0, "x_end": 0.49, "column_index": 1},
    {"block_index": 1, "y_start": 0.08, "y_end": 0.17, "x_start": 0.51, "x_end": 1.0, "column_index": 2},
    {"block_index": 2, "y_start": 0.60, "y_end": 0.70, "x_start": 0.0, "x_end": 0.49, "column_index": 1},
]


def _write_page_image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(image)
    for index in range(4):
        top = 80 + index * 240
        draw.rectangle([40, top, 260, top + 60], fill=0)
        draw.rectangle([320, top, PAGE_W - 40, top + 60], fill=0)
    image.save(path, format="PNG")


def _merge(client, batch_id, page_no, merges):
    import main

    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/merges",
        json={"merges": merges},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )


def _rects(*indices):
    return [
        [BLOCKS[i]["x_start"], BLOCKS[i]["y_start"], BLOCKS[i]["x_end"], BLOCKS[i]["y_end"]]
        for i in indices
    ]


@pytest.fixture
def merge_env(tmp_path, monkeypatch, db_session):
    import main

    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))

    student = Student(name="合并测试", grade="高一")
    db_session.add(student)
    db_session.commit()

    batch = MistakeBatch(
        student_id=student.id,
        subject="physics",
        title="合并测试",
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
                "version": 4,
                "pages": [
                    {
                        "page_no": 1,
                        "width": PAGE_W,
                        "height": PAGE_H,
                        "layout": {
                            "mode": "double",
                            "boundary": 0.5,
                            "source": "visual",
                            "confidence": 1.0,
                        },
                        "column_boundary": 0.5,
                        "blocks": BLOCKS,
                        "gap_candidates": [],
                        "snap_points": [],
                        "column_snap_points": {},
                        "column_gap_candidates": {},
                        "manual_boxes": [],
                        "manual_merges": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
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
                image_block=(
                    f"/static/test_uploads/mistakes/{batch.id}/blocks/"
                    f"p001_b{block['block_index']:02d}.png"
                ),
                recognize_status="pending",
                grad_status="unknown",
            )
        )
    db_session.commit()

    # 块 1（右栏那半）预置作答状态：合并时若以它为准，状态必须跟过来
    right = (
        db_session.query(MistakeRecord)
        .filter(MistakeRecord.batch_id == batch.id, MistakeRecord.block_index == 1)
        .one()
    )
    right.grad_status = "incorrect"
    right.error_reason = "受力分析漏力"
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


def test_merge_two_columns_into_one_record(client, db_session, merge_env):
    """左右两半 → 一条记录：合成图、成员图留档、状态按 primary 继承。"""

    batch_id = merge_env["batch"].id
    response = _merge(
        client, batch_id, 1, [{"rects": _rects(0, 1), "primary": 1}]
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["merge_count"] == 1
    assert payload["block_count"] == 2  # 合并组 + 未被合并的块 2
    # 方向自动判定：两块纵向重叠 → 横向拼
    assert payload["manual_merges"][0]["direction"] == "h"

    records = _page_records(db_session, batch_id)
    assert len(records) == 2
    merged = records[0]
    assert merged.merge_id == payload["manual_merges"][0]["id"]
    assert merged.merged_block_count == 2
    assert len(merged.block_image_list) == 2
    # 跨栏合并后不再属于某一栏
    assert merged.column_index == 0
    # 状态以 primary（右栏那块）为准
    assert merged.grad_status == "incorrect"
    assert merged.error_reason == "受力分析漏力"

    merge_id = merged.merge_id
    images = _block_images(merge_env["dir"])
    assert f"p001_m{merge_id}.png" in images
    assert {"p001_b00.png", "p001_b01.png"} <= set(images)
    # 合成图是横向拼：宽 ≈ 两块之和，高 ≈ 单块高
    with Image.open(merge_env["dir"] / "blocks" / f"p001_m{merge_id}.png") as composed, Image.open(
        merge_env["dir"] / "blocks" / "p001_b00.png"
    ) as first:
        assert composed.width > first.width * 1.5
        assert composed.height <= max(first.height, 1) + 4


def test_merge_persists_geometry_and_survives_recut(client, db_session, merge_env):
    """落盘的是几何而非块序号：块序号整体位移后仍能按重叠找回成员。"""

    batch_id = merge_env["batch"].id
    response = _merge(client, batch_id, 1, [{"rects": _rects(0, 1), "direction": "v"}])
    assert response.status_code == 200, response.text
    entry = _analysis(merge_env["dir"])["pages"][0]
    assert len(entry["manual_merges"]) == 1
    assert entry["manual_merges"][0]["rects"] == _rects(0, 1)
    # 自动基线一个不少（合并只是叠在上面的一层，不做破坏性改写）
    assert len(entry["blocks"]) == 3

    # 模拟重切：块序号整体 +1（插入了一个页眉块），几何不变
    import main

    entry["blocks"] = [
        {"block_index": 0, "y_start": 0.01, "y_end": 0.03, "x_start": 0.0, "x_end": 1.0, "column_index": 0},
        *[
            {**block, "block_index": block["block_index"] + 1}
            for block in BLOCKS
        ],
    ]
    (merge_env["dir"] / "analysis.json").write_text(
        json.dumps({"version": 4, "pages": [entry]}, ensure_ascii=False),
        encoding="utf-8",
    )
    items = main._apply_manual_merges(
        main._blocks_from_meta(entry, 1),
        main._normalize_mistake_merges(entry.get("manual_merges")),
    )
    merged = [item for item in items if item["kind"] == "merged"]
    assert len(merged) == 1
    # 成员跟着几何走到了新序号 1 / 2
    assert [block.block_index for block in merged[0]["members"]] == [1, 2]


def test_split_restores_blocks_and_cleans_composed_image(client, db_session, merge_env):
    """拆分（提交空列表）→ 记录与块图完全还原，合成图不留孤儿。"""

    batch_id = merge_env["batch"].id
    assert _merge(client, batch_id, 1, [{"rects": _rects(0, 1)}]).status_code == 200
    assert len(_page_records(db_session, batch_id)) == 2

    response = _merge(client, batch_id, 1, [])
    assert response.status_code == 200, response.text
    assert response.json()["block_count"] == 3

    records = _page_records(db_session, batch_id)
    assert [record.block_index for record in records] == [0, 1, 2]
    assert all(record.merge_id == "" for record in records)
    assert all(record.merged_block_count == 1 for record in records)
    assert _block_images(merge_env["dir"]) == [
        "p001_b00.png",
        "p001_b01.png",
        "p001_b02.png",
    ]
    assert _analysis(merge_env["dir"])["pages"][0]["manual_merges"] == []


def test_one_block_cannot_belong_to_two_groups(client, db_session, merge_env):
    """两个组抢同一块 → 后一个组整体作废（宁可不合并，也不拼出错题）。"""

    batch_id = merge_env["batch"].id
    response = _merge(
        client,
        batch_id,
        1,
        [
            {"id": "maaaaaaaa", "rects": _rects(0, 1)},
            {"id": "mbbbbbbbb", "rects": _rects(1, 2)},
        ],
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["merge_count"] == 1  # 只有真正成组的那个会落盘
    # 但只有第一个真正成组：块 1 已被占用
    records = _page_records(db_session, batch_id)
    assert [record.merge_id for record in records].count("mbbbbbbbb") == 0
    assert [record.merge_id for record in records].count("maaaaaaaa") == 1
    assert len(records) == 2  # 合并组(0,1) + 块 2


def test_detail_returns_manual_merges(client, db_session, merge_env):
    """批次详情要带回合并组，否则前端无从显示「已合并 N 块」与拆分按钮。"""

    batch_id = merge_env["batch"].id
    assert _merge(client, batch_id, 1, [{"rects": _rects(0, 1)}]).status_code == 200

    import main

    response = client.get(
        f"/api/mistakes/batches/{batch_id}",
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )
    assert response.status_code == 200, response.text
    page = response.json()["pages"][0]
    assert len(page["manual_merges"]) == 1
    assert page["manual_merges"][0]["rects"] == _rects(0, 1)
    merged_records = [
        item for item in response.json()["records"] if item["merge_id"]
    ]
    assert len(merged_records) == 1
    assert merged_records[0]["merged_block_count"] == 2
    assert len(merged_records[0]["block_images"]) == 2
