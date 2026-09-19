"""插图落点：写在正文的光标处 / 占位符处（2026-09-15 立，2026-09-18 修订）。

需求原文：「图片不要插入文字尾部，我的光标在什么位置，就插在什么位置」，以及
「占位符……做成一个跳转的 tab，点击以后自动打开补图片的那个弹窗」。

2026-09-18 修订了那个「跳转」的**发起位置**：「这里的插图待补的跳转，我不希望是在这个
预览的地方，我原本是希望在 latex 源码编辑的位置点击然后跳转到截图处」。于是：

- 预览里的占位符变成纯展示（不可点，图片下方也不再显示 markdown 的 alt）；
- 源码区按**光标所在的占位符**浮出「补这张图」，点它仍是打开原卷整页截图的框选弹窗。

这条契约横跨两侧前端，任何一边退化回「追加到文字尾部」都不会报错 —— 只是图跑错地方。
所以这里逐条钉住：

- 错题审校页：`submitMrCrop` 写正文（占位符就地替换 / 光标处插入），不写数组尾部；
- 源码区入口：按光标位反推「是第几个占位符」，序号错位只会把图补到别处；
- 题库录入页：`submitPdfCropCoordinates` 同样插到光标处；
- 两侧都必须**记住光标**：点按钮那一刻 textarea 已经失焦，读 `selectionStart` 会拿到
  默认值 0，图会莫名其妙跑到最前面。
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMPORT_JS = PROJECT_ROOT / "static" / "js" / "import.js"
MISTAKE_JS = PROJECT_ROOT / "static" / "js" / "mistake.js"
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"


@pytest.fixture(scope="module")
def import_js() -> str:
    return IMPORT_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def mistake_js() -> str:
    return MISTAKE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _function_body(source: str, name: str, indent: str = "        ") -> str:
    """取一个函数体（到下一个同级 ``function`` 为止）。import.js 的缩进比 mistake.js 深。"""

    marker = f"{indent}function {name}("
    index = source.find(marker)
    assert index >= 0, f"找不到函数 {name}"
    rest = source[index + len(marker):]
    nxt = rest.find(f"\n{indent}function ")
    return marker + (rest[:nxt] if nxt >= 0 else rest)


# ---------------------------------------------------------------- 错题审校页


def test_crop_writes_the_image_into_the_body_text(mistake_js):
    """补完的图写进**正文**：占位符就地替换，或插到光标处。"""

    body = _function_body(mistake_js, "submitMrCrop", indent="    ")

    assert "insert.field" in body and "payload[insert.field] = next" in body
    assert "mrReplacePlaceholder(source, insert.placeholderIndex, markdown)" in body
    assert "mrInsertMarkdownImage(source, insert.cursor, markdown)" in body
    # 老行为：往数组尾部 concat 一张图。那正是「图跑到了文字尾部」的根因。
    assert "concat([res.data.image_path" not in body
    assert "figure_images : record.answer_images" not in body


def test_placeholder_is_replaced_in_place_not_appended(mistake_js):
    """占位符路径必须**精确换掉那一个**，而不是往后追加一段。"""

    body = _function_body(mistake_js, "mrReplacePlaceholder", indent="    ")

    assert "seen === index" in body, "要按序号定位，不能见到第一个就换"
    assert ": null;" in body, "占位符被手删过时要能告诉调用方「没替换成功」"
    assert ".splice(" not in body


def test_preview_placeholder_is_a_static_marker_not_a_click_target(mistake_js):
    """2026-09-18 需求：补图入口不在预览里，预览只标出「这一处还缺图」。

    预览是只读的一侧，而用户当时的注意力在源码里改字 —— 可点的标记容易误触；入口搬到
    源码区之后，这里必须真的没有 click 目标，否则两处入口会慢慢分叉。
    """

    preview = _function_body(mistake_js, "contentPreviewHtml", indent="    ")
    assert "mrPlaceholderChipHtml(label)" in preview
    assert "mrPlaceholderChipHtml(label, placeholderIndex, target)" not in preview
    assert "openMistakeCropModalFromPlaceholder" not in preview
    assert "placeholderIndex" not in preview, "预览不再需要占位符序号"

    chip = _function_body(mistake_js, "mrPlaceholderChipHtml", indent="    ")
    assert "openMistakeCropModalFromPlaceholder" not in chip, "预览标记不能再开弹窗"
    assert "<button" not in chip, "静态标记，不是按钮"
    assert "插图待补" in chip


def test_image_alt_is_no_longer_rendered_as_a_caption(mistake_js):
    """图片下方不再显示 markdown 的 alt（2026-09-18）。

    补图写进来的 alt 恒为「插图」，每张图下面挂一行灰字纯属噪音，用户会以为是占位符
    没替换干净。alt 仍留在 ``<img alt>`` 属性里（无障碍 / 加载失败时有用）；占位符配图的
    「图N」编号标注要保留 —— 那是有信息量的编号。
    """

    preview = _function_body(mistake_js, "contentPreviewHtml", indent="    ")
    assert 'alt="' in preview, "alt 仍要留在 <img alt> 属性里"
    # 被删掉的那段写法：`<span class="block text-[10px] text-slate-400 mt-0.5">' + alt + '</span>`。
    # 光看 `' + alt +` 会误伤 `'" alt="' + alt +`（属性赋值，那段要留着），所以按标注的类名定位。
    assert "text-slate-400 mt-0.5\">' + alt" not in preview, "alt 不再当标注渲染"
    assert "text-slate-400 mt-0.5\">' + label" in preview, "「图N」编号标注要保留"


def test_source_editor_placeholder_is_the_way_in(html, mistake_js):
    """入口在源码区：光标落到 `[插图待补: 图N]` 上，该行旁浮出「补这张图」。"""

    for area_id, action_id in (("mrContent", "mrContentFigureAction"),
                               ("mrAnswer", "mrAnswerFigureAction")):
        # 浮层要和 textarea 共用一个 relative 包裹层 —— 定位参考就是它。
        assert f'id="{area_id}Wrap"' in html
        assert f'id="{action_id}"' in html
        assert (html.index(f'id="{area_id}Wrap"') < html.index(f'id="{area_id}"')
                < html.index(f'id="{action_id}"')), "包裹层要套住 textarea，浮层紧随其后"

    place = _function_body(mistake_js, "mrPlaceholderAt", indent="    ")
    assert "new RegExp(MR_PLACEHOLDER_RE.source, 'g')" in place, (
        "必须另起正则实例：共用那个 /g 正则会被 lastIndex 状态串味"
    )
    assert "pos >= start && pos <= end" in place
    assert "index: index" in place, "要给出出现顺序上的序号（与 mrReplacePlaceholder 同口径）"

    sync = _function_body(mistake_js, "mrSyncPlaceholderAction", indent="    ")
    assert "mrPlaceholderAt(area.value, area.selectionStart)" in sync
    assert "mrPlaceholderActionHtml(conf.target, info)" in sync
    assert "mrHidePlaceholderAction" in sync, "光标离开占位符要收起"

    action = _function_body(mistake_js, "mrPlaceholderActionHtml", indent="    ")
    assert "openMistakeCropModalFromPlaceholder(" in action, "按钮要能开框选弹窗"
    assert "info.index" in action, "必须带上「是第几个占位符」"
    assert "esc(info.label)" in action, "label 来自原始文本，必须转义"

    entry = _function_body(mistake_js, "openMistakeCropModalFromPlaceholder", indent="    ")
    assert "openMistakeCropModal(target, placeholderIndex)" in entry


def test_source_placeholder_action_follows_the_cursor_only(mistake_js):
    """浮层只跟光标走：动了就重算；blur 要延迟收起；换题与开弹窗都要收起。"""

    track = _function_body(mistake_js, "bindReviewCursorTracking", indent="    ")
    assert "mrSyncPlaceholderAction(id)" in track, "光标一动就重算浮层"
    assert "setTimeout(" in track and "mrHidePlaceholderAction(id)" in track, (
        "blur 后要延迟收起：Safari 不认 mousedown 的 preventDefault，点按钮那一刻 textarea "
        "仍会失焦，立刻收等于把按钮从指针底下抽走"
    )

    action = _function_body(mistake_js, "mrPlaceholderActionHtml", indent="    ")
    assert "preventDefault" in action

    select = _function_body(mistake_js, "selectReviewRecord", indent="    ")
    assert "mrHidePlaceholderActions()" in select, "换题要收：序号指的是上一道的占位符"

    modal = _function_body(mistake_js, "openMistakeCropModal", indent="    ")
    assert "mrHidePlaceholderActions()" in modal


def test_source_placeholder_action_falls_back_without_layout(mistake_js):
    """量不出行位置（无布局引擎 / 元素缺失）时退到右上角，而不是抛异常或干脆不显示。"""

    body = _function_body(mistake_js, "mrPlacePlaceholderAction", indent="    ")
    assert "typeof wrap.appendChild !== 'function'" in body
    assert "typeof document.createElement !== 'function'" in body
    assert "document.createElement('div')" in body and "document.createElement('span')" in body
    assert "getComputedStyle" in body, "镜像要复刻 textarea 的排版参数"
    for name in ("markRect", "areaRect", "chipRect"):
        assert name in body
    assert "!chipRect.width" in body, "量到 0 宽说明没有布局引擎，必须走兜底"
    assert "action.style.right = ''" in body and "action.style.left" in body


def test_modal_remembers_where_the_cursor_was(mistake_js):
    """点按钮那一刻 textarea 已失焦；而且要区分「没放过光标」和「光标在第 0 位」。"""

    track = _function_body(mistake_js, "bindReviewCursorTracking", indent="    ")
    assert "reviewDraft.cursor = { areaId: id, pos: area.selectionStart }" in track
    for event in ("focus", "click", "keyup", "input"):
        assert f"'{event}'" in track, f"{event} 时要刷新光标位"

    point = _function_body(mistake_js, "mrCropInsertPoint", indent="    ")
    assert "reviewDraft.cursor && reviewDraft.cursor.areaId === areaId" in point
    assert "selectionStart" not in point, "不能直接读 selectionStart：没放光标时它是 0"

    # 换题要把上一道的光标记清掉，否则会在新题的第 N 个字符处插进去。
    select = _function_body(mistake_js, "selectReviewRecord", indent="    ")
    assert "reviewDraft.cursor = null" in select


def test_review_page_offers_a_way_to_clear_leftover_placeholders(html, mistake_js):
    """不打算补的占位符要能一键清掉 —— 否则只能去 textarea 里手动删。"""

    assert 'id="mrClearPlaceholderBtn"' in html
    assert "clearMistakePlaceholders()" in html
    body = _function_body(mistake_js, "clearMistakePlaceholders", indent="    ")
    assert "MR_PLACEHOLDER_RE" in body
    assert "window.confirm" in body, "清掉正文内容是有损操作，要先问一句"
    assert 'window.clearMistakePlaceholders = clearMistakePlaceholders;' in mistake_js

    toggle = _function_body(mistake_js, "updateMrPlaceholderButton", indent="    ")
    assert "btn.style.display" in toggle, "没有占位符时按钮要收起来"


def test_inline_image_in_preview_can_be_removed(mistake_js):
    """补错了要能撤：预览里的内联图带移除入口，点了把那段 markdown 摘掉。"""

    preview = _function_body(mistake_js, "contentPreviewHtml", indent="    ")
    assert "removeInlineMistakeImage(" in preview

    body = _function_body(mistake_js, "mrRemoveNthInlineImage", indent="    ")
    assert "seen === index" in body and ": null;" in body
    assert 'window.removeInlineMistakeImage = removeInlineMistakeImage;' in mistake_js


def test_crop_box_no_longer_shows_pixel_size(html, mistake_js):
    """框选不再显示「761 × 164 px」这类尺寸数字（老师看的是图形框得对不对）。"""

    assert "mrCropSizeTag" not in html
    assert "mrCropSizeTag" not in mistake_js


# ---------------------------------------------------------------- 题库录入页


def test_bank_crop_inserts_at_cursor_not_at_the_end(import_js):
    """题库录入页的「手动截图」同样插到光标处。"""

    body = _function_body(import_js, "submitPdfCropCoordinates")
    assert "insertMarkdownImageAt(" in body
    assert "window.cropInsertCursor" in body
    # 老行为：`textarea.value.trim() + "\n\n![插图](url)\n\n"` —— 追加到文字尾部。
    assert ".value.trim() +" not in body
    assert "textarea.selectionStart = inserted.caret" in body, "插完光标留在图后面，连着截第二张才顺"


def test_bank_crop_remembers_the_cursor_of_the_right_card(import_js):
    """光标必须来自**同一张卡片**：在 A 卡的框里放光标、却点 B 卡的截图按钮，那个位置没意义。"""

    body = _function_body(import_js, "openPdfCropModalForQuestion")
    assert "window.cropInsertCursor = null" in body
    assert "cursorCard.contains(cursorArea)" in body
    assert "window.lastCropTextarea" in body

    assert "window.lastCropTextarea = t;" in import_js, "focusin 里要记住最后碰过的编辑框"
    for cls in ("card-content-textarea", "card-answer-textarea"):
        assert cls in import_js


def test_insert_helper_pads_blank_lines(import_js, mistake_js):
    """两侧的插入助手都要补空行 —— 图片不能跟正文挤在同一行；pos 缺失时退化为末尾。"""

    helper = _function_body(import_js, "insertMarkdownImageAt")
    twin = _function_body(mistake_js, "mrInsertMarkdownImage", indent="    ")

    assert "Math.min(pos, text.length)" in helper
    assert "Math.min(pos, source.length)" in twin
    for body in (helper, twin):
        assert "pos >= 0" in body, "pos 为 null（没放过光标）时要退化成「插到末尾」"
        assert "lead" in body and "tail" in body, "前后都要按需补空行"
