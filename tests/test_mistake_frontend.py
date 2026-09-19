"""错题扫描工作台的前端契约测试。

前端没有构建步骤，`index.html` 里的 id / onclick / 脚本顺序与 `mistake.js`
之间只有约定，没有编译期检查，改错了会在浏览器里静默失效。这里把最容易
踩空的几类不变量钉住：

1. `mistake.js` 引用的 DOM id 必须都能在 `index.html` 里找到（防拼写漂移）；
2. `index.html` 内联调用的函数必须有人定义（防 `onclick` 悬空报 undefined）；
3. 脚本加载顺序（`mistake.js` 必须晚于 `paper.js`，因为它要包装后者）；
4. 缓存刷新占位（写死匹配 `?v=1.0.1` 会让其它版本号的标签永远拿不到 mtime）；
5. 前后端口径一致（对错状态取值、记录更新用的 HTTP 动词）。
"""

import re
import shutil
import subprocess
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
APP_CSS = PROJECT_ROOT / "static" / "css" / "app.css"
MISTAKE_JS = PROJECT_ROOT / "static" / "js" / "mistake.js"
API_JS = PROJECT_ROOT / "static" / "js" / "api.js"
MAIN_PY = PROJECT_ROOT / "main.py"

INLINE_ATTR = (
    "click", "change", "input", "keydown", "keyup", "dragover", "dragleave",
    "drop", "mousedown", "mouseup", "mousemove", "submit",
)
# 内联里出现的非自定义函数（浏览器 API / 语言关键字），不要求被前端导出
BUILTIN_CALLS = {"document", "window", "event", "this", "if", "return", "function"}

# 名字带 Mistake、但**不属于错题工作台**的内联 handler。
# `toggleMistakeOnly` 是题库工作台筛选栏上的「只看错题」chip（与学科 Tab 正交，
# 只筛题库列表的来源），实现在 editor.js —— 错题工作台自己并不调它。
# 上面那条 test_inline_handlers_are_all_defined 会兜住「点击没反应」，
# 本用例（按名字前缀粗筛错题区）不该管它。
NOT_MISTAKE_WORKSPACE = {"toggleMistakeOnly"}


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css():
    return APP_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js():
    return MISTAKE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api():
    return API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_source():
    return MAIN_PY.read_text(encoding="utf-8")


def function_source(js, name):
    """取出一个顶层函数的完整源码。

    原来这里用 ``re.search(r"function X[\\s\\S]{0,2600}")`` 这种**固定字符窗口**。
    窗口卡在注释中间时断言会因为「注释变长」而红，而不是因为不变量被破坏 ——
    那种红只会训练人把数字改大，最后把断言废掉（本次改 renderPageView 的注释就
    触发了这一条）。按缩进找下一个同级 ``function`` 才是稳的：IIFE 里顶层函数缩进
    4 空格，嵌套函数缩进更深，不会误切。
    """
    start = js.index("    function " + name + "(")
    nxt = re.search(r"\n    (?:async )?function ", js[start + 1:])
    return js[start:start + 1 + nxt.start()] if nxt else js[start:]


def _referenced_ids(source):
    """一个 JS 文件里通过 el()/getElementById()/querySelector('#x') 引用的 id。"""
    refs = set()
    for pattern in (
        r"""\bel\(\s*['"]([A-Za-z0-9_\-]+)['"]""",
        r"""getElementById\(\s*['"]([A-Za-z0-9_\-]+)['"]""",
        r"""querySelector\(\s*['"]#([A-Za-z0-9_\-]+)['"]""",
    ):
        refs |= set(re.findall(pattern, source))
    return refs


def _defined_ids(source):
    return set(re.findall(r"""\bid\s*=\s*["']([A-Za-z0-9_\-]+)["']""", source))


def _inline_called_functions(source):
    calls = set()
    attr = "|".join(INLINE_ATTR)
    for handler in re.findall(rf"""on(?:{attr})\s*=\s*["']([^"']*)["']""", source):
        for fn in re.findall(r"""(?:^|[^.\w$])([A-Za-z_$][A-Za-z0-9_$]*)\s*\(""", handler):
            if fn not in BUILTIN_CALLS:
                calls.add(fn)
    return calls


