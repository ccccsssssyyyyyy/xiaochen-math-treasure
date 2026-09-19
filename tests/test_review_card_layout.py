# -*- coding: utf-8 -*-
"""错题本审校页三栏卡片化 —— 中栏与题库「录入新数学题」共用同一套表单组织。

用户 2026-09-15 拍板（第 13 轮）：审校页红框内三栏改成

    左「本次收录」  一张卡片 + 列表
    中「错题编辑」  抬头条 + 滚动区里的**三张卡片**：分类信息 / 题干内容录入 / 解析录入
    右「题目预览」  一张卡片：上半题干、下半「参考答案与详细解析」

中栏此前只有一张「题面·解析」字段卡片 —— 分类信息（来源/学段/章节/小节/知识点/
解题方法/关联章节/自定义标签）全都没有落点，用户只能在题库录入页补。这一版把题库
录入表单那套组织搬了过来。

"一致"的判据是**复用同一批类**，而不是"看起来像"：

    | 角色       | 题库（基准）                                          | 审校页（须相同）              |
    |------------|-------------------------------------------------------|-------------------------------|
    | 卡片容器   | ``glass-panel p-4 rounded-2xl``                        | ``glass-panel p-3 rounded-2xl`` |
    | 区块标题   | ``text-sm font-semibold text-slate-500`` + ``fa-*`` 图标 | 同                            |
    | 字段 label | ``text-[11px] font-medium text-slate-500 mb-1``        | 同                            |
    | 输入控件   | ``glass-input`` / ``glass-select``                     | 同（原本就同款，别"顺手优化"） |

被替换掉的旧样式 ``text-[10px] font-bold text-slate-400 uppercase tracking-wide``
在审校页区间内必须为 0：它在页面上渲染成"小灰字大写标题"，与题库的深色加粗标题属于
两套视觉语言。回退即失败 —— 这条是本文件的核心理由。

刻意**不**断言的两件事：

* 编辑框的 ``font-mono`` —— LaTeX 源码用等宽字体是有意为之（列对齐、易读），
  不属于"对齐题库"的范围，反而专门加了一条测试守住它不被顺手改掉。
* 三栏卡片的具体高度/内边距数值 —— 那属于版式微调，卡死会阻碍正常演进。

2026-09-16（第 14 轮）：解析卡片里加了「解析截图识别」——
点击 / 拖入 / 粘贴截图 → ``POST /api/ocr`` → 文字按「追加 / 替换」写入解析框。
解析那个 label 因此从"独占一行"变成 flex 行里的左半（右半是模式开关），
解析卡片也从 3 个直接子元素变成 4 个。两者都在下面被显式断言住。

学科差异（数学全套 / 物化只留 题型·来源·学段·章节·小节 + 关联章节）是**运行时**行为
（``applyReviewMetaSubjectUI()`` 按 id 切换 ``hidden``），静态 HTML 里两种形态的容器
都得在 —— 所以这里只断言容器与 id 齐全，字段增减交给 ``tests/js/`` 的行为夹具。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"

REVIEW_START = 'id="mistakeReviewView"'
REVIEW_END = 'id="mistakeDetailView"'

CARD = "glass-panel rounded-2xl"
HEADING_SPAN = '<span class="text-sm font-semibold text-slate-500">'
HEADING_H3 = '<h3 class="text-sm font-semibold text-slate-500 flex items-center space-x-2 min-w-0">'

# 中栏三张卡片（带 p-3，所以不会撞上上面那个不带内边距的 CARD 常量）
MID_CARDS = (
    ('mrMetaCard', '<div id="mrMetaCard" class="glass-panel p-3 rounded-2xl space-y-3 shrink-0">', '分类信息'),
    ('mrContentCard', '<div id="mrContentCard" class="glass-panel p-3 rounded-2xl shrink-0 flex flex-col">', '题干内容录入'),
    ('mrAnswerCard', '<div id="mrAnswerCard" class="glass-panel p-3 rounded-2xl shrink-0 flex flex-col">', '解析录入'),
)
SCROLL_BOX = '<div class="flex-1 min-h-0 overflow-y-auto custom-scrollbar flex flex-col gap-2">'

# 三档字段 label：分类信息里的普通字段 / 带图标的字段 / 两个编辑框
META_LABEL_PLAIN = 'class="block text-[11px] font-medium text-slate-500 mb-1"'
META_LABEL_ICON = 'class="block text-[11px] font-medium text-slate-500 mb-1 flex items-center"'
FIELD_LABEL = 'class="mt-3 text-[11px] font-medium text-slate-500 mb-1 flex items-center"'

LEGACY_MICRO_HEADING = "text-[10px] font-bold text-slate-400 uppercase tracking-wide"


def _review_html() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert REVIEW_START in html, "审校页容器不见了"
    assert REVIEW_END in html, "批次详情页容器不见了（审校页区间要靠它收尾）"
    return html.split(REVIEW_START)[1].split(REVIEW_END)[0]


def _columns(review: str) -> tuple[str, str, str]:
    """按三栏各自的起始标记切出区间（三栏内部都没有同类嵌套，第一个闭合标签即边界）。"""
    left = review.split('<aside class="w-[248px]')[1].split("</aside>")[0]
    mid = review.split('<section class="flex-1 min-w-0')[1].split("</section>")[0]
    right = review.split('<aside class="w-[36%]')[1].split("</aside>")[0]
    return left, mid, right


# --- 卡片结构 -------------------------------------------------------------


def test_left_column_is_one_glass_card() -> None:
    """左栏「本次收录」整个列表包进一张玻璃卡片，标题行带下分隔线。"""
    left, _, _ = _columns(_review_html())
    assert left.count(CARD) == 1, "左栏应恰好一张卡片"
    assert 'id="mrList"' in left, "列表容器必须在卡片内部，否则滚动区跑出圆角"
    assert "border-b border-slate-200/60" in left, "卡片标题行需要下分隔线"


def test_mid_column_is_header_bar_plus_scroll_area_with_three_cards() -> None:
    """中栏 = 抬头条（玻璃条）+ 滚动区，滚动区里依次是分类信息 / 题干 / 解析三张卡片。

    三张卡片是这一轮的落点：此前分类信息在审校页**没有编辑入口**，用户改一次来源就
    得退回题库录入页。
    """
    _, mid, _ = _columns(_review_html())
    # 抬头条一张（不带内边距的那种写法），三张卡片各带 p-3，所以这个计数只有 1
    assert mid.count(CARD) == 1, f"中栏「glass-panel rounded-2xl」应只剩抬头条，实得 {mid.count(CARD)}"
    assert SCROLL_BOX in mid, "中栏缺滚动区（flex-1 min-h-0 overflow-y-auto）"

    for card_id, tag, title in MID_CARDS:
        assert tag in mid, f"{card_id} 卡片容器不见了或类名漂了"
        assert f"<span>{title}</span>" in mid, f"{card_id} 的区块标题文字应为「{title}」"

    positions = [mid.index(tag) for _, tag, _ in MID_CARDS]
    assert positions == sorted(positions), "三张卡片的顺序应为 分类信息 → 题干 → 解析"
    meta_pos = positions[0]
    assert mid.count(SCROLL_BOX) == 1 and mid.index(SCROLL_BOX) < meta_pos, \
        "三张卡片必须在滚动区内部（否则中栏会被撑破、工具栏被顶出屏幕）"


def test_right_column_is_one_glass_card() -> None:
    """右栏「题目预览」同样一张卡片，预览区在卡片内滚动。"""
    _, _, right = _columns(_review_html())
    assert right.count(CARD) == 1, "右栏应恰好一张卡片"
    assert 'id="mrPreview"' in right, "预览容器必须在卡片内部"


# --- 标题与字段标签 -------------------------------------------------------


def test_column_headings_match_bank_style() -> None:
    """三栏标题都改成题库的「深色加粗 + 图标」，不再是 10px 灰色大写小字。"""
    left, mid, right = _columns(_review_html())
    assert HEADING_SPAN in left and "本次收录" in left
    assert HEADING_SPAN in right and "题目预览" in right
    # 中栏抬头不再叫「题面 / 解析」：题面与解析各占一张卡，标题跟着改成语义更准的说法
    assert HEADING_H3 in mid and "错题编辑" in mid
    assert "渲染预览" not in right, "右栏标题已更名「题目预览」"
    for chunk in (left, mid, right):
        assert "fa-" in chunk, "标题要带图标（题库每个区块标题都有）"


def test_classification_fields_cover_the_bank_input_form() -> None:
    """分类信息卡片的字段与题库录入表单一一对应，且带图标标签。"""
    _, mid, _ = _columns(_review_html())
    assert mid.count(META_LABEL_PLAIN) == 6, \
        f"分类信息应有 6 个普通字段 label（题型/难度/来源/学段/章节/小节），实得 {mid.count(META_LABEL_PLAIN)}"
    assert mid.count(META_LABEL_ICON) == 3, \
        f"带图标的标签字段应为 3 个（知识点/解题方法/自定义标签），实得 {mid.count(META_LABEL_ICON)}"

    for text in ("题型", "难度", "来源", "学段", "章节", "小节",
                 "知识点", "解题方法", "关联章节", "自定义标签"):
        assert f">{text}" in mid, f"缺字段标签「{text}」"

    # 控件本体：分类靠 select，来源与自定义标签是自由文本
    for element_id, tag in (
        ("mrQType", "select"), ("mrDifficulty", "select"),
        ("mrCompulsory", "select"), ("mrChapter", "select"), ("mrKnowledge", "select"),
        ("mrSource", "input"), ("mrCustomTags", "input"),
    ):
        assert f'id="{element_id}"' in mid, f"缺控件 {element_id}"
        block = mid.split(f'id="{element_id}"')[0].rsplit("<", 1)[-1]
        assert block.startswith(tag), f"{element_id} 应为 <{tag}>，实得 <{block[:12]}>"

    # 三级级联：后级默认禁用，等前级选完由 JS 放开（物化只留这一块 + 关联章节）
    assert mid.count("disabled") >= 4, "主分类与关联章节的后级下拉都应默认 disabled"


def test_edit_fields_are_labelled_like_bank_fields() -> None:
    """「题面」「解析」各有一个题库同款 label —— 此前题面根本没有标签。

    2026-09-16：解析那一行成了 flex 行（右半挂「追加 / 替换」开关），label 自身
    的 ``mt-3`` / ``mb-1`` 让位给外层行与识别区的间距 —— 所以它不再匹配 ``FIELD_LABEL``。
    这里按"同一套字体与字号"核对，题面那条继续用原常量守住它别漂。
    """
    _, mid, _ = _columns(_review_html())
    assert mid.count(FIELD_LABEL) == 1, "题面字段的 label 应保持题库同款写法"
    labels = re.findall(
        r'class="(?:mt-3 )?text-\[11px\] font-medium text-slate-500[^"]*flex items-center"', mid
    )
    assert len(labels) == 2, f"题面与解析各一个 label，实得 {len(labels)}"
    assert "<span>题面</span>" in mid, "题面字段缺少标签文字"
    assert "<span>解析</span>" in mid, "解析字段缺少标签文字"


def test_legacy_uppercase_micro_heading_is_gone_from_review() -> None:
    """旧的小灰字大写标题在审校页区间内必须清零 —— 这是本轮被替换掉的那套。"""
    review = _review_html()
    hits = review.count(LEGACY_MICRO_HEADING)
    assert hits == 0, f"审校页仍残留 {hits} 处旧样式标题"


def test_answer_ocr_entry_is_wired_end_to_end() -> None:
    """解析 OCR 四处都得接上：入口元素、模式开关、窗口导出、粘贴路由。

    少了任何一处，表现都是"点了没反应"或"粘贴的截图不知道跑哪去了"，
    而字符串断言各自都能过 —— 所以要在这里一次性把整条链核完。
    """
    review = _review_html()
    for element_id in ('id="mrAnswerOcrZone"', 'id="mrAnswerOcrFile"',
                       'id="mrAnswerOcrIdle"', 'id="mrAnswerOcrBusy"',
                       'id="mrAnswerOcrBusyText"', 'id="mrAnswerOcrDone"',
                       'id="mrAnswerOcrThumb"', 'id="mrAnswerOcrDoneText"',
                       'id="mrOcrModeAppend"', 'id="mrOcrModeReplace"'):
        assert element_id in review, f"解析 OCR 缺元素 {element_id}"
    for mode in ("append", "replace"):
        assert f"setReviewOcrMode('{mode}')" in review, f"「{mode}」按钮没接上处理函数"

    mistake_js = (PROJECT_ROOT / "static" / "js" / "mistake.js").read_text(encoding="utf-8")
    for name in ("setReviewOcrMode", "runReviewAnswerOcr", "renderReviewOcrModeUI"):
        assert f"window.{name} = {name};" in mistake_js, \
            f"mistake.js 没导出 {name}（模板里的 onclick 会打到 undefined）"
    assert "bindReviewAnswerOcr();" in mistake_js, "识别区的点击/拖入/键盘事件没绑"
    assert "fetch('/api/ocr'" in mistake_js, "没走既有的 /api/ocr 通道"

    ocr_js = (PROJECT_ROOT / "static" / "js" / "ocr.js").read_text(encoding="utf-8")
    # ocr.js 是**经典脚本**（<script src> 无 type=module），顶层 function 声明本身
    # 就是全局 —— 所以不用再抄一行导出，`window.cleanMathOcrText` 天然存在。
    # 这里守住「它仍是顶层声明」这件事：哪天有人把 ocr.js 包进 IIFE，这条会先红。
    assert re.search(r"^\s{0,8}function cleanMathOcrText\(", ocr_js, re.M), \
        "cleanMathOcrText 不再是顶层声明 —— 审校页会静默退回未清洗的原文"
    assert "window.cleanMathOcrText" in mistake_js, "审校页没复用 ocr.js 的文本清洗"
    assert "window.runReviewAnswerOcr(imageFile)" in ocr_js, \
        "ocr.js 的全局粘贴路由没接管审校页（粘贴的截图会掉进插图兜底）"
    assert "mistakeReviewView" in ocr_js, "粘贴路由没按审校页可见性判断"
    # 上面几条只能证明"代码写着"，分叉/顺序错照样绿。真实行为在
    # tests/js/ocr_paste_routing_check.js：它把 ocr.js 装起来丢一个 paste 事件进去。


# --- 卡片浮得出来 ---------------------------------------------------------


def test_columns_dropped_opaque_white_backdrop() -> None:
    """左右栏原本的 ``bg-white/60`` 必须去掉：白底上再放白色玻璃卡片，边界就看不出来了。

    审校页最外层是 ``bg-slate-50``，卡片靠这层浅灰底色才立得住。
    """
    left, _, right = _columns(_review_html())
    for name, chunk in (("左栏", left), ("右栏", right)):
        assert "bg-white/60" not in chunk, f"{name}还留着不透明白底，卡片会糊成一片"
    review = _review_html()
    assert "bg-slate-50" in review, "审校页应保持浅灰底色（卡片的对照面）"


def test_height_chain_survives_the_new_cards() -> None:
    """中栏必须继续撑起 ``flex-1 min-h-0`` 链路，否则卡片会被内容顶破、工具栏被挤走。

    这条防的是"加卡片后高度塌掉"：中间多套一层滚动区却忘了透传 flex 属性。
    """
    _, mid, _ = _columns(_review_html())
    assert SCROLL_BOX in mid, "滚动区本身缺 flex-1 min-h-0"
    # 两个编辑框各自留高度与可拖拉手柄，别把 h-64/h-40 与 resize-y 一起改没了
    for element_id, height in (("mrContent", "h-64"), ("mrAnswer", "h-40")):
        block = mid.split(f'id="{element_id}"')[1].split("></textarea>")[0]
        assert height in block, f"{element_id} 丢了高度档位 {height}"
        assert "resize-y" in block, f"{element_id} 丢了纵向拖拽手柄"


# --- 别把功能改丢 ---------------------------------------------------------


def test_review_ids_and_bindings_survive_the_restyle() -> None:
    """改版式不能改 id、也不能改事件绑定 —— JS 全靠 id 取元素。"""
    review = _review_html()
    for element_id in (
        'id="mrList"',
        'id="mrContent"',
        'id="mrAnswer"',
        'id="mrPreview"',
        'id="mrDocLabel"',
        'id="mrLockHint"',
        'id="mrLockHintText"',
        'id="mrRetryOneBtn"',
        'id="mrCropFigureBtn"',
        'id="mrCropAnswerBtn"',
        'id="mrClearPlaceholderBtn"',
        # 本轮新增：分类信息 + 学科显隐开关（JS 按这些 id 取元素）
        'id="mrMetaCard"',
        'id="mrContentCard"',
        'id="mrAnswerCard"',
        'id="mrMetaRow1"',
        'id="mrDifficultyWrap"',
        'id="mrTagsRow"',
        'id="mrRelChapterBox"',
        'id="mrCustomTagsRow"',
        'id="mrKnowledgeTagsChips"',
        'id="mrKnowledgeTagInput"',
        'id="mrSolveMethodTagsChips"',
        'id="mrSolveMethodTagInput"',
        'id="mrRelCompulsory"',
        'id="mrRelChapter"',
        'id="mrRelKnowledge"',
        'id="mrAddRelChapterBtn"',
        'id="mrRelChips"',
    ):
        assert element_id in review, element_id

    assert review.count('oninput="scheduleMistakeReviewSave()"') == 4, \
        "题面 / 解析 / 来源 / 自定义标签四个输入框都要接上防抖保存"
    assert review.count('onchange="scheduleMistakeReviewSave()"') == 3, \
        "题型 / 难度 / 小节三个直改直存的下拉要接防抖保存"
    for handler in ("onReviewCompulsoryChange()", "onReviewChapterChange()",
                    "onReviewRelCompulsoryChange()", "onReviewRelChapterChange()",
                    "onReviewMetaTagKey(event,&#39;knowledge&#39;)",
                    "onReviewMetaTagKey(event,&#39;solve&#39;)",
                    "addReviewRelatedChapter()"):
        assert handler in review, f"缺事件绑定 {handler}"


def test_latex_editors_keep_monospace() -> None:
    """两个编辑框保持等宽字体 —— LaTeX 源码列对齐靠它，不属于"对齐题库"的范围。"""
    review = _review_html()
    for element_id in ('id="mrContent"', 'id="mrAnswer"'):
        block = review.split(element_id)[1].split("></textarea>")[0]
        assert "font-mono" in block, f"{element_id} 丢了等宽字体"


def test_toolbar_still_sits_between_title_and_editor() -> None:
    """工具条顺序不变：mrRetryOneBtn 在 mrContent 之前。

    ``test_mistake_url_version_and_flow.py`` 用「这两个 id 之间」来圈定工具条区间，
    顺序颠倒会让那条测试**静默抠错块**（断言指向错误区间却可能仍绿）。
    """
    review = _review_html()
    assert review.index('id="mrRetryOneBtn"') < review.index('id="mrContent"')


# --- DOM 结构 -------------------------------------------------------------
#
# 上面那些断言都是**字符串**匹配。字符串有个盲区：少写一个 ``</div>``，字符串照样全在，
# 页面却可能整块布局崩掉（卡片吃掉后续所有兄弟节点）。所以这里再建一次真实的 DOM 树，
# 断言三栏的**嵌套形状**。这套核对在第 12 轮实施时先跑通过一次，才固化进来。

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[_Node] = []

    @property
    def cls(self) -> str:
        return self.attrs.get("class", "")

    @property
    def eid(self) -> str:
        return self.attrs.get("id", "")

    def kids(self, tag: str | None = None) -> list[_Node]:
        return [c for c in self.children if tag is None or c.tag == tag]

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", [])
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if self._stack[-1].tag == tag:
            self._stack.pop()
            return
        for k in range(len(self._stack) - 1, 0, -1):
            if self._stack[k].tag == tag:
                del self._stack[k:]
                return


@pytest.fixture(scope="module")
def review_view() -> _Node:
    tree = _Tree()
    tree.feed(INDEX_HTML.read_text(encoding="utf-8"))
    tree.close()
    return next(n for n in tree.root.walk() if n.eid == "mistakeReviewView")


def test_review_columns_nest_as_designed(review_view: _Node) -> None:
    """三栏的真实嵌套形状 —— 卡片真的是卡片的形状，不是靠字符串碰巧拼出来的。"""
    # 三栏容器不是审校页的第 1 个子元素：前面还有页头行与 mrGateBar 提示条
    tricol = next(c for c in review_view.kids("div") if c.kids("aside"))
    cols = [c for c in tricol.children if c.tag in ("aside", "section")]
    assert [c.tag for c in cols] == ["aside", "section", "aside"], "三栏顺序变了"
    left, mid, right = cols

    # 左栏：一张卡片，内部恰好「标题行 + 列表」
    left_card = left.kids("div")[0]
    assert "glass-panel" in left_card.cls
    assert [c.eid or c.tag for c in left_card.children] == ["div", "mrList"], \
        "左栏卡片内部形状变了（列表必须直接挂在卡片下，否则滚动区会跑出圆角）"

    # 中栏：抬头条 / 锁提示 / 工具栏 / 滚动区 —— 四个直接子元素
    mids = list(mid.children)
    assert len(mids) == 4, f"中栏直接子元素应为 4 个，实得 {len(mids)}"
    assert "glass-panel" in mids[0].cls and "rounded-2xl" in mids[0].cls, "抬头条不是玻璃条"
    assert mids[1].eid == "mrLockHint", "锁提示位置变了"
    assert len(mids[2].kids("button")) == 4, "工具栏按钮数变了"
    assert "overflow-y-auto" in mids[3].cls and "flex-1" in mids[3].cls, \
        "第 4 个子元素应是撑满剩余高度的滚动区"

    # 中栏滚动区：恰好三张卡片，顺序 分类信息 → 题干 → 解析
    scroller = mids[3]
    cards = scroller.kids("div")
    assert [c.eid for c in cards] == ["mrMetaCard", "mrContentCard", "mrAnswerCard"], \
        f"滚动区里应为三张卡片，实得 {[c.eid for c in cards]}"
    for card in cards:
        assert "glass-panel" in card.cls, f"{card.eid} 不是玻璃卡片"

    # 分类信息卡片：标题 + 三列第一行 + 三列级联行 + 标签行 + 关联章节 + 自定义标签
    meta_card, content_card, answer_card = cards
    assert [c.eid or c.tag for c in meta_card.children] == \
        ["h3", "mrMetaRow1", "div", "mrTagsRow", "mrRelChapterBox", "mrCustomTagsRow"], \
        "分类信息卡片内部形状变了"
    row1 = next(c for c in meta_card.kids("div") if c.eid == "mrMetaRow1")
    assert "grid-cols-3" in row1.cls, "第一行应是三列栅格"
    assert [c.eid for c in row1.kids("div")] == ["", "mrDifficultyWrap", ""], \
        "第一行应为 题型 / 难度(可收起) / 来源 三格"

    # 题干卡片：标题 + 字段 label + 「textarea 包裹层」，不多不少
    # （2026-09-18 起 textarea 外面套了一层 relative 包裹层，给「补这张图」浮层当定位参考）
    assert [c.eid or c.tag for c in content_card.children] == ["h3", "label", "mrContentWrap"], \
        "题干卡片内部形状变了（应为 标题 + label + textarea 包裹层）"
    assert "text-[11px]" in content_card.kids("label")[0].cls, "题面的字段 label 样式不符"
    content_wrap = next(c for c in content_card.children if c.eid == "mrContentWrap")
    assert "relative" in content_wrap.cls, \
        "包裹层要不是 relative，浮层就会以别处为原点，飘到编辑框外面去"
    assert [c.eid or c.tag for c in content_wrap.children] == \
        ["mrContent", "mrContentFigureAction"], "包裹层里应是 textarea + 补图浮层"

    # 解析卡片：标题 + label 行（标签 + 追加/替换开关）+ 识别区 + textarea 包裹层
    assert [c.eid or c.tag for c in answer_card.children] == \
        ["h3", "div", "mrAnswerOcrZone", "mrAnswerWrap"], "解析卡片内部形状变了"
    label_row = answer_card.kids("div")[0]
    assert [c.tag for c in label_row.children] == ["label", "div"], \
        "解析 label 行应只有「标签 + 模式开关」两块"
    assert "text-[11px]" in label_row.kids("label")[0].cls, "解析的字段 label 样式不符"
    assert [c.eid for c in label_row.kids("div")[0].kids("button")] == \
        ["mrOcrModeAppend", "mrOcrModeReplace"], "「追加 / 替换」开关的顺序或命名不符"

    ocr_zone = next(c for c in answer_card.children if c.eid == "mrAnswerOcrZone")
    assert "border-dashed" in ocr_zone.cls and "cursor-pointer" in ocr_zone.cls, \
        "识别区应是虚线、可点的上传区"
    assert ocr_zone.attrs.get("tabindex") == "0", "识别区要能 Tab 到（键盘可达）"
    assert [c.eid for c in ocr_zone.children] == \
        ["mrAnswerOcrFile", "mrAnswerOcrIdle", "mrAnswerOcrBusy", "mrAnswerOcrDone"], \
        "识别区三态（可上传 / 识别中 / 已识别）+ 文件输入的形状变了"
    # 只有「已识别」态带缩略图：认错图要能当场看出来
    assert any(c.tag == "img" for c in ocr_zone.walk()), "识别区没有缩略图位"

    answer_wrap = next(c for c in answer_card.children if c.eid == "mrAnswerWrap")
    assert "relative" in answer_wrap.cls, "解析框的包裹层同样要是定位参考"
    assert [c.eid or c.tag for c in answer_wrap.children] == \
        ["mrAnswer", "mrAnswerFigureAction"], "解析包裹层里应是 textarea + 补图浮层"

    # 右栏：一张卡片，内部恰好「标题行 + 预览区」
    right_card = right.kids("div")[0]
    assert "glass-panel" in right_card.cls
    assert [c.eid or c.tag for c in right_card.children] == ["div", "mrPreview"], \
        "右栏卡片内部形状变了"
