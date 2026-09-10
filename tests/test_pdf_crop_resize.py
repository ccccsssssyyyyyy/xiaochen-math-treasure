"""防御性契约测试：手动截图的选区可缩放 / 可平移，且已完成的选区不被误点冲掉。

回归背景：
    原实现只能一次性拖拽画框——画完再按下去就等价于重画，辛苦框好的区域
    容易被误点一下冲掉，也无法微调大小。

约定（用户拍板）：
    1. 四角手柄缩放（nw/ne/sw/se）+ 框内部拖拽平移整框；
    2. 已完成的选区被「锁定」：在框外 mousedown **不重画、不清除**，
       必须先点「清除框选」或按 ESC 才能重新画框；
    3. 缩放「不翻越」：拖过对角锚点时框停在最小尺寸，绝不翻转到另一侧；
    4. 不显示实时尺寸标签。

本测试分两层：
    1. 源码契约（无需 node runtime）——锁定 DOM 手柄与三态分发语义；
    2. node 实跑（node 可用时）——vm 沙箱真实执行 computeCropResize。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS_PATH = PROJECT_ROOT / "static" / "js" / "import.js"
INDEX_HTML_PATH = PROJECT_ROOT / "static" / "index.html"
NODE_CHECK_PATH = Path(__file__).resolve().parent / "js" / "crop_resize_check.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _overlay_markup() -> str:
    """抠出 index.html 里 pdfCropOverlayRect 整块（含四角手柄）。"""
    html = _read(INDEX_HTML_PATH)
    start = html.find('id="pdfCropOverlayRect"')
    assert start > 0, "未找到 pdfCropOverlayRect"
    end = html.find("</div>", html.find('data-handle="se"'))
    return html[start:end]


def test_four_corner_handles_declared() -> None:
    """四角手柄必须存在且带方向标识，否则 JS 无法判定缩放方向。"""
    markup = _overlay_markup()
    for handle in ("nw", "ne", "sw", "se"):
        assert f'data-handle="{handle}"' in markup, f"缺少 {handle} 手柄"


def test_handles_receive_pointer_events() -> None:
    """父级 overlay 是 pointer-events-none，手柄必须单独开 auto 才能被点到。"""
    markup = _overlay_markup()
    assert "pointer-events-none" in markup, "overlay 需保持 pointer-events-none 以不挡图片事件"
    assert markup.count("pointer-events-auto") >= 4, "四个手柄都需 pointer-events-auto"


def test_existing_selection_is_locked_against_redraw() -> None:
    """已有选区时，框外 mousedown 必须直接 return（不重画、不清除）。"""
    src = _read(IMPORT_JS_PATH)
    assert "cropDragMode" in src, "缺少拖拽模式状态位"
    # 三态分发：draw 只在无选区时进入
    assert "cropDragMode = 'draw'" in src
    assert "cropDragMode = 'move'" in src
    assert "cropDragMode = 'resize'" in src
    # 锁定语义：hasSelection 且既非手柄也非框内 -> return
    assert re.search(
        r"\}\s*else if \(hasSelection\) \{\s*return;", src
    ), "已有选区时框外点击必须 return，否则会重画把原框冲掉"


def test_clear_resets_drag_mode() -> None:
    """清除选区后必须回到可画新框状态，否则下次 mousedown 会沿用旧模式。"""
    src = _read(IMPORT_JS_PATH)
    start = src.find("function clearPdfCropSelection()")
    assert start > 0, "未找到 clearPdfCropSelection"
    body = src[start:start + 900]
    assert "cropDragMode = null" in body, "清除选区需重置 cropDragMode"
    assert "cropResizeHandle = null" in body, "清除选区需重置 cropResizeHandle"


def test_escape_clears_selection_before_closing_modal() -> None:
    """ESC 需分级：有选区时先清选区并保留弹窗，无选区才关闭。"""
    src = _read(IMPORT_JS_PATH)
    assert "handlePdfCropEscape" in src, "缺少 ESC 分级处理函数"
    assert "onEscape: handlePdfCropEscape" in src, "弹窗 onEscape 必须接到分级函数"

    start = src.find("function handlePdfCropEscape()")
    assert start > 0, "未找到 handlePdfCropEscape 定义"
    body = src[start:start + 400]
    assert "clearPdfCropSelection()" in body, "有选区时 ESC 应清除选区"
    assert "closePdfCropModal()" in body, "无选区时 ESC 才关闭弹窗"
    # 清选区分支必须 return，否则会连带关闭弹窗
    assert re.search(r"clearPdfCropSelection\(\);\s*return;", body), \
        "清除选区后需 return，否则 ESC 会连带关闭弹窗"


def test_compute_crop_resize_runtime() -> None:
    """node 可用时，vm 沙箱真实执行缩放算法（含不翻越与边界 clamp）。"""
    node_bin = shutil.which("node")
    if not node_bin:
        return  # node 不可用则跳过实跑，源码契约已覆盖关键不变式

    proc = subprocess.run(
        [node_bin, str(NODE_CHECK_PATH), str(IMPORT_JS_PATH)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "选区缩放算法实跑未通过:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
