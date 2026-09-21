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
#: 题面正文渲染的**唯一**实现（2026-09-21 方案 B 后由它收口）
MATH_RENDER_JS = PROJECT_ROOT / "static" / "js" / "math-render.js"


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js():
    return REDO_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def math_render_js():
    return MATH_RENDER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_source():
    return MAIN_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def check_js():
    return REDO_CHECK_JS.read_text(encoding="utf-8")


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
#: 当前 107 项（含人工标注掌握度、切回错题台落点两节）。留 ~10% 余量：正常增删
#: 个别断言不会误报，
#: 整块章节被删掉则一定会红。
REDO_CHECK_MIN_ITEMS = 122


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


def test_redo_adopt_popup_is_fully_removed(html, js):
    """确认卡整条链路已下线，三个文件里都不许留半截。

    入口改到组卷面板第四格：点「记作重做练习」才认领，点 PDF 预览不问也不认领。
    留一半的症状分别是「内联 onclick 点了毫无反应」（DOM 没了 handler 还在）与
    「导出后突然又弹出旧卡」（触发点没拆干净）。
    """

    for name in (
        "redoAdoptPrompt",
        "redoAdoptText",
        "redoAdoptConfirmBtn",
        "redoAdoptDismissBtn",
    ):
        assert name not in html, f"{name} 应从 index.html 移除"

    for name in (
        "maybePromptRedoAdopt",
        "confirmRedoAdopt",
        "dismissRedoAdopt",
        "adoptContext",
        "declinedDraftSignatures",
    ):
        assert name not in js, f"redo.js 里还留着 {name}"

    paper_src = (PROJECT_ROOT / "static" / "js" / "paper.js").read_text(encoding="utf-8")
    assert "maybePromptRedoAdopt" not in paper_src
    assert "promptRedoAdoptAfterExport" not in paper_src
    assert "target !== 'sheet'" not in paper_src, "导出成功那条路不该再触发认领"


def test_paper_panel_has_redo_adopt_cell():
    """认领入口是组卷面板导出行的第四格：点了走 POST /api/redo/adopt。"""

    src = (PROJECT_ROOT / "static" / "js" / "paper.js").read_text(encoding="utf-8")

    assert "adoptPaperAsRedo" in src
    assert "'/api/redo/adopt'" in src
    assert "paperRedoAdoptBtn" in src
    assert "${redoAdoptCell}" in src, "第四格没挂进导出按钮行"
    # 四格等分后 LaTeX 那格只剩约 110px，缩写了；全称留在 title 里
    assert "<span>LaTeX</span>" in src


def test_redo_adopt_state_lives_in_store():
    """认领态必须挂在 PaperStore 上，并带卷面指纹。

    面板每次改动都整块重建 innerHTML（改分、换题、调留白都触发），状态放 DOM 上会被
    抹掉 —— 表现成「刚认领完，动一下题目就变回未认领」。指纹防的是另一头：认领之后又
    改了题却仍显示已认领，那录进去的会与手上纸卷对不上。
    """

    src = (PROJECT_ROOT / "static" / "js" / "paper.js").read_text(encoding="utf-8")
    assert "redoAdopt: null" in src, "PaperStore 里没有认领态字段"
    assert "window.PaperStore.redoAdopt =" in src, "认领成功后没有写回状态"
    assert "adopt.signature === sig" in src, "缺卷面指纹比对"
    assert "loadedPaperFingerprint = signature" in src, (
        "认领后没把「卷面 = 库里这张卷」的基准归位，再点一次会又建一张重复卷"
    )


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


def test_switch_mistake_subtab_collapses_other_top_views(js):
    """切子标签必须把错题工作台收敛成「只有目标视图可见」。

    错题台有四个并列顶层视图（批次列表 / 重做复习 / 错题本审校 / 批次详情）。
    只藏 listView 的写法下，从批次详情（切题页）被认领跳转拉过来时 detailView
    还在，两个 ``flex-1`` 视图同屏平分高度 → 重做复习页的固定骨架吃掉 145px，
    题干区被压成 0。2026-09-20 实测：题目看不见，只剩「做错 / 做对」按钮。
    """

    assert "focusMistakeView" in js, "缺少统一的视图收口函数"
    for view_id in (
        "mistakeListView",
        "mistakeRedoView",
        "mistakeReviewView",
        "mistakeDetailView",
    ):
        assert view_id in js, f"收口清单漏了 {view_id}"

    # 两个分支都要收口。scan 分支若只「显示列表」而不收口，正是叠加的来源。
    assert "focusMistakeView('mistakeRedoView')" in js
    assert "focusMistakeView('mistakeListView')" in js
    # 旧写法（只藏列表）必须已经消失
    assert "if (listView) listView.classList.add('hidden');" not in js


def test_workspace_switch_keeps_the_open_view(js):
    """切回错题台：重做录入页收起，其余**谁开着就落谁**，不能一律打开列表。

    ``openMistakeBatch`` 开详情前先关了列表，说明四个顶层视图本就互斥。无条件打开列表
    的写法会让「停在切题详情页 → 去题库 → 切回来」变成列表与详情同屏平分高度
    （2026-09-20 用户截图：切题页漏在下半屏）。
    """

    assert "function landOnMistakeWorkspace(" in js
    assert "landOnMistakeWorkspace();" in js, "selectWorkspace 包装层没走落点收口"

    start = js.index("function landOnMistakeWorkspace(")
    end = js.index("function closeRedoReview(")
    body = js[start:end]
    for view_id in ("mistakeReviewView", "mistakeDetailView", "mistakeListView"):
        assert view_id in body, f"落点规则漏了 {view_id}"
    assert "focusMistakeView(keepId)" in body, "落点必须收口，否则会叠加"


