"""错题工作台「跨页合并」接口测试（``POST /api/mistakes/batches/{id}/cross-merges``）。

真实场景：一道题的**题干**落在上一页最后一块，**选项与配图**落在下一页第一块。
页图是两张分开的 PNG，页内相对坐标各从 0 开始 —— 页 9 的 0.891 与页 10 的 0.039
放在一个列表里，后端没有任何办法判断谁属于谁。所以跨页组的成员必须带页号，组也
只能挂在 analysis.json 的顶层。

这里锁死六件事：

1. 成员按 ``(页号, 矩形)`` 定位，跨页拼图读出的是两页各自的块图；
2. 记录只落在 ``primary`` 成员所在页，其余页上的那一半**被占用**、不再单独出记录
   （否则同一道题在库里有两份，错题本与统计都会翻倍）；
3. 合成图是两页块图的纵向拼接（宽度对齐后高度相加）；
4. 状态按 ``primary`` 继承；
5. 拆分（提交空列表）能完全还原，且不留孤儿合成图；
6. 成员全在同一页的组被拒绝（那种组属于 ``page.manual_merges``），且跨页组不会被
   页面级的框选写入口抹掉。
"""

import json

import pytest
from PIL import Image, ImageDraw

from mathbank.database import MistakeBatch, MistakeRecord, Student

PAGE_W, PAGE_H = 600, 1200

#: 第 1 页：一道完整的题（块 0）＋ 跨页题的**题干**（块 1，贴着页底）。
PAGE_1_BLOCKS = [
    {"block_index": 0, "y_start": 0.10, "y_end": 0.20, "x_start": 0.0, "x_end": 1.0, "column_index": 1},
    {"block_index": 1, "y_start": 0.85, "y_end": 0.95, "x_start": 0.0, "x_end": 1.0, "column_index": 1},
]
#: 第 2 页：跨页题的**选项**（块 0，贴着页顶，故意窄一圈以验证宽度对齐）＋ 另一道题。
PAGE_2_BLOCKS = [
    {"block_index": 0, "y_start": 0.05, "y_end": 0.15, "x_start": 0.10, "x_end": 0.90, "column_index": 1},
    {"block_index": 1, "y_start": 0.40, "y_end": 0.50, "x_start": 0.0, "x_end": 1.0, "column_index": 1},
]


def _write_page_image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(image)
    for index in range(4):
        top = 80 + index * 240
        draw.rectangle([40, top, PAGE_W - 40, top + 60], fill=0)
    image.save(path, format="PNG")


def _page_entry(page_no, blocks):
    return {
        "page_no": page_no,
        "width": PAGE_W,
        "height": PAGE_H,
        "layout": {
            "mode": "single",
            "boundary": 1.0,
            "source": "text_layer",
            "confidence": 1.0,
        },
        "column_boundary": 1.0,
        "blocks": blocks,
        "gap_candidates": [],
        "snap_points": [],
        "column_snap_points": {},
        "column_gap_candidates": {},
        "manual_boxes": [],
        "manual_merges": [],
        "hidden_blocks": [],
    }


