"""离线切块：把扫描页图按行投影切成候选题块（零 token、纯确定性）。

设计取舍
--------

本模块**只做视觉切分，不做题目语义判断**。它输出两类东西：

1. ``blocks``：被「强间隙」分隔开的候选题块 —— 一份多页卷子切出的粗粒度单元
2. 切分诊断：``gap_candidates``（中/强间隙位置）、``snap_points``（全部行间隙中点），
   以及 ``line_count`` / ``*_gap_px`` 等统计 —— 随 ``analysis.json`` 落盘供排查

为什么连诊断一起输出：扫描卷子里**题间距未必大于行间距**。实测样本（12 页物理卷）
的判断题 6 行之间间距仅 14–18 px，与普通换行完全一致，纯水平投影无法把它们切开 ——
这是本方案的已知边界（见实施计划 §13）。把间隙位置落盘，能直接看出「算法眼中哪里有
可切的地方」，判断一次误切是阈值问题还是版面问题。

界面上的人工纠错走**画框**（任意矩形遮蔽，见 :func:`merge_manual_boxes`）：框到哪、
哪归框，不需要「可切点」这种一维提示。

算法
----

1. 灰度化 → Otsu 自适应二值化（扫描件常有灰底/阴影，固定阈值不稳）
2. 逐行统计墨迹像素数得到行投影
3. 低于峰值 ``BLANK_PROFILE_RATIO`` 的行判为空白行，据此切出「文本行」
4. 相邻文本行的间隙按中位行距分级：
   - ``gap >= median * STRONG_GAP_FACTOR`` → 强间隙，作为题块边界
   - ``gap >= median * CANDIDATE_GAP_FACTOR`` → 中间隙，仅作候选切点
   - 其余（行内换行）忽略
5. 过矮的噪声块与页面边缘的孤立小块过滤掉

不依赖 numpy：``bytes.count`` 是 C 实现，单页约 0.1 s，足够快。
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from PIL import Image, ImageOps

# ----------------- 可调参数（集中在此，便于实测调参） -----------------

#: 渲染 PDF 页图时使用的 DPI。200 足够切块与前端预览，也控制落盘体积。
DEFAULT_RENDER_DPI = 200

#: 单个批次允许的最大页数（与实施计划 §6 一致）
DEFAULT_MAX_PAGES = 60

#: 行投影中「峰值 × 该比例」以下的行视为空白行
BLANK_PROFILE_RATIO = 0.02

#: 文本行最小高度占页高比例，低于此值视为噪声（横线、污点）
MIN_LINE_HEIGHT_RATIO = 0.0012

#: 强间隙相对「行内间距基准」的倍数 —— 决定题块边界
STRONG_GAP_FACTOR = 2.2

#: 候选切点相对「行内间距基准」的倍数 —— 仅用于前端吸附提示
CANDIDATE_GAP_FACTOR = 1.35

#: 间隙阈值按页高的下限兜底。这一项是实测调出来的关键参数：
#: 密集排版页面上存在大量 2–4 px 的微小间隙（上下标、公式、笔画贴近），
#: 会把「行内间距基准」压得极低。若兜底取固定像素值（曾用 16 px），
#: 恰好与该类页面的真实行间距（约 15 px）撞车 —— 实测样本第 10 页因此
#: 把一道例题的题干与选项切成 3 块。改成按页高比例后，阈值随分辨率缩放，
#: 200 DPI（页高 2339 px）下约为 21 px / 12 px。
MIN_STRONG_GAP_RATIO = 0.009
MIN_CANDIDATE_GAP_RATIO = 0.005

#: 题块最小高度占页高比例，低于此值的块被丢弃（页脚横线、水印残渣）
MIN_BLOCK_HEIGHT_RATIO = 0.006

#: 切块向上下外扩的余量占页高比例，避免切到字的上缘/下缘
BLOCK_PADDING_RATIO = 0.0025

#: 页眉 / 页脚保护区：与相邻块间距超过该比例、且自身矮于
#: ``HEADER_FOOTER_MAX_RATIO`` 的孤立块判为页眉页脚（如页码、考生信息栏）
ISOLATED_GAP_RATIO = 0.06
HEADER_FOOTER_MAX_RATIO = 0.035

# ----------------- 分栏相关参数（双栏 / 通栏排版自适应） -----------------
#
# 背景：上面的行投影是「整页一条投影」，块只有纵向范围。双栏卷子上左右栏
# 的文字在纵向上错开，同一水平位置总有一栏有字 → 切点消失，两栏内容被压进
# 同一个块。实测样本第 2 页：全宽投影只切出 6 块，其中一块高 1224 px（占页
# 面 52%），把左栏「三、简答题 + 第 11 题」和右栏「第 12 题」压在一起；按栏
# 分别投影则是 12 + 4 = 16 块。因此需要先判栏，再每栏各自投影。

#: 栏结构取值
COLUMN_SINGLE = "single"
COLUMN_DOUBLE = "double"

#: 栏结构的判定来源，写进 ``analysis.json`` 供前端显示
LAYOUT_SOURCE_TEXT = "text_layer"  # PDF 自带文本层，最准
LAYOUT_SOURCE_VISUAL = "visual"    # 纯图像分析（扫描件走这条）
LAYOUT_SOURCE_MANUAL = "manual"    # 人工在前端指定
LAYOUT_SOURCE_ASSUMED = "assumed"  # 兜底（图太小/无内容可判）

#: 判栏用的横向覆盖率剖面分辨率（横向格数 × 纵向带数）。
#: 200 格 ≈ 每格 0.5% 页宽，实测样本的分栏缝约 1%–2% 页宽，能落到 2–4 格里；
#: 纵向 400 带只为统计「有多少带在这一格有墨」，再密也不提升区分度。
COLUMN_PROFILE_BINS = 200
COLUMN_PROFILE_ROWS = 400

#: 剖面上一格被算作「有墨」的墨迹占比下限（抵消重采样的边缘渗色）
COLUMN_PROFILE_INK_CELL = 0.02

#: 中缝的扫描范围（占页宽）—— 真中缝应大致居中
COLUMN_GUTTER_SCAN_MIN = 0.35
COLUMN_GUTTER_SCAN_MAX = 0.65

#: 取中缝两侧「成栏内容」覆盖率的窗口（相对中缝的偏移，占页宽）。
#: 两侧各取一个区间取峰值，因此**要求中缝整体窄于 4% 页宽**，否则窗口会落在缝里
#: 取不到内容、把双栏页判成单栏（实测样卷的中缝是 1%–2%）。
#: 宁可漏判双栏（人工一键改）也不误判（会把内容从中间腰斩）——窗口刻意取得窄。
COLUMN_FLANK_INNER = 0.02
COLUMN_FLANK_OUTER = 0.12

#: 中缝覆盖率上限：超过说明这里有成片内容横跨，不是分栏缝。
#: 实测「缝覆盖率」双栏页 0.000–0.010（图像）/ 0.011–0.023（文本层），
#: 单栏页 0.038–0.222 / 0.085–0.286 —— 两档之间留了 4 倍以上余量，取 0.05。
DOUBLE_COLUMN_GUTTER_MAX_SHARE = 0.05

#: 中缝两侧覆盖率下限：低于它说明那一侧本来就不成栏（例如内容只占左半页的
#: 扫描偏移页），所谓「中缝」只是页边空白。实测双栏页两侧覆盖率 0.220–0.312，
#: 单栏页里靠这条兜住的是这类「半页内容」页（实测 0.021 / 0.106），取 0.12。
COLUMN_FLANK_MIN_SHARE = 0.12

#: 缝两侧各自至少要有一「段」这么宽的内容，才算成栏。
#:
#: 这条专治**通栏但每行被切成多段**的页面（选项 A/B/C/D 横排、表格行）：这类页
#: 每行都有好几个空白间隙，其中必然有一条落在中缝扫描窗里，于是「缝」成立、
#: 两侧覆盖率也都不低 —— 光靠覆盖率剖面会把它们判成双栏，然后在中间腰斩。
#: 真双栏每栏的每行都是一整段（实测样卷单栏宽 0.33–0.44 页宽），而分段页的每段
#: 只有 0.12 页宽。取 0.25：既容得下真栏的缩进折损，也够把分段页挡在外面。
COLUMN_MIN_FLANK_WIDTH = 0.25

#: 文本层判栏所需的最少文本行数。更少则任何统计都不可信，回退图像判栏。
COLUMN_MIN_TEXT_ROWS = 8

#: 置信度低于此值时前端提示「自动判定可能不准，请核对」。
LOW_LAYOUT_CONFIDENCE = 0.40

#: 人工指名「双栏」却没给分栏线、自动也判不出时的兜底分栏线（页面正中）。
DEFAULT_COLUMN_BOUNDARY = 0.5

#: 人工框的最小边长（占页宽/页高的比例）。前端拖拽过程中会不断产生退化的
#: 瞬时矩形，小于此值的一律丢弃，免得算出零面积的题块。
MANUAL_BOX_MIN_SIZE = 0.008

#: 判定「人工框吃掉了某个自动块」所需的最小重叠（归一化比例，两轴都要满足）。
#: 语义上是「碰到就算被吃掉」，留这点容差只是为了排掉边界相接时的浮点噪声。
MANUAL_BOX_OVERLAP_EPSILON = 0.002

#: 被人工框切剩下的残段低于此高度时丢弃（几像素的噪声条，裁出来的图空无一物）。
#: 只作用于残段，不影响人工框本身是否有效。
#:
#: 取 0.008 而不是更小的值：实测一份 16 页的双栏卷，人工框切出的 71 段残段里有 10 段
#: 落在 0.01%–0.77% 页高（页高 2340 px 时 0.3–18 px），都是框边擦过临行留下的半行
#: 空白或笔画末梢，裁出来的图肉眼看不到东西，却会在卡片列表里各占一张卡 —— 用户看到
#: 「怎么又多了几张空卡」。而同一份卷上**最小的真实自动块**占页高 1.33%，0.008 与它
#: 之间没有任何残段，也就是说这个阈值在这批数据上既不误杀、又把噪声清干净了。
#: 与 :data:`MANUAL_BOX_MIN_SIZE` 同值也说得通：比一个最小有效框还小的东西不是内容。
MANUAL_FRAGMENT_MIN_HEIGHT = 0.008

#: 判断块属于左栏还是右栏时的容差：双栏页上右栏块的 x_start 恰好等于分栏线，
#: 严格比较会因浮点误差把它判成左栏。
MANUAL_BOX_COLUMN_TOLERANCE = 0.01

# ----------------- 题号切点（PDF 文本层） -----------------
#
# 行投影只认几何：题与题之间的空白要**明显大于行距**才切得开。可密排的单栏正文里
# 题间距与行距是同一个量级 —— 实测一份卷子的正文页最大行间隙只有 18 px，而强间隙
# 阈值是 21 px（见 MIN_STRONG_GAP_RATIO），于是一整页 5 道题粘成 1 块。
#
# 这也不是阈值没调好：把阈值降下去，同一份卷的双栏页会从 15 块涨到 36 块、另一页
# 3 块涨到 22 块 —— 一个几何阈值不可能同时照顾紧排单栏与双栏。题目边界本来就是
# **排版语义**，不是几何量。
#
# 所以这里从 PDF 文本层取「行首是题号」的行，先用它认出「哪一条墨迹行带是这一行」，
# 再把切点钉在该行正上方的行间空白处。拿不到文本层（纯扫描件）就一个切点也不加，
# 行为与从前完全一致 —— 这条通道要么给出正确切点，要么什么都不做，不会把原本切得
# 对的页面改坏。
#
# 锚定这一步不能省：文本层给的 y0 是**字形框上缘**，而墨迹上缘一律落在它下方
# （实测真实 16 页卷 45 个题号行：中位低 13.8 px、正常范围 2.2–21.5 px）。若拿字形
# 框上缘直接去够「紧上方那道缝」，容差就得开大到一行行距，而开大之后又会够到上一
# 道题的缝 —— 实测那样会把上一题最后 1–4 行（多为选项行）切进新块，全卷 31 处。
# 先认行带再切，就没有这个「够不到就退一步」的自由度。

#: 题号行的正则：1–2 位数字 + ``.`` / ``．`` / ``、``
QUESTION_NUMBER_RE = re.compile(r"^(\d{1,2})\s*[.．、]")

#: 题号上限。卷子里不会出现比这更大的题号，超过了必然是别的东西。
QUESTION_NUMBER_MAX = 30

#: 同一页内相邻两个题号之间允许的最大跳号。题目是连续出现的，跳号说明中间那条
#: 「题号行」是误报（实测见过「10.20 m/s,则他在全程中的平均速度为」）。
QUESTION_NUMBER_MAX_JUMP = 5

#: 题号行必须落在版心左界这么多以内（占页宽）。题号是**段首**，一定顶在最左；
#: 而图注、发票联、页边标注这类误报都悬在页面中部或右侧。
QUESTION_LEFT_MARGIN_TOLERANCE = 0.02

#: 题号行的最小行宽（占页宽）。题干所在行都是一整行，误报都是短标签。
#: 实测真实题号行宽 0.27–0.90；误报（图注「5.一14 m」0.077、「2.40元」0.040）
#: 全部低于 0.08，取 0.15 两头都留了余量。
QUESTION_MIN_LINE_WIDTH = 0.15

#: 判定「这条墨迹行带就是题号行本人」的取整容差（占页高，下限 1 px）。
#:
#: 只吸收取整误差（``round(y0, 6) * height`` 会把 220 压成 219.9996），**不**承担
#: 墨迹相对字形框的偏移量 —— 那个偏移靠「先认行带、再要求它落在字形框内」解决，
#: 不由容差兜。
QUESTION_CUT_GAP_TOLERANCE_RATIO = 0.001

#: 汉字/中文标点。用来区分「6.2021年4月天和核心舱…」（真题号）与
#: 「10.20 m/s,则他在全程中的平均速度为」（上一题的续行）。
_CJK_CHAR_RE = re.compile(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]")


@dataclass(frozen=True)
class ColumnLayout:
    """一页的栏结构。

    ``boundary`` 是分栏线的归一化 x（0–1）：双栏时它是左右栏的分界，
    单栏时无意义（恒为 1.0，即「整页一栏」）。
    """

    mode: str = COLUMN_SINGLE
    boundary: float = 1.0
    source: str = LAYOUT_SOURCE_ASSUMED
    confidence: float = 1.0

    @property
    def is_double(self) -> bool:
        return self.mode == COLUMN_DOUBLE and 0.0 < self.boundary < 1.0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "boundary": round(self.boundary, 6),
            "source": self.source,
            "confidence": round(self.confidence, 4),
        }


def manual_column_layout(mode: str, boundary: float | None = None) -> ColumnLayout:
    """构造「人工指定」的栏结构，供后端解析前端参数时使用。

    ``boundary`` 省略时返回一个 ``is_double`` 为 False 的占位对象（仍在
    ``mode=double``）—— 算法层见到它会用自动检测的分栏线补齐，而不是把整页
    退回单栏。这样前端「先切成双栏、再拖分栏线」的两步操作中间态也是合法的。
    """

    if mode == COLUMN_SINGLE:
        return ColumnLayout(mode=COLUMN_SINGLE, boundary=1.0, source=LAYOUT_SOURCE_MANUAL)
    if boundary is None:
        return ColumnLayout(mode=COLUMN_DOUBLE, boundary=1.0, source=LAYOUT_SOURCE_MANUAL)
    try:
        value = float(boundary)
    except (TypeError, ValueError):
        return ColumnLayout(mode=COLUMN_DOUBLE, boundary=1.0, source=LAYOUT_SOURCE_MANUAL)
    if not (0.0 < value < 1.0):
        return ColumnLayout(mode=COLUMN_DOUBLE, boundary=1.0, source=LAYOUT_SOURCE_MANUAL)
    return ColumnLayout(
        mode=COLUMN_DOUBLE, boundary=value, source=LAYOUT_SOURCE_MANUAL
    )


@dataclass(frozen=True)
class BlockRegion:
    """一个候选题块在页面上的矩形范围（全部归一化到 0–1）。

    单栏页面上 ``x_start/x_end`` 恒为 0/1、``column_index`` 为 0；
    双栏页面上左栏块 ``column_index=1``（x 落在 ``[0, boundary]``），
    右栏块 ``column_index=2``（x 落在 ``[boundary, 1]``）。
    """

    page_no: int
    block_index: int
    y_start: float
    y_end: float
    x_start: float = 0.0
    x_end: float = 1.0
    column_index: int = 0

    @property
    def height(self) -> float:
        return self.y_end - self.y_start

    @property
    def width(self) -> float:
        return self.x_end - self.x_start


@dataclass
class PageAnalysis:
    """单页切块结果。"""

    page_no: int
    width: int
    height: int
    #: 本页栏结构（单栏 / 双栏）。默认单栏，便于旧调用方与旧数据兼容。
    layout: ColumnLayout = field(default_factory=ColumnLayout)
    blocks: list[BlockRegion] = field(default_factory=list)
    #: 中/强间隙的中点（诊断口径：算法认为这里可以切）
    gap_candidates: list[float] = field(default_factory=list)
    #: **全部**相邻文本行间隙的中点 —— 诊断口径：记录哪些位置切下去不会碰到字。
    snap_points: list[float] = field(default_factory=list)
    line_count: int = 0
    base_gap_px: float = 0.0  # 偏低分位间隙（估计「行内间距」基准）
    median_gap_px: float = 0.0
    strong_gap_px: int = 0
    candidate_gap_px: int = 0
    ignored_block_count: int = 0  # 被当作页眉/页脚/噪声丢弃的块数
    #: 按栏的间隙中点：``{栏号: [行间隙中点 y]}``。双栏页面左右两栏的行位置不同，
    #: 合并起来看会把「这一栏的间隙」和「隔壁栏的字」混在一张表里 —— 诊断时按栏取更准。
    #: 单栏页面只有 key 0。
    column_snap_points: dict = field(default_factory=dict)
    #: 按栏的候选切点，语义同上。
    column_gap_candidates: dict = field(default_factory=dict)
    #: 由 PDF 文本层题号行定位出来的切点（归一化 y）。
    #:
    #: 与 ``gap_candidates`` 的区别：那些是**几何**上可以切的位置（间隙够大），
    #: 这些是**语义**上的题目边界（行首是题号）。密排卷子上后者才是真边界，前者
    #: 一个都没有。留在文件里是为了让「这一页怎么切出这么多块」查得到。
    question_cut_points: list = field(default_factory=list)
    #: 人工框选的矩形（归一化 ``(x0, y0, x1, y1)``，y 向下）。
    #:
    #: ⚠️ 它**不并入** ``blocks``。``blocks`` 永远是「自动切块 + 人工切线」的
    #: 基线，人工框只是叠在上面的一层遮蔽物（见 :func:`merge_manual_boxes`）。
    #: 这样删掉一个画错的框重新应用，被它压住的自动块能原样回来；若把合并结果
    #: 写回 ``blocks``，被吸收的碎片就再也找不回了。
    manual_boxes: list = field(default_factory=list)
    #: 人工合并组：一道题被分栏/排版切成多块时，用户把它们并为一条记录。
    #:
    #: 与 ``manual_boxes`` 同层但语义不同：框是「遮蔽替换」，合并是「多块拼成
    #: 一条」。每项形如 ``{"id": "m1", "rects": [[x0,y0,x1,y1], …],
    #: "direction": "v"|"h", "primary": 0}`` —— ``rects`` 用**几何**而不是块序号
    #: 定位成员，这样重切导致块序号变化后仍能按重叠把成员找回来。
    manual_merges: list = field(default_factory=list)
    #: 被用户删掉的题块：一串 ``[x0, y0, x1, y1]`` 矩形。
    #:
    #: 删除必须记在这里而不是只删数据库记录 —— 每次切题/画框/合并都是「整页删旧插新」
    #: 重建，只删记录的话下一动它就会复活。存几何而不是块序号，理由同上：重切后块号
    #: 会变，几何不变。
    hidden_blocks: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_no": self.page_no,
            "width": self.width,
            "height": self.height,
            "line_count": self.line_count,
            "layout": self.layout.to_dict(),
            "base_gap_px": round(self.base_gap_px, 2),
            "median_gap_px": round(self.median_gap_px, 2),
            "strong_gap_px": self.strong_gap_px,
            "candidate_gap_px": self.candidate_gap_px,
            "ignored_block_count": self.ignored_block_count,
            "blocks": [
                {
                    "block_index": b.block_index,
                    "y_start": round(b.y_start, 6),
                    "y_end": round(b.y_end, 6),
                    "x_start": round(b.x_start, 6),
                    "x_end": round(b.x_end, 6),
                    "column_index": b.column_index,
                }
                for b in self.blocks
            ],
            "gap_candidates": [round(y, 6) for y in self.gap_candidates],
            "snap_points": [round(y, 6) for y in self.snap_points],
            #: 语义切点（题号行）—— 与 snap_points 同为诊断口径
            "question_cut_points": [round(y, 6) for y in self.question_cut_points],
            #: 双栏时的分栏线 x（单栏为 1.0）—— 前端画分栏线用
            "column_boundary": round(self.layout.boundary, 6),
            #: 按栏的吸附池与候选切点（key 为栏号：0 整页/跨栏、1 左栏、2 右栏）
            "column_snap_points": {
                str(key): [round(y, 6) for y in values]
                for key, values in self.column_snap_points.items()
            },
            "column_gap_candidates": {
                str(key): [round(y, 6) for y in values]
                for key, values in self.column_gap_candidates.items()
            },
            "manual_boxes": [
                [round(value, 6) for value in box] for box in self.manual_boxes
            ],
            "manual_merges": self.manual_merges,
            "hidden_blocks": [
                [round(value, 6) for value in rect] for rect in self.hidden_blocks
            ],
        }


class PageSplitError(RuntimeError):
    """切块过程中的可预期失败（文件损坏、页数超限等）。"""


# ----------------- 页图渲染 -----------------


def _is_image_path(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def render_source_to_pages(
    source_path: str | Path,
    out_dir: str | Path,
    *,
    dpi: int = DEFAULT_RENDER_DPI,
    max_pages: int = DEFAULT_MAX_PAGES,
    stem: str = "page",
) -> list[Path]:
    """把 PDF（或图片）转成落盘的页图，返回页图路径列表。

    - PDF：用 PyMuPDF 逐页渲染为 PNG（``page_1.png`` 起）
    - 图片：统一转存为 PNG（单图批次即 1 页）
    - 页数超过 ``max_pages`` 时拒绝，避免一次误传把磁盘写满
    """

    source = Path(source_path)
    if not source.is_file():
        raise PageSplitError(f"找不到文件: {source}")
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if _is_image_path(source):
        try:
            with Image.open(source) as image:
                image.load()
                # 统一成 RGB / PNG，避免前端拿到 CMYK 或调色板图
                normalized = ImageOps.exif_transpose(image).convert("RGB")
                out_path = target_dir / f"{stem}_1.png"
                normalized.save(out_path, format="PNG", optimize=True)
        except Exception as exc:  # noqa: BLE001 - 统一转成可读的业务错误
            raise PageSplitError(f"图片读取失败: {exc}") from exc
        return [out_path]

    if source.suffix.lower() != ".pdf":
        raise PageSplitError(f"不支持的文件类型: {source.suffix or '(无扩展名)'}")

    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确指引
        raise PageSplitError(
            "本地 Python 环境未安装 PyMuPDF，请运行 pip install pymupdf 后重试。"
        ) from exc

    pages: list[Path] = []
    try:
        with pymupdf.open(source) as document:
            total = document.page_count
            if total <= 0:
                raise PageSplitError("PDF 里没有任何页面。")
            if total > max_pages:
                raise PageSplitError(
                    f"PDF 共 {total} 页，超过单批次上限 {max_pages} 页。请拆分后再导入。"
                )
            for index in range(total):
                page = document[index]
                pixmap = page.get_pixmap(dpi=dpi)
                out_path = target_dir / f"{stem}_{index + 1}.png"
                pixmap.save(out_path)
                pages.append(out_path)
    except PageSplitError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PageSplitError(f"PDF 渲染失败: {exc}") from exc
    return pages


def extract_pdf_text_rows(
    source_path: str | Path,
) -> dict[int, list[tuple[float, float, float, float]]]:
    """提取 PDF 每页的**文本行**归一化坐标，供判栏使用。

    返回 ``{页号(1 起): [(x0, x1, y0, y1), ...]}``。取行级（``get_text("dict")``
    的 lines）而不是块级（``get_text("blocks")``）：块会把左右两栏的内容合并成
    一个通栏块，实测样本上正是这一点让 p9–p13 被误判成双栏（选项 A/B/C/D 横向
    一排时，块级坐标是 [0.02, 0.92]，行级则是 4 个独立窄行）。

    非 PDF、纯扫描件（无文本层）、或读取失败一律返回空 dict —— 调用方据此回退到
    图像判栏。判栏只需要行的位置，不需要内容，因此这里对文本只做「非空」判断。
    """

    source = Path(source_path)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        return {}
    try:
        import pymupdf
    except ImportError:
        return {}

    return {
        page_no: [(x0, x1, y0, y1) for x0, x1, y0, y1, _text in page_lines]
        for page_no, page_lines in extract_pdf_text_lines(source_path).items()
    }


def extract_pdf_text_lines(
    source_path: str | Path,
) -> dict[int, list[tuple[float, float, float, float, str]]]:
    """提取 PDF 每页的文本行：``{页号(1 起): [(x0, x1, y0, y1, 文字), …]}``。

    比 :func:`extract_pdf_text_rows` 多带一行文字 —— 题号切点必须看内容（行首是不是
    题号），只看坐标无从判断。判栏只需要位置，所以那边继续用不带文字的那份。
    返回坐标与它完全一致（同一次读取、同一套过滤）。

    非 PDF、纯扫描件（无文本层）、或读取失败一律返回空 dict，调用方据此回退。
    """

    source = Path(source_path)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        return {}
    try:
        import pymupdf
    except ImportError:
        return {}

    lines: dict[int, list[tuple[float, float, float, float, str]]] = {}
    try:
        with pymupdf.open(source) as document:
            for index in range(document.page_count):
                page = document[index]
                width = float(page.rect.width) or 1.0
                height = float(page.rect.height) or 1.0
                page_lines: list[tuple[float, float, float, float, str]] = []
                for block in page.get_text("dict").get("blocks", []):
                    for line in block.get("lines", []):
                        x0, y0, x1, y1 = (float(value) for value in line["bbox"])
                        if x1 <= x0 or y1 <= y0:
                            continue
                        text = "".join(
                            str(span.get("text") or "")
                            for span in line.get("spans", [])
                        ).strip()
                        if not text:
                            continue
                        page_lines.append(
                            (
                                x0 / width,
                                x1 / width,
                                y0 / height,
                                y1 / height,
                                text,
                            )
                        )
                if page_lines:
                    lines[index + 1] = page_lines
    except Exception:  # noqa: BLE001 - 判栏/切点是增强项，读失败不该让切题失败
        return {}
    return lines


def _question_rest_is_plausible(rest: str) -> bool:
    """题号后面那截文字像不像题面开头（挡掉同形误报）。

    实测过的三种同形误报，都在这里挡：

    * 章节号「1.5.1 加速度及其概念」「2.2匀变速直线运动速度与时间关系」——
      数字后面紧跟数字；
    * 单位/续行「10.20 m/s,则他在全程中的平均速度为」「3.tls」「2.5 cm」——
      数字后面紧跟空白或字母；
    * 答案册的「5.[A][B]I .[D]」—— 方括号开头。

    而「6.2021年4月天和核心舱发射成功」是真题号（题面以年份开头），所以判据是
    「数字后面是不是**汉字**」而不是「后面有没有数字」。
    """

    if not rest:
        return False
    if rest[0] in "[【":
        return False
    digits = re.match(r"\d+", rest)
    if digits:
        following = rest[digits.end(): digits.end() + 1]
        return bool(_CJK_CHAR_RE.match(following))
    if re.match(r"[A-Za-z]{2,}", rest):
        return False
    return True


def _question_cut_points(
    text_lines: Sequence[tuple[float, float, float, float, str]],
) -> list[tuple[float, float]]:
    """从一个条带（整页，或双栏中的一栏）的文本行里挑出题号行。

    返回值是**每道题号行的字形框区间** ``(y0, y1)``（归一化 y），不是切点本身：
    调用方要用这个区间去认得「哪一条墨迹行带是这一行」，再切在它正上方那道缝上。
    只给 ``y0`` 是给不出这层身份的 —— 字形框上缘离墨迹上缘一大截（实测中位
    13.8 px），拿它去够缝隙只能退而求其次切到上一行。

    坐标是条带口径的归一化 y。条带内按阅读顺序（y 升序）扫，题号必须递增，且相邻
    题号的跳号不超过 :data:`QUESTION_NUMBER_MAX_JUMP` —— 这条同时挡掉「先出现一个
    假题号、把后面真题号全比下去」的连锁失效（实测在双栏页上出现过）。
    """

    if not text_lines:
        return []
    left_margin = min(row[0] for row in text_lines)
    left_limit = left_margin + QUESTION_LEFT_MARGIN_TOLERANCE
    cuts: list[tuple[float, float]] = []
    last_number = 0
    for x0, x1, y0, y1, text in sorted(text_lines, key=lambda row: row[2]):
        match = QUESTION_NUMBER_RE.match(text)
        if match is None:
            continue
        number = int(match.group(1))
        if not 1 <= number <= QUESTION_NUMBER_MAX:
            continue
        if x1 - x0 < QUESTION_MIN_LINE_WIDTH:
            continue
        if x0 > left_limit:
            continue
        if not _question_rest_is_plausible(text[match.end():]):
            continue
        if number <= last_number:
            continue
        if last_number and number - last_number > QUESTION_NUMBER_MAX_JUMP:
            continue
        last_number = number
        cuts.append((y0, y1))
    return cuts


# ----------------- 行投影与切块 -----------------


def _otsu_threshold(histogram: Sequence[int], total: int) -> int:
    """Otsu 自适应阈值：扫描件常有灰底与阴影，固定阈值不稳。"""

    if total <= 0:
        return 128
    sum_all = sum(i * histogram[i] for i in range(256))
    sum_background = 0.0
    weight_background = 0
    best_threshold = 128
    best_variance = -1.0
    for threshold in range(256):
        weight_background += histogram[threshold]
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += threshold * histogram[threshold]
        mean_background = sum_background / weight_background
        mean_foreground = (sum_all - sum_background) / weight_foreground
        variance = (
            weight_background * weight_foreground
            * (mean_background - mean_foreground) ** 2
        )
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def _ink_profile(gray: Image.Image, threshold: int) -> list[int]:
    """逐行统计墨迹像素数。

    注意这里是 ``<=`` 而不是 ``<``：Otsu 返回的阈值本身即「背景侧的上界」。
    对只有纯黑与纯白两种灰阶的图（例如二值化后的扫描件或测试用合成图），
    最优阈值恰好是 0，若用 ``<`` 则会漏掉全部墨迹、整页判为空白。
    """

    width, height = gray.size
    binary = gray.point(lambda value: 255 if value <= threshold else 0)
    data = binary.tobytes()
    return [data[y * width:(y + 1) * width].count(255) for y in range(height)]


def _text_lines(
    profile: Sequence[int],
    height: int,
    blank_threshold: int,
    min_line_height: int,
) -> list[tuple[int, int]]:
    """把行投影切成「文本行」的像素区间（闭区间）。"""

    lines: list[tuple[int, int]] = []
    in_text = False
    start = 0
    for y in range(height):
        is_text = profile[y] > blank_threshold
        if is_text and not in_text:
            in_text = True
            start = y
        elif not is_text and in_text:
            in_text = False
            if y - start >= min_line_height:
                lines.append((start, y - 1))
    if in_text and height - start >= min_line_height:
        lines.append((start, height - 1))
    return lines


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2])


def _low_percentile(values: Sequence[float], fraction: float = 0.25) -> float:
    """取偏低分位数，用来估计「行内间距」这一基准。

    不能用中位数：一页上「行内换行」通常多于「题间分隔」，但短页面（例如只有
    3 个间隙、其中 2 个是题间大间隙）会把中位数直接拉到题间距的量级，导致阈值
    虚高、整页切不开。偏低分位对这类页面明显更稳。
    """

    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(len(ordered) * fraction)
    return float(ordered[min(index, len(ordered) - 1)])


def _drop_headers_footers(
    blocks: list[tuple[int, int]],
    height: int,
) -> list[tuple[int, int]]:
    """丢弃页面顶部/底部那些「与正文隔得很远、自身又很矮」的孤立块。

    典型目标是页码、扫描 App 水印、单独一行的考生信息栏。判据刻意保守：
    必须同时满足「自身矮」与「间距极大」，避免误删真正短小的题目。
    """

    if len(blocks) <= 1:
        return blocks

    isolated_gap = height * ISOLATED_GAP_RATIO
    max_height = height * HEADER_FOOTER_MAX_RATIO

    kept = list(blocks)
    # 只检查首尾，中间的矮块（小题）不参与判定
    while len(kept) > 1:
        first = kept[0]
        gap_after = kept[1][0] - first[1] - 1
        if (first[1] - first[0] + 1) <= max_height and gap_after >= isolated_gap:
            kept.pop(0)
        else:
            break
    while len(kept) > 1:
        last = kept[-1]
        gap_before = last[0] - kept[-2][1] - 1
        if (last[1] - last[0] + 1) <= max_height and gap_before >= isolated_gap:
            kept.pop()
        else:
            break
    return kept


@dataclass
class _RegionSplit:
    """某一个竖直条带（整页，或双栏中的一栏）内的行投影切块结果。

    块区间是**相对该条带**的像素坐标，由调用方平移回全页归一化坐标。
    """

    blocks: list[tuple[int, int]]
    gap_candidates: list[float]
    snap_points: list[float]
    #: 真正生效的题号切点（归一化 y），供 analysis.json 留痕
    question_cut_points: list[float]
    line_count: int
    base_gap_px: float
    median_gap_px: float
    strong_gap_px: int
    candidate_gap_px: int
    ignored_block_count: int


def _empty_split() -> _RegionSplit:
    return _RegionSplit(
        blocks=[],
        gap_candidates=[],
        snap_points=[],
        question_cut_points=[],
        line_count=0,
        base_gap_px=0.0,
        median_gap_px=0.0,
        strong_gap_px=0,
        candidate_gap_px=0,
        ignored_block_count=0,
    )


def _forced_cut_span(position: float | Sequence[float]) -> tuple[float, float]:
    """把一条语义切点展开成 ``(字形框上缘, 字形框下缘)`` 的归一化 y。

    传单个数值时按零高度处理，仅供只关心「这一行在哪」的调用方与测试使用 ——
    零高度区间意味着只有「墨迹上缘恰好落在字形框上缘」的行才认得出来。
    """

    if isinstance(position, (int, float)):
        value = float(position)
        return (value, value)
    values = [float(item) for item in position]
    if len(values) < 2:
        value = values[0] if values else 0.0
        return (value, value)
    first, second = values[0], values[1]
    return (first, second) if first <= second else (second, first)


def _split_region(
    gray: Image.Image,
    forced_cuts: Sequence[float | Sequence[float]] | None = None,
) -> _RegionSplit:
    """在一个竖直条带内做行投影切块（相对该条带的像素坐标）。

    「条带」就是整页（单栏）或某一栏（双栏）。抽成独立函数是为了让单栏与分栏
    复用同一套阈值与过滤逻辑 —— 分栏在算法上不该与单栏有任何分歧，唯一差别是
    输入图像被横向切开、输出坐标再平移回去。

    ``forced_cuts`` 是**语义切点**（归一化 y，或 ``(y0, y1)`` 字形框区间）：
    题号行的位置，见 :func:`_question_cut_points`。它们与强间隙是**并集**关系 ——
    只加切点、不减切点，所以对没给语义切点的页面（拿不到文本层、或一个题号都没
    认出来）行为与从前逐字一致。

    每个切点的落法分两步：先在行带里认出「哪一条是题号行」（不早于字形框上缘的
    第一条行带，且必须落在该行字形框内），再切在这一条**正上方**那道缝上。认不
    出来就一刀不切 —— 没有「退一步切上一行」这条路径，这条路径会把上一道题最后
    一行切进新块。
    """

    width, height = gray.size
    if width <= 0 or height <= 0:
        return _empty_split()

    threshold = _otsu_threshold(gray.histogram(), width * height)
    profile = _ink_profile(gray, threshold)
    peak = max(profile) if profile else 0
    if peak <= 0:
        return _empty_split()

    blank_threshold = max(2, int(peak * BLANK_PROFILE_RATIO))
    min_line_height = max(2, int(height * MIN_LINE_HEIGHT_RATIO))
    lines = _text_lines(profile, height, blank_threshold, min_line_height)
    if not lines:
        return _empty_split()

    # 相邻文本行的间隙
    gaps: list[tuple[int, int]] = []  # (间隙像素, 位于第几个 line 之后)
    for index in range(len(lines) - 1):
        gap = lines[index + 1][0] - lines[index][1] - 1
        gaps.append((gap, index))

    positive_gaps = [gap for gap, _ in gaps if gap > 0]
    base_gap = _low_percentile(positive_gaps)
    median_gap = _median(positive_gaps)
    min_strong = max(6, int(height * MIN_STRONG_GAP_RATIO))
    min_candidate = max(3, int(height * MIN_CANDIDATE_GAP_RATIO))
    strong_gap = max(min_strong, int(base_gap * STRONG_GAP_FACTOR))
    candidate_gap = max(
        min_candidate,
        min(int(base_gap * CANDIDATE_GAP_FACTOR), strong_gap - 1 if strong_gap > 1 else 1),
    )

    # 语义切点（题号行）→ 先认出「哪一条行带是这一行」，再切在它正上方那道缝上。
    # 认不出来就一刀不切：宁可少切，也不能切进上一道题的尾巴里。
    forced_indices: set[int] = set()
    applied_forced: list[float] = []
    gap_tolerance = max(1.0, height * QUESTION_CUT_GAP_TOLERANCE_RATIO)
    for position in forced_cuts or []:
        span_top, span_bottom = _forced_cut_span(position)
        target = span_top * height
        index: int | None = None
        for candidate, band in enumerate(lines):
            # 题号行的墨迹 = 不早于字形框上缘的第一条行带。
            if band[0] >= target - gap_tolerance:
                index = candidate
                break
        if index is None or index == 0:
            # 整页没有行带，或题号行之上再没有行带（页顶那一道没有缝可切）。
            continue
        if lines[index][0] > span_bottom * height + gap_tolerance:
            # 这条行带的首行已经越过题号行的字形框下缘：题号行的墨迹跟上方内容粘在
            # 同一条行带里（真实卷里是右侧照片/图形把行间空白吃光了），这里没有白
            # 缝，硬切会切穿图形。
            continue
        gap = lines[index][0] - lines[index - 1][1] - 1
        if gap <= 0:
            continue
        forced_indices.add(index - 1)
        applied_forced.append(
            round((lines[index - 1][1] + lines[index][0]) / 2.0 / height, 6)
        )

    # 强间隙 → 题块边界；语义切点同样是一道边界
    raw_blocks: list[tuple[int, int]] = []
    current_start = lines[0][0]
    current_end = lines[0][1]
    for gap, index in gaps:
        if gap >= strong_gap or index in forced_indices:
            raw_blocks.append((current_start, current_end))
            current_start = lines[index + 1][0]
        current_end = lines[index + 1][1]
    raw_blocks.append((current_start, current_end))

    # 过矮的块视为噪声
    min_block_height = max(3, int(height * MIN_BLOCK_HEIGHT_RATIO))
    raw_blocks = [(a, b) for a, b in raw_blocks if (b - a + 1) >= min_block_height]
    before_filter = len(raw_blocks)
    raw_blocks = _drop_headers_footers(raw_blocks, height)

    # 候选切点：中/强间隙的中点（随 analysis.json 落盘，供排查误切）
    gap_candidates: list[float] = []
    for gap, index in gaps:
        if gap >= candidate_gap:
            middle = (lines[index][1] + lines[index + 1][0]) / 2.0
            gap_candidates.append(round(middle / height, 6))

    # 吸附点：**全部**行间隙中点（诊断口径：这些位置切下去不会碰到字）
    snap_points = sorted(
        {
            round((lines[index][1] + lines[index + 1][0]) / 2.0 / height, 6)
            for index in range(len(lines) - 1)
        }
    )

    return _RegionSplit(
        blocks=raw_blocks,
        gap_candidates=sorted(set(gap_candidates)),
        snap_points=snap_points,
        question_cut_points=sorted(set(applied_forced)),
        line_count=len(lines),
        base_gap_px=base_gap,
        median_gap_px=median_gap,
        strong_gap_px=strong_gap,
        candidate_gap_px=candidate_gap,
        ignored_block_count=before_filter - len(raw_blocks),
    )


# ----------------- 栏结构判定 -----------------
#
# 判据只有一条：**横向覆盖率剖面上的「中缝」**。
#
# 把页面横向分成 N 格、纵向分成 M 带，对每一格统计「有墨的带里有多少带在这一格上
# 也有墨」，就得到一条覆盖率剖面。双栏页面上两栏各自成栏、中间那条缝几乎不承载
# 内容，剖面上会出现一个明显的谷；单栏（含「内容只占半页、另半页留白」）的谷
# 要么不存在，要么两侧根本不成栏。
#
# 为什么不用更朴素的判据（都实测过，都被真实样本打穿）：
#
# * 整页列投影找竖直空白谷 —— 分栏缝被页眉条形码、通栏图截断（16 页里 5 页判错）；
# * 分带列投影 —— 带边界落在行间空白时整带全判为缝（16 页全判双栏）；
# * 「一行内部是否被切成两段」—— 对**通栏页**失灵：选项 A/B/C/D 横向一排时每行都有
#   多段，实测 16 页里把 7 页单栏判成双栏；
# * 文本层「通栏块占比 / 左右锚定块的 x 中位数」—— 文本块会跨栏合并，通栏与双栏
#   的统计分布重叠，判不出（实测 16 页错 4 页）。
#
# 覆盖率剖面的判据对上述干扰都免疫：单个跨栏元素只是让谷抬高一点点，不足以
# 越过阈值；而真双栏的谷是贯穿整页高度的。
#
# 实测（错题1-物理.pdf，16 页：p1–p8 双栏、p9–p16 单栏，逐页核对文本行 x 分布）：
# 文本层路径 16/16、图像路径 16/16（文本层是独立第二意见，扫件无文本层时走图像）。


def _column_coverage_profile(
    gray: Image.Image,
) -> tuple[list[int], int, list[tuple[float, float]]]:
    """算横向覆盖率剖面，返回 ``(剖面, 有墨的纵向带数, 内容段)``。

    剖面第 b 格 = 在有墨的那些纵向带里，有多少带在这一格上也有墨。二值化用 Otsu
    （扫描件灰底用固定阈值不稳，与行投影共用同一套，且沿用「墨迹 = 255」的约定）。

    整页二值图先按 BOX 缩到 ``COLUMN_PROFILE_BINS × COLUMN_PROFILE_ROWS`` —— BOX
    取均值，于是每格的值就是该格的墨迹占比乘以 255，判定「这格有墨」只需比一次
    阈值，省掉逐像素的 Python 循环（200 DPI 的整页图逐像素跑一圈要 2–3 秒）。

    「内容段」是每带内连续有墨的横向区间（归一化 x 范围），用来量每侧最宽的一段 ——
    判据要区分「双栏」与「通栏但每行多段」，只看覆盖率是不够的。
    """

    width, height = gray.size
    if width <= 0 or height <= 0:
        return [], 0, []

    threshold = _otsu_threshold(gray.histogram(), width * height)
    binary = gray.point(lambda value: 255 if value <= threshold else 0)
    small = binary.resize((COLUMN_PROFILE_BINS, COLUMN_PROFILE_ROWS), Image.BOX)
    data = small.tobytes()
    # 格值 = 255 × 墨迹占比，因此「占比 > CELL」即「格值 > 255 × CELL」。
    # ⚠️ 与 _ink_profile 同一约定（墨迹是 255）—— 弄反会让整页每格都算有墨、
    # 中缝直接消失、所有页面被判成单栏。
    cutoff = int(255 * COLUMN_PROFILE_INK_CELL)

    coverage = [0] * COLUMN_PROFILE_BINS
    runs: list[tuple[float, float]] = []
    inked_bands = 0
    for band in range(COLUMN_PROFILE_ROWS):
        base = band * COLUMN_PROFILE_BINS
        band_has_ink = False
        run_start = -1
        for column in range(COLUMN_PROFILE_BINS + 1):
            inked = column < COLUMN_PROFILE_BINS and data[base + column] > cutoff
            if inked:
                coverage[column] += 1
                band_has_ink = True
                if run_start < 0:
                    run_start = column
            elif run_start >= 0:
                runs.append((run_start / COLUMN_PROFILE_BINS, column / COLUMN_PROFILE_BINS))
                run_start = -1
        if band_has_ink:
            inked_bands += 1
    return coverage, inked_bands, runs


def _layout_from_coverage(
    coverage: Sequence[int],
    inked: int,
    *,
    source: str,
    runs: Sequence[tuple[float, float]] | None = None,
) -> ColumnLayout:
    """把横向覆盖率剖面判成栏结构。图像与文本层两条路径共用。

    ``runs`` 是本页各「内容段」的归一化 x 区间（图像路径给每带有墨的横向区间，
    文本层路径直接给文本行）。给了就额外要求缝两侧各有一段 :data:`COLUMN_MIN_FLANK_WIDTH`
    宽的成栏内容 —— 这是区分「双栏」与「通栏但每行多段（选项横排/表格）」的关键。
    """

    total = max(1, inked)
    low = int(COLUMN_GUTTER_SCAN_MIN * COLUMN_PROFILE_BINS)
    high = int(COLUMN_GUTTER_SCAN_MAX * COLUMN_PROFILE_BINS)
    window = list(coverage[low:high]) or [0]
    gutter = min(window)
    gutter_index = low + window.index(gutter)

    inner = int(COLUMN_FLANK_INNER * COLUMN_PROFILE_BINS)
    outer = int(COLUMN_FLANK_OUTER * COLUMN_PROFILE_BINS)
    left_flank = max(
        coverage[max(0, gutter_index - outer):max(1, gutter_index - inner)] or [0]
    )
    right_flank = max(
        coverage[
            min(COLUMN_PROFILE_BINS, gutter_index + inner):
            min(COLUMN_PROFILE_BINS, gutter_index + outer)
        ]
        or [0]
    )

    gutter_share = gutter / total
    flank_share = min(left_flank, right_flank) / total

    gutter_x = (gutter_index + 0.5) / COLUMN_PROFILE_BINS
    if runs is None:
        span_share = COLUMN_MIN_FLANK_WIDTH
    else:
        left_span = max((x1 - x0 for x0, x1 in runs if x1 <= gutter_x), default=0.0)
        right_span = max((x1 - x0 for x0, x1 in runs if x0 >= gutter_x), default=0.0)
        span_share = min(left_span, right_span)
    flank_is_column = span_share >= COLUMN_MIN_FLANK_WIDTH

    if (
        gutter_share <= DOUBLE_COLUMN_GUTTER_MAX_SHARE
        and flank_share >= COLUMN_FLANK_MIN_SHARE
        and flank_is_column
    ):
        # 置信度取自「两侧内容比缝密多少倍」
        separation = flank_share / max(gutter_share, 1.0 / COLUMN_PROFILE_BINS)
        return ColumnLayout(
            mode=COLUMN_DOUBLE,
            boundary=gutter_x,
            source=source,
            confidence=round(min(1.0, separation / 12.0), 4),
        )

    # 单栏：置信度取「三个理由里更站得住的那个」——
    # 要么缝上承载了成片内容（不是双栏），要么缝有一侧压根不成栏（内容只占半页），
    # 要么每一侧都只有窄条（通栏页的选项横排 / 表格行，不是两栏）。
    reason_gutter = gutter_share / DOUBLE_COLUMN_GUTTER_MAX_SHARE
    reason_flank = 1.0 - flank_share / COLUMN_FLANK_MIN_SHARE
    reason_span = 1.0 - span_share / COLUMN_MIN_FLANK_WIDTH
    return ColumnLayout(
        mode=COLUMN_SINGLE,
        boundary=1.0,
        source=source,
        confidence=round(
            max(0.0, min(1.0, max(reason_gutter, reason_flank, reason_span))), 4
        ),
    )


def detect_column_layout_from_image(gray: Image.Image) -> ColumnLayout:
    """纯图像判栏（扫描件走这条）。"""

    coverage, inked, runs = _column_coverage_profile(gray)
    if inked <= 0:
        # 整页空白（或二值化后无墨）：无从判断，按单栏处理
        return ColumnLayout()
    return _layout_from_coverage(coverage, inked, source=LAYOUT_SOURCE_VISUAL, runs=runs)


def detect_column_layout_from_text_rows(
    rows: Sequence[tuple[float, float, float, float]],
) -> ColumnLayout | None:
    """用 PDF 文本层判栏 —— 有文本层时最准，也最便宜。

    ``rows`` 是归一化的 ``(x0, x1, y0, y1)`` **文本行**坐标。判据与图像路径同源：
    把每行的 x 区间投到横向剖面上，看中缝处有没有内容横跨、两侧是否各自成栏。

    文本行太少（不足 :data:`COLUMN_MIN_TEXT_ROWS`）时返回 ``None``，由调用方回退
    图像判栏 —— 样本不够时任何统计都不可信。注意本函数会给出**确定的单栏结论**，
    不再像早期实现那样「判不出双栏就返回 None」：那是实测 p9–p13 被误判成双栏的
    直接原因（文本层说不像双栏，却因为返回 None 而回退到较弱的图像判据）。
    """

    cleaned = [
        (float(x0), float(x1), float(y0), float(y1))
        for x0, x1, y0, y1 in rows
        if float(x1) > float(x0)
    ]
    if len(cleaned) < COLUMN_MIN_TEXT_ROWS:
        return None

    coverage = [0] * COLUMN_PROFILE_BINS
    for x0, x1, _y0, _y1 in cleaned:
        start = max(0, int(x0 * COLUMN_PROFILE_BINS))
        end = min(COLUMN_PROFILE_BINS, int(x1 * COLUMN_PROFILE_BINS) + 1)
        for index in range(start, end):
            coverage[index] += 1

    return _layout_from_coverage(
        coverage,
        len(cleaned),
        source=LAYOUT_SOURCE_TEXT,
        # 文本行本身就是「内容段」：缝两侧各自最宽的那一行决定这里能不能算一栏。
        runs=[(x0, x1) for x0, x1, _y0, _y1 in cleaned],
    )


# ----------------- 整页切块 -----------------


def _blocks_from_split(
    split: _RegionSplit,
    *,
    page_no: int,
    height: int,
    x_start: float,
    x_end: float,
    column_index: int,
    index_offset: int = 0,
) -> list[BlockRegion]:
    """把条带内的像素块区间转成全页归一化 ``BlockRegion``。"""

    padding = int(height * BLOCK_PADDING_RATIO)
    blocks: list[BlockRegion] = []
    for offset, (top, bottom) in enumerate(split.blocks):
        blocks.append(
            BlockRegion(
                page_no=page_no,
                block_index=index_offset + offset,
                y_start=max(0.0, (top - padding) / height),
                y_end=min(1.0, (bottom + padding + 1) / height),
                x_start=x_start,
                x_end=x_end,
                column_index=column_index,
            )
        )
    return blocks


def _merge_splits(first: _RegionSplit, second: _RegionSplit) -> _RegionSplit:
    """合并两栏的统计（整页口径，仅供诊断与前端展示）。"""

    return _RegionSplit(
        blocks=[],
        gap_candidates=sorted({*first.gap_candidates, *second.gap_candidates}),
        snap_points=sorted({*first.snap_points, *second.snap_points}),
        question_cut_points=sorted(
            {*first.question_cut_points, *second.question_cut_points}
        ),
        line_count=first.line_count + second.line_count,
        base_gap_px=first.base_gap_px or second.base_gap_px,
        median_gap_px=first.median_gap_px or second.median_gap_px,
        strong_gap_px=first.strong_gap_px or second.strong_gap_px,
        candidate_gap_px=first.candidate_gap_px or second.candidate_gap_px,
        ignored_block_count=first.ignored_block_count + second.ignored_block_count,
    )


def _page_analysis(
    *,
    page_no: int,
    width: int,
    height: int,
    layout: ColumnLayout,
    split: _RegionSplit,
    blocks: list[BlockRegion],
    columns: dict[int, _RegionSplit] | None = None,
) -> PageAnalysis:
    """组装单页结果。

    ``split`` 是整页口径的诊断统计（双栏时由两栏合并）；``columns`` 是**按栏**的
    切分结果，用于生成按栏吸附点 —— 单栏页面省略，等价于 ``{0: split}``。
    """

    per_column = columns or {0: split}
    return PageAnalysis(
        page_no=page_no,
        width=width,
        height=height,
        layout=layout,
        blocks=blocks,
        gap_candidates=split.gap_candidates,
        snap_points=split.snap_points,
        question_cut_points=split.question_cut_points,
        line_count=split.line_count,
        base_gap_px=split.base_gap_px,
        median_gap_px=split.median_gap_px,
        strong_gap_px=split.strong_gap_px,
        candidate_gap_px=split.candidate_gap_px,
        ignored_block_count=split.ignored_block_count,
        column_snap_points={
            key: list(value.snap_points)
            for key, value in per_column.items()
            if value.snap_points
        },
        column_gap_candidates={
            key: list(value.gap_candidates)
            for key, value in per_column.items()
            if value.gap_candidates
        },
    )


def _resolve_layout(
    layout: ColumnLayout | None,
    gray: Image.Image,
    text_rows: Sequence[tuple[float, float, float, float]] | None,
) -> ColumnLayout:
    """确定本页最终使用的栏结构。

    优先级：人工指定 > PDF 文本层判栏 > 图像判栏。

    人工只说「双栏」却没给分栏线时（前端切到双栏但还没拖过分栏线），用自动
    检测的分栏线补上 —— 否则「人工指定」会因为少一个参数而整页退回单栏，
    与用户意图正好相反。
    """

    def _detect() -> ColumnLayout:
        if text_rows:
            detected = detect_column_layout_from_text_rows(text_rows)
            if detected is not None:
                return detected
        return detect_column_layout_from_image(gray)

    if layout is None:
        return _detect()

    if layout.mode == COLUMN_DOUBLE and not layout.is_double:
        fallback = _detect()
        boundary = fallback.boundary if 0.0 < fallback.boundary < 1.0 else DEFAULT_COLUMN_BOUNDARY
        return ColumnLayout(
            mode=COLUMN_DOUBLE,
            boundary=boundary,
            source=LAYOUT_SOURCE_MANUAL,
            confidence=fallback.confidence,
        )

    return layout


def analyze_page_image(
    image_path: str | Path,
    *,
    page_no: int = 1,
    layout: ColumnLayout | None = None,
    text_rows: Sequence[tuple[float, float, float, float]] | None = None,
    text_lines: Sequence[tuple[float, float, float, float, str]] | None = None,
) -> PageAnalysis:
    """对单张页图切块（自动判栏；也支持按人工指定的栏结构切）。

    ``layout``
        人工在前端指定的栏结构，优先级最高。只指定 ``mode=double`` 而没给
        分栏线时会用自动检测的分栏线补上（见 :func:`_resolve_layout`）。
    ``text_rows``
        PDF 文本层的归一化文本行 ``(x0, x1, y0, y1)``；有则优先用它判栏
        （电子版 PDF 最准），没有（扫描件）回退到图像判栏。
    ``text_lines``
        同上的文本行，但多带一行文字（见 :func:`extract_pdf_text_lines`）。
        只用来找题号切点 —— 判栏不需要内容，所以两条通道各取所需。
    """

    path = Path(image_path)
    try:
        with Image.open(path) as image:
            image.load()
            gray = image.convert("L")
            width, height = gray.size
    except Exception as exc:  # noqa: BLE001
        raise PageSplitError(f"页图分析失败（{path.name}）: {exc}") from exc

    if width <= 0 or height <= 0:
        raise PageSplitError(f"页图尺寸异常（{path.name}）")

    resolved = _resolve_layout(layout, gray, text_rows)

    if not resolved.is_double:
        split = _split_region(gray, _question_cut_points(text_lines or ()))
        blocks = _blocks_from_split(
            split,
            page_no=page_no,
            height=height,
            x_start=0.0,
            x_end=1.0,
            column_index=0,
        )
        return _page_analysis(
            page_no=page_no,
            width=width,
            height=height,
            layout=resolved,
            split=split,
            blocks=blocks,
        )

    # 双栏：左右各自投影。块顺序按阅读顺序 —— 左栏从上到下，再右栏从上到下。
    cut = max(1, min(width - 1, int(round(resolved.boundary * width))))
    blocks: list[BlockRegion] = []
    tally = _empty_split()
    columns = (
        (1, (0, 0, cut, height), 0.0, resolved.boundary),
        (2, (cut, 0, width, height), resolved.boundary, 1.0),
    )
    per_column: dict[int, _RegionSplit] = {}
    for column_index, box, x_start, x_end in columns:
        # 题号行按 x 归属到栏：双栏页左右栏的行位置互相错开，把左栏的题号钉到
        # 右栏的间隙上会切出完全错误的边界。
        column_lines = [
            row
            for row in (text_lines or ())
            if x_start <= row[0] < x_end
        ]
        column_split = _split_region(
            gray.crop(box), _question_cut_points(column_lines)
        )
        per_column[column_index] = column_split
        blocks.extend(
            _blocks_from_split(
                column_split,
                page_no=page_no,
                height=height,
                x_start=x_start,
                x_end=x_end,
                column_index=column_index,
                index_offset=len(blocks),
            )
        )
        tally = _merge_splits(tally, column_split)

    return _page_analysis(
        page_no=page_no,
        width=width,
        height=height,
        layout=resolved,
        split=tally,
        blocks=blocks,
        columns=per_column,
    )


def clean_manual_boxes(
    boxes: Iterable[Sequence[float]],
) -> list[tuple[float, float, float, float]]:
    """校验并规整人工框，返回可信的 ``(x0, y0, x1, y1)`` 列表（按位置排序）。

    这是人工框规则的**唯一真源**：接口层落盘、算法层切块都从这里过一遍，
    免得出现「接口放行了一个 3 px 的框、算法又悄悄丢掉」这种两处规则不一致
    的状态（前端会照着落盘的框画出来，那个框却永远不会成为题块）。

    丢弃与纠正：

    - 元素个数不是 4、元素不是数字 → 丢；
    - 反向拖出来的框（x1 < x0）→ 就地交换，不丢；
    - 超出页面的坐标（< 0 或 > 1）→ 裁到页内；
    - 任一边小于 ``MANUAL_BOX_MIN_SIZE`` → 丢（前端拖拽会不断产生这类瞬时矩形）。
    """

    cleaned: list[tuple[float, float, float, float]] = []
    for box in boxes or []:
        try:
            x0, y0, x1, y1 = (float(value) for value in box)
        except (TypeError, ValueError):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        x0, x1 = max(0.0, x0), min(1.0, x1)
        y0, y1 = max(0.0, y0), min(1.0, y1)
        if x1 - x0 < MANUAL_BOX_MIN_SIZE or y1 - y0 < MANUAL_BOX_MIN_SIZE:
            continue
        cleaned.append((x0, y0, x1, y1))
    cleaned.sort(key=lambda item: (item[0], item[1]))
    return cleaned


def boxes_to_blocks(
    page_no: int,
    boxes: Iterable[Sequence[float]],
    *,
    column_boundary: float | None = None,
) -> list[BlockRegion]:
    """把人工画的矩形框转成题块。

    ``boxes`` 每项是 ``(x0, y0, x1, y1)``，全部归一化到 0–1（y 向下）。这是
    「自动切块只认行间隙、不知道题在哪 → 人工框选定义题块」的落地入口，产出的
    块与自动切块同层同构，下游流程无需分支。

    退化框的丢弃与反向坐标纠正都交给 :func:`clean_manual_boxes`；本函数额外
    负责**按横向范围推断栏号**：整框落在分栏线左侧记 1、右侧记 2、横跨或没给
    分栏线记 0（0 的库内语义是「跨栏 / 不适用」，与单栏页一致）。

    返回的块按「左栏在前、同栏按 y」排序并连续重编号。
    """

    boundary: float | None = None
    try:
        candidate = float(column_boundary)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        candidate = 0.0
    if 0.0 < candidate < 1.0:
        boundary = candidate

    blocks: list[BlockRegion] = []
    for x0, y0, x1, y1 in clean_manual_boxes(boxes):
        column_index = 0
        if boundary is not None:
            if x1 <= boundary + MANUAL_BOX_COLUMN_TOLERANCE:
                column_index = 1
            elif x0 >= boundary - MANUAL_BOX_COLUMN_TOLERANCE:
                column_index = 2
        blocks.append(
            BlockRegion(
                page_no=page_no,
                block_index=len(blocks),
                y_start=y0,
                y_end=y1,
                x_start=x0,
                x_end=x1,
                column_index=column_index,
            )
        )
    return blocks


def _box_overlaps_block_horizontally(box: BlockRegion, block: BlockRegion) -> bool:
    """人工框在横向上是否盖到了这个块 —— 盖不到就谈不上切它。"""

    x_overlap = min(box.x_end, block.x_end) - max(box.x_start, block.x_start)
    return x_overlap > MANUAL_BOX_OVERLAP_EPSILON


def _subtract_manual_boxes(
    block: BlockRegion, boxes: Sequence[BlockRegion]
) -> list[BlockRegion]:
    """把自动块里被人工框压住的纵向区间切走，返回剩下的残段。

    这就是「框到哪，哪就归框」的落地：**压住的部分给框，没压住的部分仍然
    是这个自动块自己的**。一个块可能被切出上下两段（框打在中间）或只剩一段
    （框打在头/尾）或整块消失（框全包住）。

    不做「碰到一点就把整块吃掉」是有意的：碎片页上框的边缘常常只压住邻块的
    一小截，整块吃掉等于把那截字**静默**从错题本里删掉，而用户看不出少了什么。
    留残段最坏是多出一个可删的小卡片，不会丢内容。
    """

    spans = [
        (max(block.y_start, box.y_start), min(block.y_end, box.y_end))
        for box in boxes
        if _box_overlaps_block_horizontally(box, block)
    ]
    if not spans:
        return [block]

    remaining = [(block.y_start, block.y_end)]
    for start, end in spans:
        if end - start <= 0:
            continue
        pieces: list[tuple[float, float]] = []
        for piece_start, piece_end in remaining:
            if end <= piece_start or start >= piece_end:
                pieces.append((piece_start, piece_end))
                continue
            if start > piece_start:
                pieces.append((piece_start, start))
            if end < piece_end:
                pieces.append((end, piece_end))
        remaining = pieces

    return [
        replace(block, y_start=piece_start, y_end=piece_end)
        for piece_start, piece_end in remaining
        if piece_end - piece_start >= MANUAL_FRAGMENT_MIN_HEIGHT
    ]


def _box_sort_bucket(block: BlockRegion, boundary: float | None) -> int:
    """排序分组：左栏 0、右栏 1；单栏页（无分栏线）恒为 0。

    判定用 ``x_start`` 而不是中心点：横跨两栏的人工框从左边缘起，理应跟左栏
    一起按 y 排；用中心点会把它整个挪到右栏那一组，看起来像跑到页面末尾。
    """

    if boundary is None:
        return 0
    return 0 if block.x_start < boundary - MANUAL_BOX_COLUMN_TOLERANCE else 1


def merge_manual_boxes(
    blocks: Sequence[BlockRegion],
    boxes: Iterable[Sequence[float]],
    *,
    page_no: int,
    column_boundary: float | None = None,
) -> list[BlockRegion]:
    """把人工框叠加到自动切块结果上（人工框优先）。

    规则一句话：**框到哪，哪就归框。** 与人工框横向重叠的自动块，其被压住的
    纵向区间被切走（残段保留），人工框自身成为题块，其余自动块原样不动。于是
    「把 3 个碎片并成一块」＝框住它们；「把 1 块拆成 2 块」＝在原块上画 2 个框。

    ``boxes`` 为空时**原样返回** ``blocks``（连 ``block_index`` 都不动）：
    「清除本页人工框 → 回到纯自动结果」必须能精确复原，否则用户删掉一个画错
    的框、重算一次，后面所有块号都跟着漂移。

    ``column_boundary`` 只影响排序分组与人工框的栏号推断。
    """

    manual = boxes_to_blocks(page_no, boxes, column_boundary=column_boundary)
    if not manual:
        return list(blocks)

    boundary: float | None = None
    try:
        candidate = float(column_boundary)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        candidate = 0.0
    if 0.0 < candidate < 1.0:
        boundary = candidate

    kept: list[BlockRegion] = []
    for block in blocks:
        kept.extend(_subtract_manual_boxes(block, manual))

    merged = kept + manual
    merged.sort(
        key=lambda block: (
            _box_sort_bucket(block, boundary),
            block.y_start,
            block.x_start,
        )
    )
    return [replace(block, block_index=index) for index, block in enumerate(merged)]


def analyze_page_images(
    page_paths: Iterable[str | Path],
    *,
    start_page_no: int = 1,
    page_text_rows: dict[int, Sequence[tuple[float, float, float, float]]] | None = None,
    page_text_lines: dict[int, Sequence[tuple[float, float, float, float, str]]] | None = None,
    page_layouts: dict[int, ColumnLayout] | None = None,
) -> list[PageAnalysis]:
    """批量分析页图，页码从 ``start_page_no`` 起顺序编号。

    ``page_text_rows`` / ``page_text_lines`` / ``page_layouts`` 都是「页号 → 该页
    的依据」：第一项是 PDF 文本层坐标（自动判栏用），第二项是同上的文本行文字
    （题号切点用），第三项是人工指定的栏结构（优先）。
    """

    results: list[PageAnalysis] = []
    for offset, path in enumerate(page_paths):
        page_no = start_page_no + offset
        results.append(
            analyze_page_image(
                path,
                page_no=page_no,
                layout=(page_layouts or {}).get(page_no),
                text_rows=(page_text_rows or {}).get(page_no),
                text_lines=(page_text_lines or {}).get(page_no),
            )
        )
    return results


# ----------------- 裁图 -----------------


def crop_region(
    image_path: str | Path,
    out_path: str | Path,
    *,
    y_start: float,
    y_end: float,
    x_start: float = 0.0,
    x_end: float = 1.0,
) -> Path:
    """按归一化坐标裁出一块区域并落盘（用于生成 ``image_block``）。

    坐标一律 0–1，与前端拖拽使用的相对坐标一致，避免 DPI 变化后错位。
    """

    for name, value in (
        ("y_start", y_start),
        ("y_end", y_end),
        ("x_start", x_start),
        ("x_end", x_end),
    ):
        if not (0.0 <= value <= 1.0):
            raise PageSplitError(f"裁剪坐标 {name}={value} 超出 0–1 范围。")
    if y_end <= y_start or x_end <= x_start:
        raise PageSplitError("裁剪区域不能为空。")

    source = Path(image_path)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            image.load()
            width, height = image.size
            box = (
                max(0, int(x_start * width)),
                max(0, int(y_start * height)),
                min(width, max(1, int(round(x_end * width)))),
                min(height, max(1, int(round(y_end * height)))),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                raise PageSplitError("裁剪区域计算后为空，请检查坐标。")
            image.convert("RGB").crop(box).save(target, format="PNG", optimize=True)
    except PageSplitError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PageSplitError(f"裁图失败: {exc}") from exc
    return target


def timestamp_slug() -> str:
    """批次目录名用的时间戳片段。"""

    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
