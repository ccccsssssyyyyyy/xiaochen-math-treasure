"""错题重做复习面板（redo.js）的前端契约测试。

与 `test_mistake_frontend.py` 同一套路：前端没有构建步骤，index.html 的 id / onclick
与 redo.js 之间只有约定。这里钉住五件事：

1. `redo.js` 引用的 DOM id 必须都在 index.html 里（防拼写漂移）；
2. `redo.js` 的 `onclick` 必须真的挂到了 window 上；
3. **两个入口必须同时改**：`switchMistakeSubtab` 的 scan/redo 与 index.html 的按钮
   —— 只改一边的表现是「点了没反应」，比报错更难查；
4. 脚本顺序：`redo.js` 在 `mistake.js` 之后（它要包一层 selectWorkspace）；
5. 调用的接口必须真实存在（防前后端路由各写各的）。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
REDO_JS = PROJECT_ROOT / "static" / "js" / "redo.js"
MAIN_PY = PROJECT_ROOT / "main.py"
#: 假 DOM 运行期夹具（vm 里真跑整个 redo.js，断言渲染口径与请求体）
REDO_CHECK_JS = Path(__file__).resolve().parent / "js" / "redo_payload_check.js"


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js():
    return REDO_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_source():
    return MAIN_PY.read_text(encoding="utf-8")


def _referenced_ids(source):
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


def _inline_calls(source):
    return set(re.findall(r"""on(?:click|change)\s*=\s*["']([A-Za-z_$][A-Za-z0-9_$]*)""", source))


def test_every_referenced_dom_id_exists_in_index(html, js):
    missing = sorted(_referenced_ids(js) - _defined_ids(html))
    assert missing == [], f"redo.js 引用了 index.html 中不存在的 id: {missing}"


