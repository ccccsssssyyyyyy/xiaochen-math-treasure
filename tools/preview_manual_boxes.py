"""人工框选在真实卷上的只读预演。

不写库、不落盘、不动 records —— 只把 ``analysis.json`` 里真实的自动切块读出来，
按用户在页面上画一个框，看 ``merge_manual_boxes`` 算出什么，回答两个问题：

1. 现在到底切得多细（哪些页、哪一栏有多少「<4% 页高」的碎块）；
2. 把那一栏一把框住后，碎块能不能并成一块，而**没被框到的栏是否原样不动**。

用法: python tools/preview_manual_boxes.py [批次号]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathbank.page_block_split import (  # noqa: E402
    MANUAL_BOX_MIN_SIZE,
    BlockRegion,
    merge_manual_boxes,
)

ROOT = Path(__file__).resolve().parent.parent
BATCH_DIR = ROOT / "static" / "uploads" / "mistakes"
#: 低于这个高度的块算「碎块」—— 一页 4% 大约就是一行的量级
TINY_RATIO = 0.04
COLUMN_LABELS = {0: "整页", 1: "左栏", 2: "右栏"}
#: 与 static/js/mistake.js 的 BOX_SNAP_TOLERANCE 同值：框边离分栏线多近就吸附过去
SNAP_TOLERANCE = 0.02


def load_pages(batch_id: int) -> list[dict]:
    """读 analysis.json 的 pages。

    落盘有 v1/v2/v3 三代，``pages`` 一律是列表（v3 只是每页多了 ``manual_boxes``）。
    """
    path = BATCH_DIR / str(batch_id) / "analysis.json"
    if not path.is_file():
        raise SystemExit(f"找不到 {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = data.get("pages") or []
    if not isinstance(pages, list):
        raise SystemExit(f"{path} 的 pages 不是列表，结构已变：{type(pages).__name__}")
    return pages


def blocks_of(page: dict) -> list[BlockRegion]:
    return [
        BlockRegion(
            page_no=page["page_no"],
            block_index=item["block_index"],
            y_start=item["y_start"],
            y_end=item["y_end"],
            x_start=item["x_start"],
            x_end=item["x_end"],
            column_index=item.get("column_index", 0),
        )
        for item in page["blocks"]
    ]


def is_tiny(block: BlockRegion) -> bool:
    return (block.y_end - block.y_start) < TINY_RATIO


def describe(title: str, blocks: list[BlockRegion]) -> None:
    tiny = sum(1 for block in blocks if is_tiny(block))
    print(f"{title}：{len(blocks)} 块，其中碎块 {tiny} 个")
    for block in blocks:
        height = (block.y_end - block.y_start) * 100
        mark = "  ← 碎块" if is_tiny(block) else ""
        print(
            f"    #{block.block_index:<3} x[{block.x_start:.3f},{block.x_end:.3f}] "
            f"y[{block.y_start:.4f},{block.y_end:.4f}] 高 {height:5.2f}%{mark}"
        )


def worst_bucket(page: dict) -> tuple[int, list[BlockRegion]]:
    """这一页碎块最多的那一栏 —— 双栏页上「切太细」几乎总集中在其中一栏。"""
    buckets: dict[int, list[BlockRegion]] = {}
    for block in blocks_of(page):
        if is_tiny(block):
            buckets.setdefault(block.column_index, []).append(block)
    if not buckets:
        return 0, []
    column_no = max(buckets, key=lambda key: len(buckets[key]))
    return column_no, buckets[column_no]


def snap_to_column(box: list[float], boundary: float | None) -> list[float]:
    """框边吸附到分栏线（对应前端 snapBoxToColumn）。"""
    if boundary is None or not (0.0 < boundary < 1.0):
        return list(box)
    snapped = list(box)
    if abs(snapped[0] - boundary) <= SNAP_TOLERANCE:
        snapped[0] = boundary
    if abs(snapped[2] - boundary) <= SNAP_TOLERANCE:
        snapped[2] = boundary
    return snapped


def column_state(raw: list[BlockRegion], merged: list[BlockRegion], column_no: int) -> tuple[int, int]:
    """某一栏在合并前后的块数（跨栏页整页一等，返回 (前, 后)）。"""
    before = [b for b in raw if column_no == 0 or b.column_index == column_no]
    after = [b for b in merged if column_no == 0 or b.column_index == column_no]
    return len(before), len(after)


def main() -> int:
    batch_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    pages = load_pages(batch_id)
    total = sum(len(page.get("blocks", [])) for page in pages)
    total_tiny = sum(
        1 for page in pages for b in page.get("blocks", [])
        if (b["y_end"] - b["y_start"]) < TINY_RATIO
    )
    print(f"批次 {batch_id}：{len(pages)} 页 / {total} 块，碎块（<{TINY_RATIO:.0%} 页高）{total_tiny} 个")

    ranked = sorted(
        (
            (sum(1 for b in page.get("blocks", []) if (b["y_end"] - b["y_start"]) < TINY_RATIO), page)
            for page in pages
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    print("碎块最多的 5 页：" + "、".join(f"第 {p['page_no']} 页 {n} 个" for n, p in ranked[:5]))

    tiny_count, page = ranked[0]
    if not tiny_count:
        print("\n没有碎块，这一批不需要框选")
        return 0

    page_no = page["page_no"]
    raw = blocks_of(page)
    boundary = page.get("column_boundary")
    column_no, bucket = worst_bucket(page)
    label = COLUMN_LABELS.get(column_no, "?")
    print(f"\n最严重的一页：第 {page_no} 页（{tiny_count} 个碎块），集中在{label}\n")
    describe(f"第 {page_no} 页 · 现状（自动切块）", raw)

    # 用户真实的手势：从上到下把这一栏的碎块一把框住，而不是逐个碎片单独处理
    x0 = max(0.0, min(b.x_start for b in bucket) - 0.01)
    x1 = min(1.0, max(b.x_end for b in bucket) + 0.01)
    y0 = max(0.0, min(b.y_start for b in bucket) - 0.004)
    y1 = min(1.0, max(b.y_end for b in bucket) + 0.004)
    hand_drawn = [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]
    box = [round(v, 6) for v in snap_to_column(hand_drawn, boundary)]
    print(f"\n在{label}画一个框，把 {len(bucket)} 个碎块一起框住：")
    print(f"    手画出来的（含 1% 溢出）{hand_drawn}")
    print(f"    吸附到分栏线之后      {box}")
    assert min(box[2] - box[0], box[3] - box[1]) >= MANUAL_BOX_MIN_SIZE, "框小于最小边长"

    merged = merge_manual_boxes(raw, [box], page_no=page_no, column_boundary=boundary)
    print()
    describe(f"第 {page_no} 页 · 框选并块后", merged)

    other_raw, other_merged = column_state(raw, merged, 1 if column_no == 2 else 2)
    print(f"\n没被框到的另一栏：{other_raw} 块 → {other_merged} 块 "
          f"（{'原样保留 ✓' if other_raw == other_merged else '被改动了 ✗'}）")
    print(f"被框住的 {len(bucket)} 个碎块 → 合并成 1 块")

    # 反向验证：不吸附会怎样。这是真实踩到的隐患，别让它悄悄回来。
    naive = merge_manual_boxes(raw, [hand_drawn], page_no=page_no, column_boundary=boundary)
    naive_before, naive_after = column_state(raw, naive, 1 if column_no == 2 else 2)
    print("\n对照实验 —— 用「手画的那个框」直接切（不吸附）：")
    print(f"    另一栏 {naive_before} 块 → {naive_after} 块"
          + ("（被邻栏的框顺带切走 ✗）" if naive_after != naive_before else "（没影响）"))
    print("    ↑ 所以框边必须吸附到分栏线，别把这条删了")

    before_tiny = sum(1 for b in raw if is_tiny(b))
    after_tiny = sum(1 for b in merged if is_tiny(b))
    print(f"\n核心结论：块数 {len(raw)} → {len(merged)}，碎块 {before_tiny} → {after_tiny}")
    print(f"整批碎块共 {total_tiny} 个。每页照这样框一次即可清掉。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
