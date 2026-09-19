"""错题工作台「删除题块」接口测试（``POST /api/mistakes/batches/{id}/pages/{n}/hidden``）。

删除是**最容易被误解成生效了**的一步操作：右侧卡片当场消失，看起来对了；但整页记录是
「每次改动都删旧插新」重建出来的，名单不落盘的话，下一次画框/合并/重切它就回来了，
而用户只会得出「删除按钮没用」的结论。所以这里锁死的是：

1. 删除后记录真的少了，且名单以**几何**落盘（不是块序号）；
2. **重建不复活** —— 紧接着调一次框选（触发整页重建），被删的块不能回来；
3. 恢复（提交空数组）＝ 完全还原；
4. 删一道**已合并**的题＝按外接矩形整组一起删；
5. 命中判定不能误伤：交叠不够、或把一个小碎片并进了更大的块，都必须判「不命中」。
"""

import json

import pytest
from PIL import Image, ImageDraw

from mathbank.database import MistakeBatch, MistakeRecord, Student

PAGE_W, PAGE_H = 600, 1200

#: 双栏页：左栏两块（0/2）、右栏一块（1）。块 0 与块 1 纵向重叠 —— 「同一道题被分栏
#: 切成左右两半」的典型形态；块 2 在页面下方，独占一道题。
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


def _rect(*indices):
    """把若干块的外接矩形写成名单条目（单块就是它自己那一块）。"""

    return [
        min(BLOCKS[i]["x_start"] for i in indices),
        min(BLOCKS[i]["y_start"] for i in indices),
        max(BLOCKS[i]["x_end"] for i in indices),
        max(BLOCKS[i]["y_end"] for i in indices),
    ]


def _hide(client, batch_id, page_no, rects):
    import main

    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/hidden",
        json={"rects": rects},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )


def _boxes(client, batch_id, page_no, boxes):
    """框选接口：这里只把它当「触发整页重建」的第二种入口用。"""

    import main

    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/blocks",
        json={"boxes": boxes},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )


def _merge(client, batch_id, page_no, merges):
    import main

    return client.post(
        f"/api/mistakes/batches/{batch_id}/pages/{page_no}/merges",
        json={"merges": merges},
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )


@pytest.fixture
def hide_env(tmp_path, monkeypatch, db_session):
    import main

    monkeypatch.setattr(main, "UPLOAD_DIR", str(tmp_path / "uploads"))

    student = Student(name="删除测试", grade="高一")
    db_session.add(student)
    db_session.commit()

    batch = MistakeBatch(
        student_id=student.id,
        subject="physics",
        title="删除测试",
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
                "version": 5,
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
                        "hidden_blocks": [],
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

    # 块 1 预置作答状态：确认删除不会把它带到别的记录上，撤销也不是靠这条
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


def _analysis(batch_dir):
    return json.loads((batch_dir / "analysis.json").read_text(encoding="utf-8"))


def test_delete_block_removes_record_and_persists_rect(client, db_session, hide_env):
    """删一块：记录少一条、名单按几何落盘、块图留着（恢复时不必重裁）。"""

    batch_id = hide_env["batch"].id
    response = _hide(client, batch_id, 1, [_rect(1)])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["hidden_count"] == 1
    assert payload["block_count"] == 2

    records = _page_records(db_session, batch_id)
    assert [record.block_index for record in records] == [0, 2]

    entry = _analysis(hide_env["dir"])["pages"][0]
    assert entry["hidden_blocks"] == [_rect(1)]
    # 自动基线一个不少 —— 删除是叠在上面的一层遮蔽，不是破坏性改写
    assert len(entry["blocks"]) == 3
    # 块图不删：恢复要用它，且重裁一张没有意义
    assert "p001_b01.png" in sorted(p.name for p in (hide_env["dir"] / "blocks").glob("*.png"))


def test_delete_survives_rebuild(client, db_session, hide_env):
    """**核心回归**：删完之后紧接着一次整页重建，被删的块不能又冒出来。"""

    batch_id = hide_env["batch"].id
    assert _hide(client, batch_id, 1, [_rect(1)]).status_code == 200
    assert [r.block_index for r in _page_records(db_session, batch_id)] == [0, 2]

    # 任意一次会触发整页重建的写操作（这里用「清空人工框」）
    assert _boxes(client, batch_id, 1, []).status_code == 200
    assert [r.block_index for r in _page_records(db_session, batch_id)] == [0, 2]

    # 合并也一样会重建：把剩下的两块合掉，被删的那块仍然不在
    assert _merge(client, batch_id, 1, [{"rects": [_rect(0), _rect(2)]}]).status_code == 200
    assert [r.block_index for r in _page_records(db_session, batch_id)] == [0]
    assert _analysis(hide_env["dir"])["pages"][0]["hidden_blocks"] == [_rect(1)]

    # 恢复时「合并 + 被删的那块」一起回来
    assert _hide(client, batch_id, 1, []).json()["block_count"] == 2
    assert [r.block_index for r in _page_records(db_session, batch_id)] == [0, 1]


def test_restore_brings_everything_back(client, db_session, hide_env):
    """恢复＝提交空名单，记录与元数据完全还原。"""

    batch_id = hide_env["batch"].id
    assert _hide(client, batch_id, 1, [_rect(0), _rect(2)]).status_code == 200
    assert [r.block_index for r in _page_records(db_session, batch_id)] == [1]

    response = _hide(client, batch_id, 1, [])
    assert response.status_code == 200, response.text
    assert response.json()["hidden_count"] == 0
    assert response.json()["block_count"] == 3

    records = _page_records(db_session, batch_id)
    assert [record.block_index for record in records] == [0, 1, 2]
    assert _analysis(hide_env["dir"])["pages"][0]["hidden_blocks"] == []
    # 预置状态还在原记录上（删除/恢复都不该动别的记录）
    assert records[1].grad_status == "incorrect"


def test_delete_all_blocks_leaves_page_empty_then_restorable(client, db_session, hide_env):
    """整页删空是合法状态（不是错误），且还能一键恢复。"""

    batch_id = hide_env["batch"].id
    response = _hide(client, batch_id, 1, [_rect(0), _rect(1), _rect(2)])
    assert response.status_code == 200, response.text
    assert response.json()["block_count"] == 0
    assert _page_records(db_session, batch_id) == []

    assert _hide(client, batch_id, 1, []).json()["block_count"] == 3
    assert len(_page_records(db_session, batch_id)) == 3


def test_delete_merged_group_by_its_bounding_rect(client, db_session, hide_env):
    """删一道合并题：按外接矩形一次删干净整组，不用逐块点。"""

    batch_id = hide_env["batch"].id
    merged = _merge(client, batch_id, 1, [{"rects": [_rect(0), _rect(1)], "primary": 1}])
    assert merged.status_code == 200, merged.text
    assert len(_page_records(db_session, batch_id)) == 2  # 合并组 + 块 2

    response = _hide(client, batch_id, 1, [_rect(0, 1)])
    assert response.status_code == 200, response.text
    assert response.json()["block_count"] == 1
    remaining = _page_records(db_session, batch_id)
    assert [record.block_index for record in remaining] == [2]
    # 合并组仍留在元数据里：恢复时它应该还是「一道合并题」，而不是散成两块
    entry = _analysis(hide_env["dir"])["pages"][0]
    assert len(entry["manual_merges"]) == 1

    assert _hide(client, batch_id, 1, []).json()["block_count"] == 2
    records = _page_records(db_session, batch_id)
    assert [record.merge_id for record in records].count(entry["manual_merges"][0]["id"]) == 1


def test_stale_rect_that_matches_nothing_is_dropped(client, db_session, hide_env):
    """命中不了任何块的矩形不落盘：否则它会一直躺着，哪天几何撞上就凭空少一道题。"""

    batch_id = hide_env["batch"].id
    # 页脚空白处的一小块，跟三个块都不搭界
    response = _hide(client, batch_id, 1, [[0.2, 0.95, 0.3, 0.97]])
    assert response.status_code == 200, response.text
    assert response.json()["hidden_count"] == 0
    assert len(_page_records(db_session, batch_id)) == 3
    assert _analysis(hide_env["dir"])["pages"][0]["hidden_blocks"] == []


def test_detail_returns_hidden_blocks(client, db_session, hide_env):
    """批次详情要带回名单，否则前端既显示不出「已删除 N 块」，也给不出恢复入口。"""

    batch_id = hide_env["batch"].id
    assert _hide(client, batch_id, 1, [_rect(2)]).status_code == 200

    import main

    response = client.get(
        f"/api/mistakes/batches/{batch_id}",
        headers={"X-Local-Token": main.LOCAL_TOKEN},
    )
    assert response.status_code == 200, response.text
    page = response.json()["pages"][0]
    assert page["hidden_blocks"] == [_rect(2)]
    assert [record["block_index"] for record in response.json()["records"]] == [0, 1]


# ---------------------------------------------------------------- 纯函数：命中判定


def test_hidden_matching_tolerates_recut_but_not_overreach():
    """命中判定的三条边界：位移能跟上、擦边不误伤、反向不吞整题。"""

    import main

    hidden = [0.0, 0.05, 0.49, 0.14]

    # 1) 完全一致 → 命中
    assert main._hidden_hit(hidden, [0.0, 0.05, 0.49, 0.14])
    # 2) 重切把这一块拆成上下两半 → 两半都在名单里，各自命中
    assert main._hidden_hit(hidden, [0.0, 0.05, 0.49, 0.095])
    assert main._hidden_hit(hidden, [0.0, 0.095, 0.49, 0.14])
    # 3) 边界小幅位移（重切常见）→ 仍然命中
    assert main._hidden_hit(hidden, [0.0, 0.055, 0.49, 0.135])
    # 4) 只擦个边的大块 → 不命中
    assert not main._hidden_hit(hidden, [0.0, 0.12, 0.49, 0.40])
    # 5) 反向：小碎片被并进了更大的块 → 不命中（宁可让用户再删一次，也不能吞掉一整题）
    assert not main._hidden_hit([0.1, 0.06, 0.2, 0.08], [0.0, 0.05, 0.49, 0.30])
    # 6) 完全不相交
    assert not main._hidden_hit(hidden, [0.0, 0.60, 0.49, 0.70])