def test_close_redo_review_does_not_touch_detail_view(js):
    """「← 返回批次列表」刻意**不**碰 detailView / reviewView。

    从题库切回错题台时保留用户上次停留的批次详情页是既有行为 —— 去别的台查个
    东西再回来，切题上下文还在。这条路只收重做面板，认领跳转由
    ``switchMistakeSubtab`` 负责收口。别顺手在这里加收口。
    """

    start = js.index("function closeRedoReview(")
    end = js.index("async function refreshRedoBadge(")
    body = js[start:end]

    assert "focusMistakeView" not in body, "closeRedoReview 不该越权收口其它视图"
    assert "redoView" in body and "listView" in body, "重做面板本身的显隐不能被删掉"


def test_grade_card_keeps_min_height(html):
    """窗口矮或分屏时题干区不能被压成 0。

    录入面板的头部 / 进度 / 底部按钮都是 ``shrink-0``（约 145px 不可压缩），
    只有题干区是 ``flex-1``。没有最小高度兜底时，容器一矮它就消失，
    表现为「题卡一片空白」—— 与视图叠加是两个独立的成因，别只修一个。
    """

    card = re.search(r'id="redoGradeCard"[^>]*class="([^"]*)"', html)
    assert card, "找不到题干区容器"
    assert "min-h-[240px]" in card.group(1), "题干区需要最小高度兜底"

    pane = re.search(r'id="redoGradePane"[^>]*class="([^"]*)"', html)
    assert pane, "找不到录入面板容器"
    assert "overflow-y-auto" in pane.group(1), (
        "压到极限时整块要能滚 —— 否则 min-h 撑出去的高度会把底部「做错 / 做对」裁掉"
    )


def test_grade_card_renders_markdown_body_images(js, check_js, math_render_js):
    """题干 / 解析里的 markdown 内联图必须渲染成 <img>。

    错题入库时补的图是写成 ``![插图](/static/uploads/...)`` **内联在正文里**的
    （见 mistake.js 的补图逻辑），未必进 ``image_paths``。错题详情、组卷台画布、
    题库编辑器、导出的 PDF 四路都认这种语法，唯独重做录入页漏了 —— 2026-09-20
    用户截图：纸上有的图，屏幕上是一串 ``![插图](...)`` 文字。

    修完那次之后做的是**方案 B**：不再在 redo.js 里补一份，而是把「题面正文 → HTML」
    收口到 ``math-render.js``，redo.js 只决定版式。所以这里钉的是「还在调公共层」，
    不是「正则还写在本地」。

    真正跑渲染的断言在 ``tests/js/redo_payload_check.js`` 第 14 节（13 项）。
    """

    # redo.js 必须走公共层，不能自己另写一份渲染
    assert "window.MathRender.renderQuestionBody(content, opts)" in js, (
        "题干/解析渲染没走公共层 —— 自己再写一份就会重新漏语法"
    )
    assert "latexPreviewHtml(item.display_content, inlineFigureUrls)" in js, (
        "题干渲染没把已渲染的图收出来"
    )
    assert "figureStripHtml(item, inlineFigureUrls)" in js, "配图数组没接上去重"
    assert "skip.indexOf(u) === -1" in js, "去重只做了一半"

    # 公共层必须真的认这种语法，并且过安全过滤
    assert r"/!\[([^\]]*)\]\(\s*([^)\s]+)\s*\)/g" in math_render_js, "公共层少了内联图正则"
    assert "safeImageUrl(fig.rawUrl)" in math_render_js, "公共层的图没走 safeImageUrl"
    assert "opts.dropUnsafe === true ? '' : escapeText(fig.match)" in math_render_js, (
        "过不了安全过滤的图应当原样留着 markdown —— 静默抹掉会让老师以为这题本来没图"
    )
    # 去重要靠「这次真正渲染出来的 URL」，不能靠原始 markdown 字符串
    assert "urls.indexOf(safeUrl) === -1" in math_render_js, "公共层没做去重"

    for label in ("正文内联图渲染成 <img>", "指同一文件时只渲染一张图", "解析里的图渲染成 <img>"):
        assert label in check_js, f"夹具第 14 节少了断言：{label}"


def test_inline_figure_syntax_has_single_definition(math_render_js):
    """「什么算一张内联图」只能有一份定义。

    2026-09-21 之前 editor / paper / mistake / redo 各写一份正则与安全判断，
    redo 那份漏了整条分支 —— 于是同一道题「导出的 PDF 上有图、重做页印出一串
    markdown」。抄四遍就注定会漏，所以这里反过来盯住：**业务模块里不许再出现
    内联图正则**，谁要用都得调 math-render.js。
    """

    for name in ("editor.js", "paper.js", "mistake.js", "redo.js", "import.js", "ocr.js"):
        source = (PROJECT_ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        # 只盯「抽取图片」那类正则；ocr.js 里保护 `![` 不被当感叹号清掉的那一行不算
        leftover = [
            line for line in source.splitlines()
            if "!\\[" in line and "]" in line and "(" in line
            and "MARKDOWN_IMG_START" not in line
        ]
        assert leftover == [], (
            f"{name} 里还留着内联图正则，应当改用 MathRender 的 "
            f"replaceInlineFigures / inlineFigureUrls / stripInlineFigures：{leftover}"
        )

    # 四处出口都真的接上了公共层
    exports = {
        "mistake.js": "renderQuestionBody",
        "redo.js": "renderQuestionBody",
        "paper.js": "stripInlineFigures",
        "editor.js": "replaceInlineFigures",
    }
    for name, fn in exports.items():
        source = (PROJECT_ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        assert f"window.MathRender.{fn}" in source, f"{name} 没接上 MathRender.{fn}"
