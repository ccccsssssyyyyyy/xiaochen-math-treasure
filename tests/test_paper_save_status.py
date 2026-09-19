"""组卷工作台「存档状态」的契约测试（修复 3）。

背景：卷面的每次改动都只落到本机 localStorage（静默、自动），真正写进数据库的
只有「保存试卷」这一个动作，而 `/api/paper/save` 每次都是 INSERT 一条新归档。
这两件事以前在界面上完全看不出来，用户关掉页面前无法确认到底存没存，误判
「没存」就会重复存出好几份归档。所以这里钉住四层不变量：

1. 运行期行为（真跑 paper.js）：文案、颜色、基准对齐/保留/跨刷新恢复 —— 见
   `js/paper_save_status_check.js`，本文件只负责把它拉起来；
2. 结构：状态元素确实渲染进了控制面板，且和「保存试卷」按钮同处一块；
3. 可达性：卷面里内联的 `onblur="saveMetaToStorage()"` 靠全局查找，函数必须
   真的导出到 window（否则失焦时抛 ReferenceError，控制台一直刷红）；
4. 文案不许撒谎：提示里说「另存一条新归档、不覆盖旧的」，后端 `save_paper`
   就只能是 INSERT；哪天改成 update，这条断言先红，提醒回去改文案。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPER_JS = PROJECT_ROOT / "static" / "js" / "paper.js"
MAIN_PY = PROJECT_ROOT / "main.py"
STATUS_CHECK_JS = Path(__file__).resolve().parent / "js" / "paper_save_status_check.js"

STATUS_ID = 'id="paperSaveStatus"'


@pytest.fixture(scope="module")
def js():
    return PAPER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_source():
    return MAIN_PY.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 1. 运行期行为
# --------------------------------------------------------------------------

def test_paper_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端语法校验")
    result = subprocess.run([node, "--check", str(PAPER_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_save_status_behaviour_runtime():
    """在 vm 沙箱里加载真实的 paper.js，跑通存档状态的完整生命周期。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(STATUS_CHECK_JS), str(PAPER_JS)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "存档状态前端实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "失败 0 项" in result.stdout, result.stdout


# --------------------------------------------------------------------------
# 2. 结构：状态元素在控制面板里
# --------------------------------------------------------------------------

def _canvas_source(js):
    fn = re.search(r"window\.renderPaperCanvas = function \(\) \{[\s\S]*?\n    \};", js)
    assert fn, "paper.js 里找不到 renderPaperCanvas"
    return fn.group(0)


def test_status_element_is_rendered_into_control_panel(js):
    src = _canvas_source(js)
    assert src.count(STATUS_ID) == 1, "paperSaveStatus 应恰好渲染一处"
    assert "草稿自动存本机" in src, "静态副说明丢了，用户就分不清草稿与归档"


def test_status_element_sits_next_to_the_save_button(js):
    """提示必须和「保存试卷」在同一块面板、按钮下方，否则用户看不到。"""
    src = _canvas_source(js)
    save_idx = src.index("savePaperToDb()")
    status_idx = src.index(STATUS_ID)
    assert save_idx < status_idx, "状态提示被排到了保存按钮上面"
    assert "Part 2: Independent Scrollable A4 Desk" in src[status_idx:], \
        "状态提示没有排在控制面板的最后，可能掉出了面板"


def test_status_is_always_visible_not_inside_the_collapsible_filter_bar(js):
    """组卷配置栏可以整条收起（maxHeight:0），状态提示不能挂在里面。"""
    collapse_block = re.search(r"window\.togglePaperFilterBar = function \(\) \{[\s\S]{0,2200}?\n    \};", js)
    assert collapse_block, "找不到 togglePaperFilterBar"
    assert "paperFilterSection" in collapse_block.group(0), "折叠的对象变了，这条断言需要重新审视"
    assert STATUS_ID not in collapse_block.group(0)


# --------------------------------------------------------------------------
# 3. 可达性：内联 handler 必须能找到函数
# --------------------------------------------------------------------------