def test_redo_inline_handlers_are_exported(js):
    """redo.js 自己导出的函数里，必须覆盖 index.html 里用到的那几个。"""
    exported = set(re.findall(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", js))
    for name in (
        "switchMistakeSubtab", "closeRedoReview", "toggleRedoNewPanel", "submitRedoNewPaper",
        "createQuickRedoPaper", "syncRedoPool", "toggleRedoBoard", "openRedoPaper",
        "redoStep", "gradeRedoCurrent", "setRedoHideAnswer", "exportRedoPaper",
        "deleteRedoPaper", "cancelRedoDelete", "markRedoMastery",
    ):
        assert name in exported, f"{name} 没有挂到 window 上，内联 onclick 会 undefined"


def test_redo_js_generated_onclick_names_are_all_exported(js):
    """redo.js **自己拼出来的**内联处理器，函数名也必须已导出。

    这类漏导出光看代码看不出来：HTML 里扫不到（字符串是运行时拼的）、点了毫无反应、
    控制台也不报错。上面那条只钉死写死在 index.html 里的名字，这条补上动态那批。
    """

    exported = set(re.findall(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", js))
    generated = _inline_calls(js)
    assert generated, "没有识别到 redo.js 里拼出来的任何 onclick/onchange，正则可能已失效"
    missing = sorted(generated - exported)
    assert missing == [], f"redo.js 拼出了未导出的内联处理器: {missing}"


def test_redo_panel_ids_called_from_html_are_exported(html, js):
    """index.html 里属于重做面板的内联调用（名字里带 Redo / redo）都要有人实现。"""
    exported = set(re.findall(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", js))
    used = {fn for fn in _inline_calls(html) if "Redo" in fn or fn.startswith("redo")}
    assert used, "没有识别到重做面板的任何内联 handler，正则或 HTML 结构可能已变"
    assert used <= exported, f"重做面板调用了 redo.js 未导出的函数: {sorted(used - exported)}"


def test_subtab_bar_and_redo_view_exist(html):
    for element_id in (
        'id="mistakeSubtabScan"',
        'id="mistakeSubtabRedo"',
        'id="mistakeRedoView"',
        'id="redoStatBar"',
        'id="redoTaskList"',
        'id="redoGradeCard"',
        'id="redoGradePane"',
        'id="redoBoardPanel"',
    ):
        assert element_id in html, element_id
    # 重做面板与批次列表是同级视图，默认必须藏着（否则进错题台直接看到空录入区）
    assert re.search(r'id="mistakeRedoView"[^>]*class="[^"]*\bhidden\b', html), "重做面板默认必须是隐藏的"


def test_script_loads_after_mistake_js(html):
    """redo.js 也包装了 selectWorkspace，必须在 mistake.js 之后加载。"""
    assert "/static/js/redo.js" in html
    assert html.index("/static/js/redo.js") > html.index("/static/js/mistake.js")


def test_every_script_tag_is_registered_for_cache_busting(html, main_source):
    list_block = re.search(r"js_files\s*=\s*\[(.*?)\]", main_source, re.S)
    assert list_block, "main.py 里找不到 js_files 列表"
    listed = set(re.findall(r'"([\w.-]+\.js)"', list_block.group(1)))
    tagged = set(re.findall(r"/static/js/([\w.-]+\.js)", html))
    assert tagged <= listed, f"这些 script 标签拿不到 mtime 版本号: {sorted(tagged - listed)}"
    assert "redo.js" in listed


def test_redo_routes_used_by_frontend_exist_in_backend(js, main_source):
    """前端打的每个 /api/redo* 路由都要在后端注册。"""
    used = set(re.findall(r"""['"`](/api/redo[^'"`]*)['"`]""", js))
    assert used, "没有识别到任何 /api/redo 调用"
    # 以 / 结尾的是拼接前缀（'/api/redo/papers/' + id），不是完整路由，单独钉。
    fixed = {route for route in used if not route.endswith("/")}
    for route in sorted(fixed):
        assert f'"{route}"' in main_source, f"后端没有注册路由 {route}"
    assert "'/api/redo/papers/'" in main_source or '"/api/redo/papers/' in main_source, \
        "后端没有注册 /api/redo/papers/{id} 系列路由"
    assert '@app.get("/api/redo/overview")' in main_source
    assert '@app.get("/api/redo/tasks")' in main_source
    assert '@app.post("/api/redo/papers")' in main_source
    assert '@app.post("/api/redo/pool/sync")' in main_source
    assert '@app.post("/api/redo/papers/{paper_id}/grade")' in main_source
    assert '@app.post("/api/redo/papers/{paper_id}/export")' in main_source
    assert '@app.delete("/api/redo/papers/{paper_id}")' in main_source
    assert '@app.post("/api/redo/questions/{question_id}/mastery")' in main_source, \
        "人工标注掌握度的接口没注册 —— 卡片上的「标为已掌握」会 404"


def test_redo_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，跳过语法检查")
    result = subprocess.run(
        [node, "--check", str(REDO_JS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_redo_js_does_not_use_regex_lookbehind(js):
    """JS 正则禁用后行断言（Safari 老版本会直接抛 SyntaxError）。"""
    assert "(?<=" not in js and "(?<!" not in js


def test_redo_js_has_no_inline_confirm_dialog(js):
    """删除确认走卡片上的两段式按钮，不弹 window.confirm（原生弹窗会遮住整页且不可回收）。"""
    assert "window.confirm(" not in js
    assert "confirm(" not in js.replace("confirmRedoDelete", "").replace("cancelRedoDelete", "")


def test_redo_payload_check_is_wired_into_pytest():
    """夹具必须真的进回归网 —— 它是脚本，不写包装就永远不会被拉起。

    `tests/js/redo_payload_check.js`（117 项）是本功能唯一的**运行期**防线：静态断言
    只能证明字段名/函数名写对了，证明不了「点的是哪道题、发出去的 body 长什么样、
    渲染的是不是导出印在纸上的那一版」。这类夹具历史上已出现过「存在很久但没人跑」
    的漏网（见 fake-dom-module-payload-check 技能第 6 条），所以这里钉住文件存在，
    且本文件里确实有一个 subprocess 调用把它跑起来。
    """
    assert REDO_CHECK_JS.is_file(), f"夹具缺失: {REDO_CHECK_JS}"
    own_source = Path(__file__).read_text(encoding="utf-8")
    assert "str(REDO_CHECK_JS), str(REDO_JS)" in own_source, "夹具没被 subprocess 拉起来"


#: 断言条数下限 —— 夹具被误删/掏空时这里会先红，不必等某项断言失效才发现。
#: 当前 117 项（含人工标注掌握度、认领组卷台那张卷两节）。留 ~10% 余量：正常增删
#: 个别断言不会误报，
#: 整块章节被删掉则一定会红。
REDO_CHECK_MIN_ITEMS = 105


def test_redo_payload_check_passes_in_fake_dom():
    """在 vm 沙箱里加载真实的 redo.js，跑通「出卷 → 打开 → 录入 → 掌握 → 导出 → 删除」。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(REDO_CHECK_JS), str(REDO_JS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "重做面板前端实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # 全通过时夹具最后一行是「全部通过：N 项」——N 骤降说明有整块断言没跑到
    # （夹具中途抛异常就是这个症状：报告的失败数会严重低估）。
    match = re.search(r"全部通过：(\d+) 项", result.stdout)
    assert match, f"夹具未输出断言总数:\n{result.stdout}"
    assert int(match.group(1)) >= REDO_CHECK_MIN_ITEMS, (
        f"断言数从 ≥{REDO_CHECK_MIN_ITEMS} 降到 {match.group(1)}，夹具可能被掏空"
    )


# ---------------------------------------------------------------- 认领：组卷台 → 重做闭环
#
# 断点：错题闭环的第 1 遍练习几乎必然发生在组卷台，但那张卷不带 is_redo，它的做题结果
# 没有入口写回 redo_attempts。确认卡就是补上的那座桥，下面这些钉住它别悄悄断掉。


def test_redo_adopt_prompt_dom_ids_exist(html):
    """确认卡是静态结构（不是运行时拼的），id 必须都在 index.html 里。"""

    defined = _defined_ids(html)
    for name in (
        "redoAdoptPrompt",
        "redoAdoptText",
        "redoAdoptConfirmBtn",
        "redoAdoptDismissBtn",
    ):
        assert name in defined, f"{name} 不在 index.html 里"


def test_redo_adopt_prompt_starts_hidden(html):
    box = re.search(r'id="redoAdoptPrompt"[^>]*class="([^"]*)"', html)
    assert box, "找不到确认卡容器"
    assert "hidden" in box.group(1), "确认卡默认必须隐藏，否则一进页面就盖住整屏"


def test_redo_adopt_prompt_handlers_are_exported(js):
    exported = set(re.findall(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", js))
    for name in (
        "confirmRedoAdopt",
        "dismissRedoAdopt",
        "maybePromptRedoAdopt",
        "openRedoPaperFromLibrary",
    ):
        assert name in exported, f"{name} 没有挂到 window 上，内联 onclick 会 undefined"


def test_paper_export_asks_before_adopting():
    """导出成功后要问一句，且答题卡不弹（答题卡不是拿来做的练习卷）。"""

    source = (PROJECT_ROOT / "static" / "js" / "paper.js").read_text(encoding="utf-8")
    assert "promptRedoAdoptAfterExport" in source, "主编辑器导出后没有问认领"
    assert "target !== 'sheet'" in source, "答题卡不该弹这个确认卡"
    # 历史卷的快速导出也要问（它带 paper_id，declined 能落库，之后不再追问）。
    assert "maybePromptRedoAdopt" in source


def test_paper_export_tracks_loaded_paper_identity():
    """从历史载入的卷面要记住来源 + 题目指纹。

    否则「载入卷 A → 导出 → 认领」会新建一张重复的卷；而指纹是防「载入 A 之后又改了
    题目，却把结果记到 A 头上」的那道闸。
    """

    source = (PROJECT_ROOT / "static" / "js" / "paper.js").read_text(encoding="utf-8")
    assert "paperSignature" in source
    assert "loadedPaperFingerprint" in source
    assert "loadedPaperId" in source


def test_redo_adopt_routes_exist(main_source):
    for route in (
        '@app.post("/api/redo/adopt")',
        '@app.post("/api/redo/release")',
        '@app.post("/api/redo/eligibility")',
        '@app.post("/api/redo/decline")',
    ):
        assert route in main_source, f"缺少路由 {route}"


def test_adopt_writes_only_metadata(main_source):
    """认领只写 metadata，绝不能碰 redo_count、也不能往 redo_attempts 里插行。

    ``redo_count = len(attempts)`` 且 ``attempt_no = redo_count + 1`` 决定选项变换
    档位 —— 认领时插一行会让第 3 遍的「剥选项」档错位成第 4 遍，而那是印在纸上的。
    """

    start = main_source.index("def _adopt_paper_as_redo(")
    end = main_source.index("def _release_adopted_redo(")
    body = main_source[start:end]

    assert "RedoAttempt(" not in body, "认领不能往 redo_attempts 里插行"
    assert not re.search(r"\.redo_count\s*=", body), "认领不能直接改 redo_count"
    assert "redo_locked_order" in body, "认领卷必须锁原序呈现"
