"""工作台入口（三段式 tab）的前端契约。

背景：三个工作台原先只藏在标题下方的下拉里，组卷台因此几乎没有自然入口。
现在宽屏平铺三个 tab，窄屏回落到底部那套已有下拉 —— 两套入口同时存在，最容易
出的问题是「改了 tab、忘了同步下拉高亮」或者「断点只写了一半，窄屏出现两套
入口」。这里把这些钉住。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
APP_CSS = PROJECT_ROOT / "static" / "css" / "app.css"
API_JS = PROJECT_ROOT / "static" / "js" / "api.js"
TABS_CHECK_JS = PROJECT_ROOT / "tests" / "js" / "workspace_tabs_check.js"

WORKSPACES = {
    "bank": "题库工作台",
    "paper": "组卷工作台",
    "mistake": "错题工作台",
}
NARROW_QUERY = "@media (max-width: 768px)"


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css():
    return APP_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api():
    return API_JS.read_text(encoding="utf-8")


def _media_blocks(css, query):
    """按花括号配平抽出某个媒体查询的块内容（同名查询可能出现多次）。"""
    blocks = []
    for match in re.finditer(re.escape(query), css):
        start = css.find("{", match.end())
        depth = 0
        for idx in range(start, len(css)):
            if css[idx] == "{":
                depth += 1
            elif css[idx] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(css[start + 1:idx])
                    break
    return blocks


def test_all_three_tabs_exist(html):
    for ws_id in WORKSPACES:
        assert f'id="ws-tab-{ws_id}"' in html, f"缺少 {ws_id} 的 tab"
    assert html.count('class="ws-tab') == 3 + 1, "tab 数量或类名不对（含 active 组合类）"


def test_each_tab_switches_its_own_workspace(html):
    for ws_id, name in WORKSPACES.items():
        pattern = re.compile(
            r'id="ws-tab-%s"[^>]*onclick="selectWorkspace\(\'%s\', \'%s\'\)"' % (ws_id, ws_id, name)
        )
        assert pattern.search(html), f"ws-tab-{ws_id} 的 onclick 没有切到 {name}"


def test_tabs_live_in_the_top_nav(html):
    nav = re.search(r'<nav id="workspaceTopNav".*?</nav>', html, re.S)
    assert nav, "找不到 #workspaceTopNav"
    assert nav.group(0).count("ws-tab-") >= 3, "tab 没有全部放进 #workspaceTopNav"


def test_dropdown_remains_as_narrow_screen_fallback(html):
    assert 'id="workspaceDropdownContainer"' in html, "窄屏回退的下拉被删掉了"
    for ws_id in WORKSPACES:
        assert f'id="ws-btn-{ws_id}"' in html, f"下拉里的 {ws_id} 按钮丢了"
    assert "toggleWorkspaceDropdown" in html


def test_select_workspace_syncs_tabs_and_dropdown(api):
    block = re.search(r"window\.selectWorkspace = function.*?\n        \};", api, re.S)
    assert block, "找不到 selectWorkspace 实现"
    body = block.group(0)

    assert "'ws-tab-' + wsId" in body, "selectWorkspace 没有同步 tab 高亮"
    assert "ws-tab-active" in body, "selectWorkspace 没有切换 tab 的选中态"
    assert "aria-selected" in body, "tab 选中态没有同步 aria-selected（无障碍）"
    assert "'ws-check-' + wsId" in body, "原有的下拉勾选态被改坏了"
    for ws_id in WORKSPACES:
        assert f"'{ws_id}'" in body, f"selectWorkspace 的高亮表里少了 {ws_id}"


def test_narrow_screen_swaps_tabs_for_dropdown(css):
    blocks = _media_blocks(css, NARROW_QUERY)
    assert blocks, f"CSS 里找不到 {NARROW_QUERY}"

    hit = [b for b in blocks
           if "#workspaceTopNav" in b and "#workspaceDropdownContainer" in b]
    assert hit, f"{NARROW_QUERY} 没有同时处理 tab 与下拉的显隐"
    block = hit[0]
    assert re.search(r"#workspaceTopNav\s*\{[^}]*display:\s*none", block), \
        "窄屏没有隐藏平铺 tab"
    assert re.search(r"#workspaceDropdownContainer\s*\{[^}]*display:\s*block", block), \
        "窄屏没有把下拉放回来"


def test_wide_screen_hides_the_dropdown(css):
    # 媒体查询之外的基线规则：宽屏不显示下拉
    outside = re.sub(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.S)
    assert re.search(r"#workspaceDropdownContainer\s*\{\s*display:\s*none", outside), \
        "宽屏基线没有隐藏下拉容器，会出现两套入口并存"


def test_tab_highlight_behaviour_runtime():
    """在 vm 沙箱里真实执行 api.js 的 selectWorkspace，驱动三轮切换。

    静态断言只能证明「代码里出现了 ws-tab-active」，证明不了状态真的切过去了。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(TABS_CHECK_JS), str(API_JS)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "工作台 tab 高亮实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "0 failed" in result.stdout, result.stdout
