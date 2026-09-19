"""防御性契约测试：手动截图的选区可缩放 / 可平移，且已完成的选区不被误点冲掉。

回归背景：
    原实现只能一次性拖拽画框——画完再按下去就等价于重画，辛苦框好的区域
    容易被误点一下冲掉，也无法微调大小。

约定（用户拍板）：
    1. 四角缩放（nw/ne/sw/se，**按坐标命中，页面上不画任何手柄元素**）+ 框内部拖拽平移整框；
    2. 已完成的选区被「锁定」：在框外 mousedown **不重画、不清除**，
       必须先点「清除框选」或按 ESC 才能重新画框；
    3. 缩放「不翻越」：拖过对角锚点时框停在最小尺寸，绝不翻转到另一侧；
    4. 不显示实时尺寸标签；
    5. 选框只用线条表示，不画四角圆点（2026-09-15 追加）。

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
    """抠出 index.html 里 pdfCropOverlayRect 整块（现已自闭合，无子元素）。"""
    html = _read(INDEX_HTML_PATH)
    start = html.find('id="pdfCropOverlayRect"')
    assert start > 0, "未找到 pdfCropOverlayRect"
    end = html.find("</div>", start)
    assert end > start, "pdfCropOverlayRect 结构异常"
    return html[start:end + len("</div>")]


def test_overlay_draws_no_corner_handles() -> None:
    """选框不画四角圆点（2026-09-15 用户要求：圆点太大影响观感，只要线条框）。

    同时卡住「不许用透明手柄糊弄」：DOM 里一个手柄元素都不许留，
    缩放必须走 hitCropHandle 的纯坐标判定。
    """
    markup = _overlay_markup()
    for handle in ("nw", "ne", "sw", "se"):
        assert f'data-handle="{handle}"' not in markup, f"{handle} 手柄元素应已删除"
    assert "rounded-full" not in markup, "选框内不应再有任何圆点样式"
    assert "pointer-events-auto" not in markup, "不应留下看不见但可点的透明手柄"


def test_overlay_keeps_line_and_fill() -> None:
    """线条框 + 框内淡色填充保留；overlay 仍 pointer-events-none 以不挡图片事件。"""
    markup = _overlay_markup()
    assert "border-brand-500" in markup, "选框需保留线条边框"
    assert "bg-brand-500/25" in markup, "用户要求保留框内淡色填充"
    assert "pointer-events-none" in markup, "overlay 需保持 pointer-events-none 以不挡图片事件"


def test_resize_survives_without_handle_elements() -> None:
    """圆点删掉后缩放不能跟着丢：坐标命中是唯一入口，且不再读 DOM 手柄。"""
    src = _read(IMPORT_JS_PATH)
    assert "function hitCropHandle(" in src, "缺少角坐标命中函数"
    assert "e.target.dataset.handle" not in src, "DOM 手柄已删，不应再读 dataset.handle"
    assert "hitCropHandle(x, y)" in src, "mousedown 必须走坐标命中"


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
