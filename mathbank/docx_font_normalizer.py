# -*- coding: utf-8 -*-
"""docx 字体归一化：让 docx 转 PDF 时真正嵌入含中文与数学符号字形的字体。

背景
----
``headless_libreoffice_profile``（Plan X, commit ``5b02448``）的 LibreOffice
``registrymodifications.xcu`` 字体替换（SimSun → STHeiti Medium 等）只在
*OnScreen* 渲染时生效，PDF 导出时不应用替换规则。

FODT 探针实证（macOS LibreOffice 26 headless）::

    soffice ... --convert-to fodt ...
    # → font-name="Calibri"  font-name="Liberation Sans"  font-name="Times New Roman"

LO 把 docx 声明的 ``新宋体``（SimSun）替换为 ``Liberation Sans``——一个**纯拉丁
字体，不含任何 CJK 字形**。LO 的字形 fallback 链无法可靠补齐中文，
PyMuPDF / PDF.js 渲染时大量中文与集合/逻辑符号（{ } ∈ ∅ 等）变成空白方框。

修复方向
--------
本模块直接改写 docx 内部的 ``<w:rFonts>`` 声明，在 LO 转 PDF **之前**把全部
run / style / docDefaults 的字体统一为目标字体。LO 真正把目标字体嵌入
PDF 子集后，任何 PDF 阅读器都能正确渲染。

目标字体的选择（用 fontTools 实测 macOS 全量字体 cmap）：

============== ========= ========= =================================
候选字体         CJK 覆盖  数学符号   备注
============== ========= ========= =================================
STHeiti Medium   4/4       0/33     黑体类；中文完美但所有数学符号缺失
Hiragino Sans GB 4/4       0/33     同上
Arial Unicode MS 4/4      32/33     **唯一同时覆盖两者**；⟺ 缺失
Apple Symbols    0/4      33/33     数学完美但**完全无中文**
Symbol           0/4      31/33     同上
STIX Two Math    —        —         **本机未安装**，不可用
============== ========= ========= =================================

→ 选定 ``Arial Unicode MS`` 为统一默认（cjk_font = math_font）。
  中文字形稍逊于 STHeiti Medium，但换得 OMML 之外的字面数学符号、
  集合括号、= / ∅ / ∈ 等全部正确，对数学试卷可接受。

真实端到端验证
--------------
用 2024-2025 成都七中高一（上）期中数学试卷 docx 转 PDF（150 DPI 渲染）：

* 当前管线（无归一化）：中文 + 数学符号均大量方框 ✗
* 归一化为 STHeiti Medium：中文完美，但 ``{ } = ∈`` 等仍方框 ✗
* 归一化为 Arial Unicode MS：中文 + 数学符号全部正确 ✓

副作用
------
不修改：文本、公式、图片、样式名、节属性、页眉页脚内容。
可能改变：原本依赖宋体/黑体的视觉风格，归一化后中文字形偏向
Arial Unicode MS 风格；对数学试卷这类以公式正确性为优先的场景完全可接受。
"""

from __future__ import annotations

import io
from typing import Optional

from docx import Document
from docx.oxml.ns import qn
from lxml import etree


# macOS 自带；fontTools 实测：唯一同时覆盖 CJK（4/4）与数学符号
# （∀ ∃ ∈ ∉ ∩ ∪ ⊂ ⟺ ⇒ ⇔ ∇ ∅ ∑ 等 32/33）的字体。
# STHeiti Medium 中文字形更佳但无数学符号；STIX Two Math 数学完美但本机未装；
# 折中选 Arial Unicode MS，对数学试卷"公式正确性 > 中文字形风格"够用。
DEFAULT_CJK_FONT: str = "Arial Unicode MS"
DEFAULT_MATH_FONT: str = "Arial Unicode MS"


def _set_rfonts_attrs(rfonts_el: etree._Element, cjk_font: str, math_font: str) -> None:
    """把 ``<w:rFonts>`` 的 ascii/hAnsi/eastAsia 设成 cjk_font，cs 设成 math_font。

    同时移除 theme 属性（``asciiTheme`` / ``hAnsiTheme`` / ``eastAsiaTheme`` /
    ``cstheme``），因为 theme 字段优先级高于字面字段，会覆盖我们的设置。
    """
    rfonts_el.set(qn("w:ascii"), cjk_font)
    rfonts_el.set(qn("w:hAnsi"), cjk_font)
    rfonts_el.set(qn("w:eastAsia"), cjk_font)
    rfonts_el.set(qn("w:cs"), math_font)
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        full_qn = qn(f"w:{attr}")
        if full_qn in rfonts_el.attrib:
            del rfonts_el.attrib[full_qn]


def _iter_rfonts(root_el: etree._Element):
    """遍历 ``root_el`` 下所有需要归一化的 ``<w:rFonts>``。

    跳过 ``<w:rPrChange>`` 内的 rFonts——那是 Word 的修订历史快照，写入会污染
    历史记录且对最终渲染无意义。
    """
    for rfonts in root_el.iter(qn("w:rFonts")):
        p = rfonts.getparent()
        in_change = False
        while p is not None:
            if p.tag == qn("w:rPrChange"):
                in_change = True
                break
            p = p.getparent()
        if not in_change:
            yield rfonts


def normalize_docx_fonts(
    docx_bytes: bytes,
    *,
    cjk_font: str = DEFAULT_CJK_FONT,
    math_font: str = DEFAULT_MATH_FONT,
) -> bytes:
    """把 docx 内所有 ``<w:rFonts>`` 归一化为 ``cjk_font`` + ``math_font``，返回新字节。

    覆盖范围（确保不留盲点）：
    1. ``word/styles.xml`` 下全部 ``<w:style>`` 元素（含 docDefaults 与全部命名样式）
    2. ``word/document.xml`` 下整棵子树（body / 段落 / run / 段落属性 rPr /
       表格单元格 rPr / 文本框 / 脚注 / 批注等所有出现 rFonts 的位置）

    跳过 ``<w:rPrChange>`` 内的 rFonts（修订历史快照）。
    """
    doc = Document(io.BytesIO(docx_bytes))

    # styles.xml（含 docDefaults + 全部命名样式）
    for rfonts in _iter_rfonts(doc.styles.element):
        _set_rfonts_attrs(rfonts, cjk_font, math_font)

    # document.xml（含 body 下所有 run / pPr / tcPr / 文本框等嵌套位置）
    for rfonts in _iter_rfonts(doc.element):
        _set_rfonts_attrs(rfonts, cjk_font, math_font)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


__all__ = ["normalize_docx_fonts", "DEFAULT_CJK_FONT", "DEFAULT_MATH_FONT"]