@pytest.fixture
def cross_env(tmp_path, monkeypatch, db_session):
    import main

    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))

    student = Student(name="跨页测试", grade="高一")
    db_session.add(student)
    db_session.commit()

    batch = MistakeBatch(
        student_id=student.id,
        subject="physics",
        title="跨页测试",
        page_count=2,
        status="reviewing",
    )
    db_session.add(batch)
    db_session.commit()

    batch_dir = tmp_path / "uploads" / "mistakes" / str(batch.id)
    (batch_dir / "blocks").mkdir(parents=True, exist_ok=True)
    _write_page_image(batch_dir / "pages" / "page_1.png")
    _write_page_image(batch_dir / "pages" / "page_2.png")
    (batch_dir / "analysis.json").write_text(
        json.dumps(
            {
                "version": 6,
                "cross_page_merges": [],
                "pages": [
                    _page_entry(1, PAGE_1_BLOCKS),
                    _page_entry(2, PAGE_2_BLOCKS),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    for page_no, blocks in ((1, PAGE_1_BLOCKS), (2, PAGE_2_BLOCKS)):
        for block in blocks:
            db_session.add(
                MistakeRecord(
                    batch_id=batch.id,
                    student_id=student.id,
                    subject="physics",
                    page_no=page_no,
                    block_index=block["block_index"],
                    block_y_start=block["y_start"],
                    block_y_end=block["y_end"],
                    block_x_start=block["x_start"],
                    block_x_end=block["x_end"],
                    column_index=block["column_index"],
                    image_block=(
                        f"/static/test_uploads/mistakes/{batch.id}/blocks/"
                        f"p{page_no:03d}_b{block['block_index']:02d}.png"
                    ),
                    recognize_status="pending",
                    grad_status="unknown",
                )
            )
    db_session.commit()

    # 跨页题的题干那半预置作答状态：以它为准时状态必须跟过来
    stem = (
        db_session.query(MistakeRecord)
        .filter(
            MistakeRecord.batch_id == batch.id,
            MistakeRecord.page_no == 1,
            MistakeRecord.block_index == 1,
        )
        .one()
    )
    stem.grad_status = "incorrect"
    stem.error_reason = "位移方向判断错"
    db_session.commit()

    return {"batch": batch, "dir": batch_dir}


def _rect(page_no, block_index):
    blocks = PAGE_1_BLOCKS if page_no == 1 else PAGE_2_BLOCKS
    block = [item for item in blocks if item["block_index"] == block_index][0]
    return [
        block["x_start"],
        block["y_start"],
        block["x_end"],
        block["y_end"],
    ]


def _cross(client, batch_id, groups):
    import main

    return client.post(
        f"/api/mistakes/batches/{batch_id}/cross-merges",
        json={"cross_merges": groups},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )


def _stem_options_group(primary=0, merge_id="mcross01"):
    return {
        "id": merge_id,
        "direction": "v",
        "primary": primary,
        "members": [
            {"page_no": 1, "rect": _rect(1, 1)},
            {"page_no": 2, "rect": _rect(2, 0)},
        ],
    }


def _records(db_session, batch_id):
    return (
        db_session.query(MistakeRecord)
        .filter(MistakeRecord.batch_id == batch_id)
        .order_by(MistakeRecord.page_no.asc(), MistakeRecord.block_index.asc())
        .all()
    )


def _analysis(batch_dir):
    return json.loads((batch_dir / "analysis.json").read_text(encoding="utf-8"))


def test_cross_merge_builds_one_record_on_primary_page(client, db_session, cross_env):
    """题干在页 1、选项在页 2 → 记录落在页 1，页 2 那一半不再单独出记录。"""

    batch_id = cross_env["batch"].id
    response = _cross(client, batch_id, [_stem_options_group()])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["cross_merge_count"] == 1
    assert payload["dropped"] == 0
    assert payload["rebuilt_pages"] == [1, 2]

    records = _records(db_session, batch_id)
    # 4 块 → 3 条：页 1 的选项那半被并走，页 2 的块 0 被占用
    assert [(r.page_no, r.block_index) for r in records] == [(1, 0), (1, 1), (2, 1)]

    merged = [r for r in records if r.merge_id][0]
    assert merged.page_no == 1
    assert merged.block_index == 1
    assert merged.merged_block_count == 2
    # 块图跨页取：一半来自页 1，一半来自页 2
    assert json.loads(merged.block_images) == [
        f"/static/test_uploads/mistakes/{batch_id}/blocks/p001_b01.png",
        f"/static/test_uploads/mistakes/{batch_id}/blocks/p002_b00.png",
    ]
    assert merged.image_block.endswith(f"p001_m{merged.merge_id}.png")
    # 状态按 primary（题干的块 1）继承
    assert merged.grad_status == "incorrect"
    assert merged.error_reason == "位移方向判断错"
    # 被占用的页 2 块 0 还在库里留过状态吗？它不该有记录
    assert not [r for r in records if r.page_no == 2 and r.block_index == 0]


def test_cross_merge_composes_vertical_stack_of_two_pages(client, db_session, cross_env):
    """合成图＝两页块图纵向拼接：宽度取两者最大值，另一张等比缩放后高度相加。"""

    batch_id = cross_env["batch"].id
    response = _cross(client, batch_id, [_stem_options_group()])
    assert response.status_code == 200, response.text

    blocks_dir = cross_env["dir"] / "blocks"
    stem = Image.open(blocks_dir / "p001_b01.png")
    options = Image.open(blocks_dir / "p002_b00.png")
    assert (stem.width, options.width) == (600, 480)

    merged = [r for r in _records(db_session, batch_id) if r.merge_id][0]
    composed = Image.open(blocks_dir / f"p001_m{merged.merge_id}.png")
    expected_width = 600
    expected_height = stem.height + round(options.height * expected_width / options.width)
    assert composed.size == (expected_width, expected_height), (
        f"合成图应是把 480 宽的选项等比放到 600 后竖着拼：{composed.size}"
    )


def test_cross_merge_primary_on_second_page_moves_record(
    client, db_session, cross_env
):
    """以页 2 那半为准 → 记录落到页 2，页 1 上的题干那半被占用。"""

    batch_id = cross_env["batch"].id
    response = _cross(client, batch_id, [_stem_options_group(primary=1)])
    assert response.status_code == 200, response.text

    records = _records(db_session, batch_id)
    assert [(r.page_no, r.block_index) for r in records] == [(1, 0), (2, 0), (2, 1)]
    merged = [r for r in records if r.merge_id][0]
    assert merged.page_no == 2
    assert merged.block_index == 0
    # primary 换了页，状态也从那一页的块继承（页 2 的块 0 是 unknown）
    assert merged.grad_status == "unknown"
    assert merged.image_block.endswith(f"p002_m{merged.merge_id}.png")


def test_cross_merge_split_restores_both_pages(client, db_session, cross_env):
    """提交空列表＝拆回：两页各恢复自己的记录，合成图不留在目录里。"""

    batch_id = cross_env["batch"].id
    assert _cross(client, batch_id, [_stem_options_group()]).status_code == 200
    merged = [r for r in _records(db_session, batch_id) if r.merge_id][0]
    composed = cross_env["dir"] / "blocks" / f"p001_m{merged.merge_id}.png"
    assert composed.is_file()

    response = _cross(client, batch_id, [])
    assert response.status_code == 200, response.text
    assert response.json()["cross_merge_count"] == 0

    records = _records(db_session, batch_id)
    assert [(r.page_no, r.block_index) for r in records] == [
        (1, 0),
        (1, 1),
        (2, 0),
        (2, 1),
    ]
    assert not any(r.merge_id for r in records)
    assert not composed.is_file(), "拆分后孤儿合成图必须清掉"
    assert _analysis(cross_env["dir"])["cross_page_merges"] == []
    # 被占用的那半重新出现后，它的块图仍在（重建时重裁）
    assert (cross_env["dir"] / "blocks" / "p002_b00.png").is_file()


def test_cross_merge_is_idempotent(client, db_session, cross_env):
    """同一组提交两次不该多出一条记录，也不该把组复制一份。"""

    batch_id = cross_env["batch"].id
    group = _stem_options_group()
    assert _cross(client, batch_id, [group]).status_code == 200
    second = _cross(client, batch_id, [group])
    assert second.status_code == 200, second.text
    assert second.json()["cross_merge_count"] == 1
    assert [(r.page_no, r.block_index) for r in _records(db_session, batch_id)] == [
        (1, 0),
        (1, 1),
        (2, 1),
    ]
    assert len(_analysis(cross_env["dir"])["cross_page_merges"]) == 1


def test_same_page_members_are_rejected(client, db_session, cross_env):
    """成员全在同一页的组被丢掉 —— 那种组属于 page.manual_merges。"""

    batch_id = cross_env["batch"].id
    response = _cross(
        client,
        batch_id,
        [
            {
                "id": "msamepage",
                "primary": 0,
                "members": [
                    {"page_no": 1, "rect": _rect(1, 0)},
                    {"page_no": 1, "rect": _rect(1, 1)},
                ],
            }
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["cross_merge_count"] == 0
    # 两块原样留着，没有被合并
    assert [(r.page_no, r.block_index) for r in _records(db_session, batch_id)] == [
        (1, 0),
        (1, 1),
        (2, 0),
        (2, 1),
    ]


def test_unmatchable_group_is_dropped_and_reported(client, db_session, cross_env):
    """成员在当前几何下找不到块 → 整组作废，并把 dropped 报给前端。"""

    batch_id = cross_env["batch"].id
    response = _cross(
        client,
        batch_id,
        [
            {
                "id": "mghost",
                # 页 2 上根本没有这个位置的块
                "members": [
                    {"page_no": 1, "rect": _rect(1, 1)},
                    {"page_no": 2, "rect": [0.0, 0.80, 1.0, 0.90]},
                ],
            }
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["cross_merge_count"] == 0
    assert response.json()["dropped"] == 1
    assert [(r.page_no, r.block_index) for r in _records(db_session, batch_id)] == [
        (1, 0),
        (1, 1),
        (2, 0),
        (2, 1),
    ]
    assert _analysis(cross_env["dir"])["cross_page_merges"] == []


def test_page_level_write_keeps_cross_groups(client, db_session, cross_env):
    """页面级的框选写入口不该碰跨页组 —— 两者在文件的不同层。"""

    import main

    batch_id = cross_env["batch"].id
    assert _cross(client, batch_id, [_stem_options_group()]).status_code == 200

    response = client.post(
        f"/api/mistakes/batches/{batch_id}/pages/2/blocks",
        json={"boxes": [[0.0, 0.60, 1.0, 0.70]]},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )
    assert response.status_code == 200, response.text

    stored = _analysis(cross_env["dir"])
    # 框选改的是页 2 的 manual_boxes，跨页组必须原样还在
    assert len(stored["cross_page_merges"]) == 1
    assert stored["cross_page_merges"][0]["members"][1]["page_no"] == 2
    assert stored["version"] == 6
    merged = [r for r in _records(db_session, batch_id) if r.merge_id]
    assert len(merged) == 1 and merged[0].merged_block_count == 2


def test_host_page_rebuild_refreshes_the_other_page_block_image(
    client, db_session, cross_env
):
    """宿主页**单页**重建时，对页的块图必须按当前几何补裁。

    ``_rebuild_mistake_page_records`` 只裁本页的块，而跨页拼图要读对页的
    ``p002_b00.png`` —— 那份文件可能是上一版几何留下的，或者（页 2 重切过）干脆没了。
    不先补齐，拼图会失败并被兜底成「保留成员图」：用户看到的是「合并了但图没拼上」，
    控制台只有一行 print，最难查的一类。
    """

    import main

    batch_id = cross_env["batch"].id
    assert _cross(client, batch_id, [_stem_options_group()]).status_code == 200

    # 模拟「页 2 的块图丢了」：宿主页重建时若不补裁，拼图会静默退化成半张图
    (cross_env["dir"] / "blocks" / "p002_b00.png").unlink()

    response = client.post(
        f"/api/mistakes/batches/{batch_id}/pages/1/blocks",
        json={"boxes": [[0.0, 0.30, 1.0, 0.40]]},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )
    assert response.status_code == 200, response.text

    blocks_dir = cross_env["dir"] / "blocks"
    assert (blocks_dir / "p002_b00.png").is_file(), "宿主页重建时应把对页块图补裁回来"

    merged = [r for r in _records(db_session, batch_id) if r.merge_id]
    assert len(merged) == 1, "跨页合并记录不该在单页重建里消失"
    assert merged[0].merged_block_count == 2
    assert f"_m{merged[0].merge_id}" in merged[0].image_block, (
        "合成图缺失 → 说明拼图失败了，退化成了单块图"
    )

    names = [url.rsplit("/", 1)[-1] for url in json.loads(merged[0].block_images)]
    sizes = [Image.open(blocks_dir / name).size for name in names]
    composed = Image.open(blocks_dir / f"p001_m{merged[0].merge_id}.png")
    width = max(item[0] for item in sizes)
    height = sum(round(item[1] * width / item[0]) for item in sizes)
    assert composed.size == (width, height), "合成图仍应是两页块图的纵向拼接"


def test_batch_detail_exposes_cross_page_merges(client, cross_env):
    """批次详情要下发跨页组，前端才有「跨页 N 块」的标记与拆分入口。"""

    import main

    batch_id = cross_env["batch"].id
    assert _cross(client, batch_id, [_stem_options_group()]).status_code == 200

    response = client.get(
        f"/api/mistakes/batches/{batch_id}",
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )
    assert response.status_code == 200, response.text
    groups = response.json()["cross_page_merges"]
    assert len(groups) == 1
    assert [member["page_no"] for member in groups[0]["members"]] == [1, 2]
    assert groups[0]["direction"] == "v"


def test_missing_page_is_rejected_before_any_write(client, db_session, cross_env):
    """页图不存在时先拒绝，不要把顶层元数据写进去。"""

    batch_id = cross_env["batch"].id
    (cross_env["dir"] / "pages" / "page_2.png").unlink()
    response = _cross(client, batch_id, [_stem_options_group()])
    assert response.status_code == 400
    assert "页图不存在" in response.json()["message"]
    assert _analysis(cross_env["dir"])["cross_page_merges"] == []
    assert len(_records(db_session, batch_id)) == 4