def test_storage_helpers_are_exported_for_inline_handlers(js):
    """`onblur="saveMetaToStorage()"` 写在 HTML 属性里，靠全局查找。

    函数声明在 IIFE 内部不会自动挂到 window，不显式导出就会在失焦时抛
    ReferenceError —— 改动虽然已被 oninput 存下，但控制台会一直刷红，
    而且后来的人会以为标题没保存。
    """
    assert "window.saveMetaToStorage = saveMetaToStorage;" in js
    assert "window.saveCartToStorage = saveCartToStorage;" in js
    inline_calls = set(re.findall(r'onblur="([A-Za-z_][A-Za-z0-9_]*)\(\)"', js))
    assert inline_calls, "paper.js 里应该有内联 onblur handler，正则或写法可能已变"
    for fn in inline_calls:
        assert f"window.{fn} =" in js or f"window.{fn}=" in js, (
            f"内联 onblur 调用的 {fn} 没有导出到 window，会在失焦时报 ReferenceError"
        )


def test_status_refresh_is_wired_into_every_state_choke_point(js):
    """两个存储收口 + 画布重绘 + 启动初始化，都不能漏。"""
    cart_fn = re.search(r"function saveCartToStorage\(\) \{[\s\S]*?\n    \}", js)
    assert cart_fn and "renderPaperSaveStatus();" in cart_fn.group(0), \
        "卷面变化后没有刷新提示，改完分数提示还停在「已保存」"

    meta_fn = re.search(r"function saveMetaToStorage\(\) \{[\s\S]*?\n    \}", js)
    assert meta_fn and "renderPaperSaveStatus();" in meta_fn.group(0), \
        "元数据变化后没有刷新提示"

    canvas_fn = re.search(r"window\.renderPaperCanvas = function \(\) \{[\s\S]*?\n    \};", js)
    assert canvas_fn and "renderPaperSaveStatus();" in canvas_fn.group(0), \
        "画布重绘会换掉提示节点，重绘后必须重画状态"

    dom_ready = re.search(r"document\.addEventListener\('DOMContentLoaded'[\s\S]*?\n    \}\);", js)
    assert dom_ready and "renderPaperSaveStatus();" in dom_ready.group(0), \
        "启动时没有初始化状态提示"


def test_saved_baseline_is_restored_on_startup(js):
    """基准不跨刷新保留，已归档的卷面会被重新报成「未保存」。"""
    loader = re.search(r"function loadStateFromStorage\(\) \{[\s\S]*?\n    \}", js)
    assert loader, "找不到 loadStateFromStorage"
    body = loader.group(0)
    assert "STORAGE_KEY_SAVED" in body and "savedSignature" in body


def test_fingerprint_excludes_fields_the_archive_does_not_store(js):
    """留白高度 / 插图对齐没进 Paper、PaperQuestion，不该参与比对。"""
    fn = re.search(r"function computePaperSignature\(\) \{[\s\S]*?\n    \}", js)
    assert fn, "找不到 computePaperSignature"
    body = fn.group(0)
    assert "solution_space" not in body, \
        "留白进了指纹：调一下留白就会被报成「未保存」，用户会白存一份重复归档"
    assert "custom_figure_align" not in body
    for field in ("title", "subtitle", "paper_type", "show_notice", "show_secret"):
        assert field in body, f"指纹漏掉了 {field}，改了它不会转脏"
    assert "item.score" in body or ".score" in body, "分值没进指纹"


# --------------------------------------------------------------------------
# 4. 文案与后端行为一致
# --------------------------------------------------------------------------

def test_save_endpoint_always_inserts_a_new_archive(main_source):
    """提示里写着「再点一次会另存一条新归档，不会覆盖旧的那条」。

    目前 `save_paper` 每次都是 `Paper(...)` + `db.add`，所以文案成立。哪天改成
    upsert，就得回头改文案，别让界面承诺一件后端不做的事。
    """
    block = re.search(r'@app\.post\("/api/paper/save"\)[\s\S]*?\n@app\.', main_source)
    assert block, "main.py 里找不到 /api/paper/save"
    body = block.group(0)
    assert "paper = Paper(" in body and "db.add(paper)" in body
    assert "db.merge" not in body, "保存接口改成了 upsert，前端文案要跟着改"
    assert not re.search(r"query\(Paper\)[\s\S]{0,200}?\.update\(", body), \
        "保存接口改成了 update，前端文案要跟着改"