def _all_js_defined_functions():
    names = set()
    for path in sorted((PROJECT_ROOT / "static" / "js").glob("*.js")):
        source = path.read_text(encoding="utf-8")
        names |= set(re.findall(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", source))
        names |= set(re.findall(r"function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", source))
        names |= set(re.findall(
            r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:function|\()",
            source,
        ))
    return names


# --------------------------------------------------------------------------
# 1. DOM 契约
# --------------------------------------------------------------------------

def test_every_referenced_dom_id_exists_in_index(html, js):
    """mistake.js 引用的每个 id 都必须在 index.html 里静态存在。

    错题工作台的元素都是静态结构（不用 JS 现造 id），所以差集必须为空 ——
    一旦为空就说明某个元素被改名或删除而 JS 没跟上。
    """
    missing = sorted(_referenced_ids(js) - _defined_ids(html))
    assert missing == [], f"mistake.js 引用了 index.html 中不存在的 id: {missing}"


def test_mistake_workspace_ui_is_present(html):
    for element_id in (
        'id="mistakeWorkspaceSection"',
        'id="ws-btn-mistake"',
        'id="mistakeStepBar"',
        'id="mistakeCardList"',
        'id="mistakePageImage"',
        'id="mistakeExportPanel"',
    ):
        assert element_id in html, element_id


def test_inline_handlers_are_all_defined(html):
    """全页面内联调用都必须在某个前端文件里定义，否则点击即 undefined。"""
    missing = sorted(_inline_called_functions(html) - _all_js_defined_functions())
    assert missing == [], f"index.html 内联调用了未定义的函数: {missing}"


def test_mistake_inline_handlers_are_exported(html, js):
    """错题区的函数必须是 mistake.js 挂到 window 上的那些。"""
    exported = set(re.findall(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", js))
    used = {
        fn for fn in _inline_called_functions(html)
        if fn.startswith(("mistake", "Mistake", "startMistake", "toggleMistake",
                          "setMistake", "renderMistake", "openMistake", "closeMistake",
                          "exportMistake", "importMistake", "recognizeMistake",
                          "deleteMistake", "editMistake", "reviewMistake",
                          "generateMistake", "uploadMistake", "backToMistake",
                          "applyMistake", "clearMistake", "removeMistake",
                          "submitMistake"))
        and fn not in NOT_MISTAKE_WORKSPACE
    }
    assert used, "没有识别到错题区任何内联 handler，正则或 HTML 结构可能已变"
    # 白名单只许放「确实不属于本模块」的。一旦 mistake.js 也导出了它，就说明它已经
    # 变成错题工作台的一部分，白名单该删 —— 留着会掩盖真正的漏导出。
    assert NOT_MISTAKE_WORKSPACE.isdisjoint(exported), (
        "这些函数已在 mistake.js 导出，请从白名单移除: "
        f"{sorted(NOT_MISTAKE_WORKSPACE & exported)}"
    )
    assert used <= exported, f"错题区调用了 mistake.js 未导出的函数: {sorted(used - exported)}"


# --------------------------------------------------------------------------
# 2. 脚本加载与缓存刷新
# --------------------------------------------------------------------------

def test_mistake_script_loads_after_paper(html):
    """mistake.js 包装了 paper.js 的 selectWorkspace，必须排在它之后。"""
    assert "/static/js/mistake.js" in html
    assert html.index("/static/js/mistake.js") > html.index("/static/js/paper.js")


def test_every_script_tag_is_registered_for_cache_busting(html, main_source):
    """每个 script 标签都要被 main.py 的 mtime 替换覆盖。

    这里钉住的是一个真实踩过的坑：`read_index` 原先写死匹配 `?v=1.0.1`，
    于是写 `?v=1.0.2` 的 api.js 和写 `?v=1.0.0` 的 onboarding.js 永远拿不到
    mtime —— 改了 JS 浏览器仍在用缓存。
    """
    list_block = re.search(r"js_files\s*=\s*\[(.*?)\]", main_source, re.S)
    assert list_block, "main.py 里找不到 js_files 列表"
    # 文件名可以带连字符（math-render.js），别把 [\w.] 写死成只认下划线
    listed = set(re.findall(r'"([\w.-]+\.js)"', list_block.group(1)))

    tagged = set()
    for src in re.findall(r'src="(/static/js/[^"]+)"', html):
        assert "?v=" in src, f"script 标签缺少版本占位，不会被 mtime 替换: {src}"
        tagged.add(Path(src.split("?")[0]).name)

    assert tagged <= listed, f"这些 JS 有标签但不在 js_files 里，缓存永不刷新: {sorted(tagged - listed)}"

    # 替换逻辑必须容忍任意占位版本号，而不是只认某一个字面量
    assert re.search(r're\.sub\(\s*r"/static/js/"\s*\+\s*re\.escape\(js\)', main_source), \
        "read_index 的版本替换仍是字面量匹配，改成任意版本号就会失效"


def test_css_cache_busting_is_unconditional(main_source):
    assert "html_content.replace('/static/css/app.css'" in main_source


# --------------------------------------------------------------------------
# 3. 三个工作台的切换链
# --------------------------------------------------------------------------

def test_workspace_switch_covers_all_three(api, js):
    """api.js 的勾选态处理必须包含 mistake，mistake.js 要包装出显隐逻辑。"""
    assert "'mistake'" in api or '"mistake"' in api
    assert "selectWorkspace" in js
    assert "MistakeStore" in js


def test_flash_prevention_css_covers_all_three_workspaces(css):
    """刷新时不能先闪一下题库页 —— 三个工作台都要有 pre-render 规则。"""
    for workspace in ("paper", "mistake"):
        assert f"html.init-ws-{workspace}" in css, workspace
    assert "init-ws-mistake #mistakeWorkspaceSection" in css


# --------------------------------------------------------------------------
# 4. 前后端口径一致
# --------------------------------------------------------------------------

def test_grad_status_values_match_backend(js, main_source):
    backend = set(re.findall(
        r'GRAD_STATUS_VALUES\s*=\s*\((.*?)\)',
        main_source,
        re.S,
    )[0].replace('"', "'").split("', '"))
    backend = {v.strip(" '") for v in backend if v.strip(" '")}

    block = re.search(r"const GRAD_META\s*=\s*\{(.*?)\n\s*\};", js, re.S)
    assert block, "mistake.js 里找不到 GRAD_META"
    frontend = set(re.findall(r"(\w+)\s*:\s*\{", block.group(1)))

    assert frontend == backend, f"前后端对错取值不一致：前端 {sorted(frontend)} / 后端 {sorted(backend)}"


def test_record_update_uses_put_on_both_sides(js, main_source):
    """记录更新后端注册的是 PUT，前端不能写成 POST。"""
    assert re.search(r'@app\.put\("\/api\/mistakes\/records\/\{record_id\}"\)', main_source)
    assert not re.search(r'@app\.post\("\/api\/mistakes\/records\/\{record_id\}"\)', main_source)

    js_block = re.search(r"function patchRecord\([\s\S]{0,1200}", js)
    assert js_block, "mistake.js 里找不到记录更新函数 patchRecord"
    assert "method: 'PUT'" in js_block.group(0), "前端记录更新没有用 PUT"


def test_record_update_field_names_match_backend(js, main_source):
    """PUT 的字段名要与后端读取的 key 对齐（改错会被静默忽略）。"""
    for field in ("grad_status", "include_in_handout"):
        assert f'"{field}" in payload' in main_source, f"后端不认这个字段: {field}"
        assert field in js, f"前端没有提交这个字段: {field}"


def test_frontend_column_payload_is_parsed_by_backend_normalizers():
    """把前端真正发出的栏目请求体喂给后端解析函数，确认两边真能对上。

    两边各自单测全绿、接缝上却对不上的情况是真实存在的：前端可能只发字符串而
    后端只认 dict、或者把「只表态双栏」和「拖过分栏线」发成了同一种形状。
    静态正则能证明字段名写对了，证明不了**语义**对得上，所以这里真调一次。
    """
    from main import _normalize_mistake_page_layouts

    layouts = _normalize_mistake_page_layouts({
        "1": "double",                               # 人工只表态「双栏」
        "2": {"mode": "double", "boundary": 0.6},    # 人工拖过分栏线
        "3": "single",
        "x": "double",                               # 脏页号
        "4": "auto",                                 # 「自动」不该被提交，这里也不认
    })
    assert layouts[1].mode == "double" and layouts[1].is_double is False, \
        "只表态「双栏」时应是占位态（boundary=1.0），由后端按自动判栏补线"
    assert layouts[2].boundary == 0.6 and layouts[2].is_double is True
    assert layouts[3].mode == "single" and layouts[3].is_double is False
    assert "x" not in {str(key) for key in layouts} and 4 not in layouts


# --------------------------------------------------------------------------
# 5. 模块导出与路由
# --------------------------------------------------------------------------

def test_mistake_module_exposes_expected_globals(js):
    for symbol in (
        "window.MistakeStore",
        "window.startMistakeCut",
        "window.startMistakeRecognize",
        "window.setMistakeGrad",
        "window.setMistakeInclude",
        "window.exportMistakeHandout",
        "window.importMistakeBatch",
        "window.deleteMistakeBatch",
        "window.openMistakeBatch",
        "window.reviewMistakeAnswer",
        "window.toggleMistakeBoxMode",
        "window.applyMistakePageBoxes",
        "window.clearMistakePageBoxes",
        "window.removeMistakeBox",
    ):
        assert symbol + " = " in js, symbol


def test_mistake_uses_the_real_api_routes(js):
    assert "'/api/mistakes/batches'" in js
    assert "/api/mistakes/batches/" in js
    assert "/api/mistakes/records/" in js
    # 学生不是从 /api/students 拉的 —— 名字随建批次一起提交，改动此处要同步后端
    assert "student_name" in js


def test_batch_creation_posts_multipart(js, main_source):
    """建批次后端读的是表单字段 + 文件，前端必须走 FormData。"""
    assert re.search(r'@app\.post\("\/api\/mistakes\/batches"\)', main_source)
    block = re.search(r"function submitMistakeBatch[\s\S]{0,1600}", js)
    assert block, "mistake.js 里找不到 submitMistakeBatch"
    assert "FormData" in block.group(0)
    assert "method: 'POST'" in block.group(0)
    for field in ("subject", "title", "file"):
        assert f"'{field}'" in block.group(0) or f'"{field}"' in block.group(0), field


def test_mistake_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端语法校验")
    result = subprocess.run(
        [node, "--check", str(MISTAKE_JS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# 6. 分栏：渲染口径与前后端字段
# --------------------------------------------------------------------------

COLUMN_CHECK_JS = Path(__file__).resolve().parent / "js" / "mistake_column_payload_check.js"


def test_layout_switch_row_is_present(html):
    """每页顶部的三态栏目开关必须在静态结构里（JS 只填内容，不造 id）。"""
    for element_id in ('id="mistakeLayoutSwitch"', 'id="mistakeLayoutHint"'):
        assert element_id in html, element_id


def test_layout_values_match_backend(js, main_source):
    """三态开关发出去的取值必须是后端认的 single / double，且「自动」＝不提交。"""
    from mathbank.page_block_split import COLUMN_DOUBLE, COLUMN_SINGLE

    assert (COLUMN_SINGLE, COLUMN_DOUBLE) == ("single", "double")
    assert re.search(r"^\s+COLUMN_SINGLE,", main_source, re.M), "main.py 没有导入 COLUMN_SINGLE"
    assert re.search(r"^\s+COLUMN_DOUBLE,", main_source, re.M), "main.py 没有导入 COLUMN_DOUBLE"
    for mode in (COLUMN_SINGLE, COLUMN_DOUBLE):
        assert f"'{mode}'" in js, f"前端没有用后端认的取值 {mode}"
    assert "delete state.pageLayouts" in js, "「自动」必须撤掉人工指定，而不是提交 auto"


def test_column_request_fields_match_backend(js, main_source):
    """人工栏目的字段名与取值必须与后端解析函数一致（改错会被静默忽略）。"""
    body = re.search(r"def _normalize_mistake_page_layouts[\s\S]{0,4000}", main_source)
    assert body, "main.py 里找不到 _normalize_mistake_page_layouts"
    assert "body.get(\"page_layouts\")" in main_source, "后端不认 page_layouts"
    assert "body.page_layouts" in js, "前端没有提交 page_layouts"

    # 切线通道已下线：请求体里不该再出现这两个字段
    assert 'body.get("page_cuts")' not in main_source, "page_cuts 通道没删干净"
    assert 'body.get("column_cuts")' not in main_source, "column_cuts 通道没删干净"


def test_block_rendering_uses_column_x_range(js):
    """题块必须按 block_x_start/x_end 定位 —— 退回整页宽涂色就等于分栏没做。

    合并题按成员矩形（`page.manual_merges[].rects`）画是**额外的**一条定位来源，
    不是替代：普通块仍得走 record 自己的 x 范围，否则分栏白做。
    """
    block = function_source(js, "renderPageView")
    assert block, "mistake.js 里找不到 renderPageView"
    assert "block_x_start" in block and "block_x_end" in block
    assert 'class="absolute left-0 right-0 cursor-pointer"' not in block, \
        "题块又变成整页宽了"


def test_js_generated_inline_handlers_are_exported(js):
    """mistake.js 自己拼出来的内联 onclick 也要挂到 window 上。

    `index.html` 里扫不到这些 handler（它们是 JS 拼的字符串），漏导出不会报错，
    只会让用户点下去毫无反应 —— 分栏开关、按新栏目重切都走这条路。

    只取属性值中「字符串拼接之前」的那一段：拼接后面的 esc()/record.id 是用来
    拼参数的私有工具，不是点击要执行的入口。
    """
    exported = set(re.findall(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", js))
    used = set()
    for handler in re.findall(r'onclick="([^"]*)"', js):
        head = handler.split("' +")[0]
        for fn in re.findall(r"""(?:^|[^.\w$])([A-Za-z_$][A-Za-z0-9_$]*)\s*\(""", head):
            if fn not in BUILTIN_CALLS:
                used.add(fn)
    assert len(used) >= 8, f"只识别到 {len(used)} 个内联 handler，正则或写法可能已变: {sorted(used)}"
    assert used <= exported, f"JS 生成的内联 handler 未导出到 window: {sorted(used - exported)}"


def test_column_behaviour_runtime():
    """在 vm 沙箱里加载真实的 mistake.js，跑通「渲染 → 拖分栏线 → 重切发请求」。

    静态断言只能证明字段名写对了，证明不了拖动之后分栏线有没有跟着走、人工栏目
    有没有随重切提交。这层错法都是静默的，只能真跑。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(COLUMN_CHECK_JS), str(MISTAKE_JS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "分栏前端实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# --------------------------------------------------------------------------
# 7. 人工框选：阈值对齐、渲染口径、实跑
# --------------------------------------------------------------------------

BOX_CHECK_JS = Path(__file__).resolve().parent / "js" / "mistake_box_payload_check.js"


def test_box_ui_is_present_in_static_html(html):
    """画框的开关与操作条必须是静态结构 —— JS 只填内容，不现造 id。"""
    for element_id in (
        'id="mistakeBoxModeBtn"',
        'id="mistakeBoxBar"',
        'id="mistakeBoxHint"',
        'id="mistakeBoxCount"',
        'id="mistakeBoxApplyBtn"',
    ):
        assert element_id in html, element_id


def test_box_thresholds_match_backend(js):
    """前端复刻了后端的四个框选阈值，对不上就会「画得出、应用后消失」。

    前端的 survivingSpans 是后端 _subtract_manual_boxes 的翻版：残段预览必须与
    应用后的结果一致，否则用户框住半块、应用后凭空多出一个碎片卡片。两边阈值
    一旦各自漂移，预览就会撒谎，而且不报错。
    """
    from mathbank import page_block_split as split

    pairs = {
        "BOX_MIN_SIZE": split.MANUAL_BOX_MIN_SIZE,
        "BOX_OVERLAP_EPSILON": split.MANUAL_BOX_OVERLAP_EPSILON,
        "BOX_FRAGMENT_MIN_HEIGHT": split.MANUAL_FRAGMENT_MIN_HEIGHT,
        "BOX_COLUMN_TOLERANCE": split.MANUAL_BOX_COLUMN_TOLERANCE,
    }
    for name, expected in pairs.items():
        found = re.search(rf"\b{name}\s*=\s*([0-9.]+)\s*;", js)
        assert found, f"mistake.js 里找不到常量 {name}"
        assert float(found.group(1)) == float(expected), (
            f"{name} 与后端不一致：前端 {found.group(1)} / 后端 {expected}"
        )


def test_box_endpoint_field_names_match_backend(js, main_source):
    """框选请求体只有 boxes 一个字段，空数组必须活着到后端（它就是「清空本页框」）。"""
    assert re.search(r'@app\.post\("/api/mistakes/batches/\{batch_id\}/pages/\{page_no\}/blocks"\)', main_source), \
        "后端没有框选路由，或路径与前端拼的不一致"
    assert 'body.get("boxes")' in main_source, "后端没有读 boxes 字段"
    # 接口层只判结构，尺寸/退化一律交给 clean_manual_boxes —— 两处各判一套的话，
    # 会出现「接口收了、前端画了、算法却不肯切」的坏状态
    assert "_normalize_mistake_boxes(body.get(\"boxes\"))" in main_source, \
        "框没有过 _normalize_mistake_boxes 这道唯一校验"

    assert "function boxesPayloadFor(page)" in js, "mistake.js 里找不到 boxesPayloadFor"
    assert "boxes: boxesFor(page.page_no).map(" in js, \
        "boxesPayloadFor 疑似过滤了空数组，「清空本页框」会失效"


def test_box_surviving_spans_is_the_only_preview_rule(js):
    """残段预览只能有一处实现 —— 散成两处后必然有一处忘了跟着改。

    现在区间运算收敛在 `survivingSpansIn(x0, x1, y0, y1, boxes)` 一处：
    - `survivingSpans(record, boxes)` 只是把 record 的矩形喂进去的薄适配层；
    - 页图渲染走的是 `survivingSpansIn`，因为合并题得按**每个成员矩形**各算一份
      （成员各自可能被框压住；只拿并集那一个范围切，残段与后端应用结果对不上）。
    所以这条断言比原来更强：不是「渲染里出现了 survivingSpans 这个名字」，而是
    「只有一处区间实现，且渲染确实从它走」。
    """
    assert js.count("function survivingSpansIn(") == 1, "区间实现不止一处（或没有）"
    assert js.count("function survivingSpans(") == 1, "survivingSpans 适配层不止一处（或没有）"

    adapter = function_source(js, "survivingSpans")
    assert "survivingSpansIn(" in adapter, "survivingSpans 没有走唯一的区间实现"

    block = function_source(js, "renderPageView")
    assert block, "mistake.js 里找不到 renderPageView"
    assert "survivingSpansIn(" in block, \
        "页图渲染没有走 survivingSpansIn，残段预览会和应用结果对不上"
    assert "survivingSpans(record, boxes)" not in block, \
        "渲染又绕回「按整条记录（合并题＝并集）算残段」了"


def test_box_record_geometry_rule_is_single_sourced(js):
    """框 ↔ 记录 的几何判定只能有一处实现。

    `focusedBoxIndex`（记录 → 框）与 `recordForBox`（框 → 记录）是一对逆运算。各写一份
    矩形比较，改了一边另一边就悄悄错位 —— 页图上高亮的框和点框跳到的卡片会变成两道题，
    而这类错位不报错，只有肉眼能看出来。真机批次里 107 条记录有 52 条是「框生成、页图
    不画块」的，走错一次就是跳到别人身上。
    """
    assert js.count("function boxMatchesRecord(") == 1, "几何判定不止一处（或没有）"
    assert js.count("function recordForBox(") == 1, "缺少 recordForBox（框 → 记录）"

    def body(name, limit):
        start = js.index("function " + name + "(")
        return js[start:start + limit]

    assert "boxMatchesRecord(" in body("focusedBoxIndex", 600), \
        "focusedBoxIndex 没有走唯一的几何判定"
    assert "boxMatchesRecord(" in body("recordForBox", 900), \
        "recordForBox 没有走唯一的几何判定"


def test_box_click_selects_record_only_outside_box_mode(js):
    """非画框模式下点框＝选中对应题块；画框模式下的语义（点框＝选中框本身）必须不变。

    框生成的记录在页图上不画块，页图上唯一可触的区域就是那个框 —— 框不接线，这类记录
    在页图上完全点不到。但框上的编辑把手（✕ / 改大小）不能被当成「点框」，否则点 ✕ 会
    跳卡片，看起来像删框删出了别的东西。
    """
    handler = js[js.index("function bindOverlayEvents("):][:3500]
    assert "const boxHit = event.target.closest('[data-box]')" in handler, \
        "overlay 点击里没有接人工框"
    assert "recordForBox(currentPage()" in handler, \
        "点框没有落到「框 → 记录」的查询上"
    assert "'[data-box-remove],[data-box-resize]'" in handler, \
        "框上的编辑把手没有从「点框」里排除，会误跳卡片"


def test_box_behaviour_runtime():
    """在 vm 沙箱里加载真实的 mistake.js，跑通「画框 → 移动/改大小/删除 → 应用」。

    框选的错法都是静默的：框拖到页外算出负坐标被后端整个丢掉、空框列表被当成
    「没改」而没发出去、未应用的框被一次后台刷新抹掉。这些都只能真跑才看得见。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(BOX_CHECK_JS), str(MISTAKE_JS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "框选前端实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


BOX_LINK_E2E_JS = Path(__file__).resolve().parent / "js" / "mistake_block_link_e2e.js"


def _jsdom_node_path():
    """jsdom 装在 managed node workspace 里（不在项目依赖内），找不到就跳过。"""
    candidates = [os.environ.get("NODE_PATH", "")]
    candidates.append(str(Path.home() / ".workbuddy" / "binaries" / "node" / "workspace" / "node_modules"))
    for candidate in candidates:
        if candidate and (Path(candidate) / "jsdom").is_dir():
            return candidate
    return None


def test_box_click_link_end_to_end():
    """真实 DOM + 真实 HTTP：点左侧人工框，右侧对应题块被滚进视野并高亮。

    假 DOM 里的 ``target.closest`` 是手搓的桩，它按假设回答「找到了 [data-box]」——
    而这次改的恰恰是这一层。所以另外跑一遍真实节点树：真实 index.html、真实
    mistake.js、真实 MouseEvent 冒泡，断言 ``scrollIntoView`` 落在哪张卡片上。

    前提是本地服务在跑（默认 127.0.0.1:8000）；没跑就跳过，不当成失败。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过真实 DOM 端到端")
    jsdom_path = _jsdom_node_path()
    if not jsdom_path:
        pytest.skip("找不到 jsdom（NODE_PATH 未指向 managed node workspace）")

    result = subprocess.run(
        [node, str(BOX_LINK_E2E_JS)],
        capture_output=True,
        text=True,
        timeout=180,
        env=dict(os.environ, NODE_PATH=jsdom_path),
    )
    if result.returncode == 2:
        pytest.skip("环境不具备（服务未运行 / 无可测样本）：\n" + result.stdout)
    assert result.returncode == 0, (
        "真实 DOM 端到端未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# --------------------------------------------------------------------------
# 8. 错题本审校页：静态结构、入口接线、入库口径实跑
# --------------------------------------------------------------------------

REVIEW_CHECK_JS = Path(__file__).resolve().parent / "js" / "mistake_review_payload_check.js"
EDITOR_JS = PROJECT_ROOT / "static" / "js" / "editor.js"


@pytest.fixture(scope="module")
def editor():
    return EDITOR_JS.read_text(encoding="utf-8")


def test_review_view_is_present_in_static_html(html):
    """审校页三栏是静态结构 —— JS 只填内容，不现造 id。"""
    for element_id in (
        'id="mistakeReviewView"',
        'id="mrList"',
        'id="mrContent"',
        'id="mrAnswer"',
        'id="mrPreview"',
        'id="mrGenerateBtn"',
        'id="mrRecognizeBtn"',
        'id="mrGateBar"',
        'id="mrGateText"',
        'id="mrSaveState"',
        'id="mistakeReviewBtn"',
    ):
        assert element_id in html, element_id


def test_review_entry_is_wired(html):
    """入口按钮 + 表单控件的绑定必须指到真实存在的函数名。

    绑了名字却没导出（``window.scheduleMistakeReviewSave`` 之类）时按下按钮毫无反应，
    页面也不会报错 —— 所以这里数的是**数量**：少一个就等于有个字段悄悄不保存。
    第 13 轮中栏改成三张卡片后，接防抖保存的输入框从 2 个（题面/解析）变成 4 个
    （再加 来源 / 自定义标签）。
    """
    assert 'onclick="openMistakeReview()"' in html, "批次详情页少了审校入口"
    assert 'onclick="closeMistakeReview()"' in html, "审校页少了返回入口"
    assert html.count('oninput="scheduleMistakeReviewSave()"') == 4, \
        "题面 / 解析 / 来源 / 自定义标签四个输入框都要接上防抖保存"
    assert html.count('onchange="scheduleMistakeReviewSave()"') == 3, \
        "题型 / 难度 / 小节三个下拉要接防抖保存"


def test_review_import_is_scoped_to_selection(js, main_source):
    """审校页必须显式带 record_ids —— 这是这条链路唯一会静默出错的地方。

    后端批量入库的默认口径是「本批次全部已识别、尚未入库的题」，而审校页左边只列了
    用户勾选进错题本的那几道。不带 record_ids 就等于绕过审校：用户以为只入库了看到的
    3 道，实际整批 40 道全进了题库和组卷试题篮，全程 200 OK、没有任何提示。
    """
    assert "record_ids: explicitIds" in js, "前端没有把审校页选中的题号传下去"
    assert 'body.get("record_ids")' in main_source, "后端不认 record_ids，前端的收窄会失效"
    assert 'MistakeRecord.recognize_status == "done"' in main_source, \
        "后端默认口径变了，上面这条注释需要重新核对"


def test_review_gate_blocks_empty_content(js):
    """题面为空必须在前端拦下，不能靠后端兜底。

    识别没跑过或失败时 content 是空串，而导出与入库都不看识别状态，会静默产出
    「本题面尚未识别」的占位题面（用户拿到 PDF 才发现）。门禁必须在前端。
    """
    body = re.search(r"function generateReviewHandout[\s\S]{0,1200}", js)
    assert body, "mistake.js 里找不到 generateReviewHandout"
    assert "题面为空" in body.group(0), "生成前没有题面非空校验"
    assert body.group(0).index("题面为空") < body.group(0).index("importMistakeBatch"), \
        "门禁必须挡在入库之前"


def test_mistake_only_filter_is_wired(html, editor):
    """「只看错题」：按钮在 index.html，逻辑在 editor.js，orgin 过滤交给服务端。

    前端筛会把 total 算错（分页在服务端），所以必须把 origin=mistake 作为查询参数
    发出去，而不是拿到一页数据后再 filter。
    """
    assert 'id="btnMistakeOnly"' in html and 'onclick="toggleMistakeOnly()"' in html
    assert "window.toggleMistakeOnly = toggleMistakeOnly;" in editor, "入口没导出"
    assert "params.append('origin', 'mistake')" in editor, \
        "origin 没有作为查询参数下发，前端筛会算错总数"
    assert "origin: str = None" in MAIN_PY.read_text(encoding="utf-8"), \
        "后端 /api/questions 没有 origin 过滤参数"


def test_question_card_shows_subject_and_origin_badges(editor):
    """卡片角标：学科恒显，错题来源额外一枚。"""
    assert "function questionCardBadges" in editor
    assert "item.origin" in editor and "mistake" in editor
    assert "${questionCardBadges(item)}" in editor, "角标函数没接进卡片模板"


def test_review_behaviour_runtime():
    """在 vm 沙箱里加载真实的 mistake.js，跑通「门禁 → 放行 → 编辑保存 → 补识别」。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(REVIEW_CHECK_JS), str(MISTAKE_JS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "审校页前端实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


ROW_HTML_CHECK_JS = Path(__file__).resolve().parent / "js" / "mistake_review_row_html_check.js"


def test_review_row_structure_and_event_propagation():
    """真实 DOM 核对左列表的行结构：内联 × 必须是行的后代，点它不能顺带选中。

    上面那个 vm 夹具用的是手搓假 DOM，只能断言 innerHTML 里写了什么。而「`<button>`
    里套 `<button>` 会被解析器把内层按钮**提到行外**」这件事只有真节点树才暴露：
    字符串里两个标签都在、顺序也对，屏幕上那个 × 已经不在行里了。所以这一次改动把行
    外层从 button 换成 div，必须由真实 DOM 来验，顺带验 stopPropagation 真的挡住了
    冒泡（假 DOM 里没有冒泡）。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过真实 DOM 校验")
    jsdom_path = _jsdom_node_path()
    if not jsdom_path:
        pytest.skip("找不到 jsdom（NODE_PATH 未指向 managed node workspace）")

    result = subprocess.run(
        [node, str(ROW_HTML_CHECK_JS), str(MISTAKE_JS)],
        capture_output=True,
        text=True,
        timeout=180,
        env=dict(os.environ, NODE_PATH=jsdom_path),
    )
    if result.returncode == 2:
        pytest.skip("环境不具备：\n" + result.stdout)
    assert result.returncode == 0, (
        "审校页行结构 / 事件传播校验未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )



# --------------------------------------------------------------------------
# 9b. 逐题识别：一道识别完就落库，前端才有「识别一道看一道」
# --------------------------------------------------------------------------


def _recognize_task_body(main_source):
    start = main_source.index("def run_mistake_recognize_task")
    end = main_source.index("def _import_mistake_record_to_bank", start)
    return main_source[start:end]


def test_recognize_applies_each_payload_inside_the_loop(main_source):
    """模型结果必须逐题写库，不能攒到整批跑完再统一归一。

    原来是 `pending.append((id, payload))` 攒在内存里、循环外再归一 —— 于是前端拿到的
    永远是「状态已就绪、点进去题面还是空的」，识别一道看一道根本做不到。

    写回点用 `_apply_mistake_payload(record, payload` 定位、**刻意不带右括号**：
    该函数后来加了 `curriculum` 参数，带右括号的锚点会随签名变化一起失效（本用例
    就是这么红过一次的），而不带右括号照样能钉住「写回在循环内」这件事。
    """
    body = _recognize_task_body(main_source)
    loop_at = body.index("for index, record in enumerate(records, start=1):")
    apply_at = body.index("_apply_mistake_payload(record, payload")
    assert apply_at > loop_at, "归一必须挪进逐题循环里"
    assert "for record_id, payload in pending:" not in body, "循环外还留着旧的归一循环"
    assert "pending.append((record.id, payload))" not in body, "还在攒 payload"


def test_recognize_commits_status_and_content_together(main_source):
    """状态与题面必须落在同一次 commit 里。

    先提交「已就绪」再写题面，中间那一刻前端会读到「状态 done、题面为空」，
    审校页的门禁正好把这道题判成「题面为空」拦下入库。
    """
    body = _recognize_task_body(main_source)
    done_at = body.index('record.recognize_status = "done"')
    apply_at = body.index("_apply_mistake_payload(record, payload")
    commit_at = body.index("db.commit()", apply_at)
    assert done_at < apply_at < commit_at


def test_recognize_task_publishes_live_queue(main_source):
    """任务要下发队列三件套，前端才能画「识别中 / 排队中」并按队列上锁。"""
    body = _recognize_task_body(main_source)
    for field in ("recognize_queue=", "recognize_current=", "recognize_done="):
        assert field in body, f"任务没下发 {field}"
    assert '"key": "normalize"' not in main_source, (
        "归一已并入识别循环，步骤条不该再留着它 —— 会永远停在「后处理归一」不前进"
    )


def test_poll_task_exposes_progress_hook(js):
    """轮询要留 onProgress 钩子，且进度刷新不许抢走当前选中的题。

    只在终态回调一次，用户得等整批跑完才看得到第一道题，逐题落库也白搭。
    """
    assert "opts.onProgress" in js
    start = js.index("function applyRecognizeProgress(task) {")
    body = js[start:js.index("\n    function ", start + 10)]
    assert "selectReviewRecord(" not in body, "识别进度不许切换当前选中的题"


def test_review_keeps_edits_safe_while_recognizing(js, html):
    """识别中/排队中的题必须只读，写回也要挡。

    逐题落库之后，用户要是能在某道题识别完成前先手写完，随后的识别结果会直接覆盖
    手写内容 —— _apply_mistake_payload 只看 payload 有没有值，不看你改没改过。
    """
    assert "function reviewRecordLocked(" in js
    assert "box.disabled = locked;" in js
    assert 'id="mrLockHint"' in html and 'id="mrLockHintText"' in html

    start = js.index("function flushReviewSave() {")
    body = js[start:js.index("\n    function ", start + 10)]
    assert "if (reviewRecordLocked(record)) return null;" in body, "识别期还能把手写内容写回服务端"

    start = js.index("function generateReviewHandout() {")
    body = js[start:js.index("\n    function ", start + 10)]
    assert "state.recognizeLive.active" in body, "识别没跑完就放行入库"


def test_review_live_refresh_compares_against_synced_snapshot(js):
    """「有没有被用户改过」要跟「上次同步给用户看的版本」比，不是跟服务端比。

    跟服务端比的话，识别刚落库时两者必然不同，会被误判成「用户在打字」，
    于是识别结果永远回填不上 —— 整套流式呈现直接失效。
    """
    assert "reviewDraft.syncedContent" in js
    start = js.index("function refreshReviewLive() {")
    body = js[start:js.index("\n    function ", start + 10)]
    assert "contentBox.value !== (reviewDraft.syncedContent || '')" in body
    assert "contentBox.value !== (record.content || '')" not in body, "还在拿 textarea 直接和服务端比"


# --------------------------------------------------------------------------
# 9. 学科文案跟随 Tab：录入标题、题型兜底、两个「错题本」按钮
# --------------------------------------------------------------------------

IMPORT_JS = PROJECT_ROOT / "static" / "js" / "import.js"
API_JS = PROJECT_ROOT / "static" / "js" / "api.js"


@pytest.fixture(scope="module")
def import_js():
    return IMPORT_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api_js():
    return API_JS.read_text(encoding="utf-8")


def test_editor_title_follows_subject_tab(html, editor, import_js):
    """三科共用同一个录入面板，抬头必须跟学科走。

    写死「录入新数学题」的话，切到物理 Tab 后抬头还是数学 —— 用户会以为切错了，
    而且标题是三科唯一的常驻身份提示。
    """
    assert "function bankSubjectLabel(" in editor and "function setEditorTitle(" in editor
    assert "window.setEditorTitle = setEditorTitle;" in editor, "内联 handler 调不到"
    for label in ("math: '数学'", "physics: '物理'", "chemistry: '化学'"):
        assert label in editor, label

    assert import_js.count("setEditorTitle('new');") == 3, "新建/重置路径没全部走生成器"
    assert import_js.count("setEditorTitle('edit');") == 2, "编辑路径没走生成器"
    assert "bankSubjectLabel() + '题！'" in import_js, "保存后的 toast 还写死学科"
    assert "setEditorTitle('draft');" in editor, "草稿标题没走生成器"

    assert "'录入新数学题'" not in import_js, "import.js 还有写死的录入标题"
    assert "'编辑数学题'" not in import_js, "import.js 还有写死的编辑标题"
    assert "'数学题入库'" not in html, "批次详情按钮还写死学科"


def test_question_type_fallback_follows_subject(api_js):
    """元数据未到达时的题型兜底也要跟学科，别再默认吐「数学题」。"""
    assert "return '数学题';" not in api_js
    assert "bankSubjectLabel() + '题';" in api_js


def test_batch_detail_bottom_button_opens_review(html, js):
    """批次详情底部的绿按钮进审校页，不再直接入库。

    它原来调 importMistakeBatch() 且不带 record_ids，等于给「绕过审校」留了正门：
    点一下就把本批所有已识别未入库的题灌进题库与试题篮，全程 200 OK、零提示。
    """
    button = re.search(r'<button[^>]*id="mistakeImportBtn"[^>]*>.*?</button>', html)
    assert button, "批次详情底部找不到入库按钮"
    block = button.group(0)
    assert 'onclick="openMistakeReview()"' in block, "按钮应该进审校页"
    assert "审校并入库" in block, "文案要说清楚它进的是审校"
    assert "importMistakeBatch()" not in block, "按钮不能再直接入库"
    assert "mistakeImportBtn" not in js, "底部按钮已不走 js，残留引用会误导"


def test_two_handout_buttons_are_distinguishable(html):
    """两个用途完全不同的按钮不能同名：一个导 PDF，一个入库并送组卷。"""
    assert "<span>生成错题本</span>" not in html, "还有同名按钮，用户会点错"
    assert "<span>生成错题本 PDF</span>" in html, "批次详情缺导出按钮"
    assert "<span>确认入库并送组卷</span>" in html, "审校页缺入库按钮"


def test_batch_import_rejects_missing_record_ids(js):
    """批量入库必须显式指定题号，函数层不放行默认口径。

    光改按钮不够：importMistakeBatch 挂在 window 上，任何调用方漏传 ids 都会
    静默退回后端「本批次全部已识别未入库」的口径。
    """
    start = js.index("function importMistakeBatch(recordIds) {")
    end = js.index("\n    function ", start + 10)
    body = js[start:end]
    assert "if (!explicitIds.length) {" in body, "缺少无 ids 时的硬门禁"
    assert body.index("if (!explicitIds.length) {") < body.index("/import-to-bank"), \
        "门禁必须挡在请求之前"
    assert "explicitIds.length ? { record_ids: explicitIds } : {}" not in body, \
        "请求体还在回退到「本批次全部」的默认口径"
    assert "el('mrGenerateBtn')" in body, "忙碌态应该打在真正触发的按钮上"


# --------------------------------------------------------------------------
# 8. 人工合并：多选时的滚动行为（实跑）
# --------------------------------------------------------------------------

MERGE_CHECK_JS = Path(__file__).resolve().parent / "js" / "mistake_merge_payload_check.js"


def test_merge_pick_scroll_behaviour_runtime():
    """在 vm 沙箱里加载真实的 mistake.js，跑通「⌘ 多选 → 合并 → 拆分」并盯住滚动。

    静态断言证明不了「重建卡片列表时视野有没有被拽走 / 列表有没有位移」—— 那是
    DOM 重建与滚动位置的交互，只有真跑才看得见。合并一道被切成 6 块的题要连点
    6 次，每次都被拽回第一块，正是从这条缝里溜过去的。

    注：这个夹具此前一直游离在 pytest 之外（只靠手动 node 跑），所以顺手接进来。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(MERGE_CHECK_JS), str(MISTAKE_JS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "合并多选实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


PAGE_INPUT_CHECK_JS = Path(__file__).resolve().parent / "js" / "mistake_page_input_check.js"


def test_mistake_page_input_runtime():
    """页码输入跳转 + 按批次记住停留页 —— 在 vm 沙箱里真跑 mistake.js。

    这两件事都属于「静态断言看不出来」的：页号输入框不收敛越界值会跳到一个不存在
    的页（页图白屏、还不报错）；「记住停留页」有多个页面切换入口，漏掉任何一个都
    只在「退出再进」时才暴露。所以这里真跑、真读 store.pageIndex 与 localStorage。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(PAGE_INPUT_CHECK_JS), str(MISTAKE_JS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "页码跳转实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


PAGE_INPUT_E2E_JS = Path(__file__).resolve().parent / "js" / "mistake_page_input_e2e.js"


def test_mistake_page_input_end_to_end():
    """真实 DOM + 真实 HTTP：输入页号跳转，以及退出重进后回到停留的那一页。

    页码输入框靠 HTML 属性上的 ``onkeydown`` / ``onchange`` 接线，而假 DOM 里没有
    「HTML 属性 → 全局函数」这条链路（属性写错名字、函数忘了挂 window 都测不出来）。
    所以另外跑一遍真实节点树：真实 index.html、真实 mistake.js、真实 KeyboardEvent，
    批次数据经真实 HTTP 取回。

    前提是本地服务在跑（默认 127.0.0.1:8000）；没跑就跳过，不当成失败。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过真实 DOM 端到端")
    jsdom_path = _jsdom_node_path()
    if not jsdom_path:
        pytest.skip("找不到 jsdom（NODE_PATH 未指向 managed node workspace）")

    result = subprocess.run(
        [node, str(PAGE_INPUT_E2E_JS)],
        capture_output=True,
        text=True,
        timeout=180,
        env=dict(os.environ, NODE_PATH=jsdom_path),
    )
    if result.returncode == 2:
        pytest.skip("环境不具备（服务未运行 / 批次不足两页）：\n" + result.stdout)
    assert result.returncode == 0, (
        "页码跳转真实 DOM 端到端未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
