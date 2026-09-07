# -*- coding: utf-8 -*-
"""把 DOCX 中的 MathType OLE 公式对象预渲染为图片并替换。

背景
----
macOS LibreOffice headless 转 PDF 时，无法调用 MathType 引擎渲染
``oleObject*.bin`` 中的公式，导致试卷预览里大量公式区域显示为空白方框。

本模块在 docx → PDF 之前介入：

1. 读取每个 ``w:object`` 里的 ``o:OLEObject`` 指向的 ``oleObjectN.bin``；
2. 用 ``decode_mtef_formula`` 把 MathType 私有二进制格式转成 LaTeX；
3. 用 ``latex`` + ``dvisvgm`` 把 LaTeX 渲染为 SVG；
4. 用 LibreOffice 把 SVG 批量转为 PNG；
5. 把 ``w:object`` 节点替换为 ``w:drawing`` 引用的 PNG 图片，并保留原尺寸。

这样 LO 转 PDF 时看到的是普通图片，不会再出现方框。

失败降级
--------
- 单个公式 decode 失败：保留原 ``w:object``；
- 单个公式 LaTeX 渲染失败：保留原 ``w:object``；
- 整体失败：返回原始 docx 字节流，不破坏主流程。
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lxml import etree


def _qn(ns: str, tag: str) -> str:
    """返回带命名空间前缀的 tag 名。"""
    return f"{{{ns}}}{tag}"


_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_V = "urn:schemas-microsoft-com:vml"
_NS_O = "urn:schemas-microsoft-com:office:office"


def _w(tag: str) -> str:
    return _qn(_NS_W, tag)


def _o(tag: str) -> str:
    return _qn(_NS_O, tag)


def _v(tag: str) -> str:
    return _qn(_NS_V, tag)


def _r_attr(name: str) -> str:
    return _qn(_NS_R, name)


@dataclass(frozen=True)
class FormulaInfo:
    """一个 MathType OLE 公式的提取结果。"""

    latex: str
    width_pt: float
    height_pt: float
    ole_rel_id: str
    ole_target: str
    ole_data: bytes


def _parse_pt(style: str, dim: str) -> float:
    """从 ``width:6.75pt;height:15pt`` 中解析 pt 值。"""
    pattern = rf"{dim}\s*:\s*([\d.]+)pt"
    m = re.search(pattern, style, re.I)
    if m:
        return float(m.group(1))
    return 15.0 if dim == "height" else 30.0


def _find_max_rid(rels_root: etree._Element) -> int:
    """从 document.xml.rels 里找最大 rId 数字。"""
    max_rid = 0
    for rel in rels_root.findall(_qn(_NS_REL, "Relationship")):
        rid = rel.get("Id") or ""
        m = re.search(r"(\d+)$", rid)
        if m:
            max_rid = max(max_rid, int(m.group(1)))
    return max_rid


def _add_png_content_type(docx_bytes: bytes) -> bytes:
    """确保 [Content_Types].xml 里有 png 默认类型声明；返回新 docx 字节。"""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zin:
        with zipfile.ZipFile(out, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    ct_root = etree.fromstring(data)
                    existing = ct_root.find(_qn(_NS_CT, "Default") + '[@Extension="png"]')
                    if existing is None:
                        default = etree.SubElement(ct_root, _qn(_NS_CT, "Default"))
                        default.set("Extension", "png")
                        default.set("ContentType", "image/png")
                        data = etree.tostring(
                            ct_root, xml_declaration=True, encoding="UTF-8", standalone=True
                        )
                zout.writestr(item, data)
    return out.getvalue()


def extract_mathtype_formulas(docx_bytes: bytes) -> List[Tuple[etree._Element, FormulaInfo]]:
    """从 docx 中提取所有 MathType OLE 公式。

    返回 ``[(w:object 元素, FormulaInfo), ...]``，按文档顺序排列。
    """
    from mathbank.mtef_helper import decode_mtef_formula

    result: List[Tuple[etree._Element, FormulaInfo]] = []
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zin:
        try:
            rels_data = zin.read("word/_rels/document.xml.rels")
        except KeyError:
            return result
        rels_root = etree.fromstring(rels_data)

        def _rel_target(rid: str) -> Optional[str]:
            for rel in rels_root.findall(_qn(_NS_REL, "Relationship")):
                if rel.get("Id") == rid:
                    return rel.get("Target")
            return None

        try:
            doc_xml = zin.read("word/document.xml")
        except KeyError:
            return result
        doc_root = etree.fromstring(doc_xml)

    for obj in doc_root.iter(_qn(_NS_W, "object")):
        ole_obj = obj.find(_qn(_NS_O, "OLEObject"))
        if ole_obj is None:
            continue
        ole_rid = ole_obj.get(_r_attr("id"))
        if not ole_rid:
            continue
        ole_target = _rel_target(ole_rid)
        if not ole_target:
            continue
        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zin:
            try:
                ole_data = zin.read(f"word/{ole_target}")
            except KeyError:
                continue

        decoded = decode_mtef_formula(ole_data)
        if not decoded or not decoded.success or not decoded.latex:
            continue

        shape = obj.find(_qn(_NS_V, "shape"))
        style = shape.get("style") if shape is not None else ""
        width_pt = _parse_pt(style, "width")
        height_pt = _parse_pt(style, "height")

        info = FormulaInfo(
            latex=decoded.latex,
            width_pt=width_pt,
            height_pt=height_pt,
            ole_rel_id=ole_rid,
            ole_target=ole_target,
            ole_data=ole_data,
        )
        result.append((obj, info))

    return result


def _render_latex_batch(
    latex_items: List[Tuple[int, str]],
    work_dir: Path,
    soffice_cmd: str = "soffice",
    latex_cmd: str = "latex",
    timeout: int = 120,
) -> Dict[int, Path]:
    """把一批 (index, latex) 渲染为 PNG，返回 ``{index: png_path}``。

    相同 LaTeX 字符串只渲染一次，避免重复工作。
    """
    svg_dir = work_dir / "svg"
    png_dir = work_dir / "png"
    svg_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)

    unique_latex: Dict[str, int] = {}
    index_to_latex_uid: Dict[int, int] = {}
    for idx, latex in latex_items:
        if latex not in unique_latex:
            unique_latex[latex] = len(unique_latex)
        index_to_latex_uid[idx] = unique_latex[latex]

    latex_to_svg: Dict[str, Path] = {}
    for latex, uid in unique_latex.items():
        tex_file = svg_dir / f"f{uid}.tex"
        dvi_file = svg_dir / f"f{uid}.dvi"
        svg_file = svg_dir / f"f{uid}.svg"
        tex_src = (
            r"\documentclass[border=2pt]{standalone}"
            + "\n"
            + r"\usepackage{amsmath,amssymb,amsfonts}"
            + "\n"
            + r"\begin{document}"
            + "\n"
            + r"$"
            + latex
            + r"$"
            + "\n"
            + r"\end{document}"
            + "\n"
        )
        tex_file.write_text(tex_src, encoding="utf-8")
        try:
            subprocess.run(
                [latex_cmd, "-interaction=nonstopmode", "-output-directory", str(svg_dir), str(tex_file)],
                capture_output=True,
                timeout=timeout,
            )
            subprocess.run(
                ["dvisvgm", "--no-fonts", f"--output={svg_file}", str(dvi_file)],
                capture_output=True,
                timeout=timeout,
            )
        except Exception:
            continue
        if svg_file.exists() and svg_file.stat().st_size > 0:
            latex_to_svg[latex] = svg_file

    if not latex_to_svg:
        return {}

    # 用 LibreOffice 批量把 SVG 转 PNG
    svg_files = [str(p) for p in latex_to_svg.values()]
    # 避免命令行过长，分批
    batch_size = 50
    for i in range(0, len(svg_files), batch_size):
        batch = svg_files[i : i + batch_size]
        try:
            subprocess.run(
                [
                    soffice_cmd,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "png",
                    "--outdir",
                    str(png_dir),
                    *batch,
                ],
                capture_output=True,
                timeout=timeout,
            )
        except Exception:
            continue

    # 建立 index -> png 映射
    uid_to_svg = {uid: latex_to_svg[latex] for latex, uid in unique_latex.items() if latex in latex_to_svg}
    uid_to_png: Dict[int, Path] = {}
    for uid, svg_path in uid_to_svg.items():
        png_path = png_dir / f"{svg_path.stem}.png"
        if png_path.exists():
            uid_to_png[uid] = png_path

    return {idx: uid_to_png[uid] for idx, uid in index_to_latex_uid.items() if uid in uid_to_png}


def _make_drawing_element(
    png_path: Path,
    width_pt: float,
    height_pt: float,
    rid: str,
) -> etree._Element:
    """用 python-docx add_picture 生成一个正确的 w:drawing 元素。"""
    from docx import Document
    from docx.shared import Inches

    tmp_doc = Document()
    paragraph = tmp_doc.add_paragraph()
    run = paragraph.add_run()
    run.add_picture(str(png_path), width=Inches(width_pt / 72.0))
    drawing = run._r.find(_qn(_NS_W, "drawing"))
    if drawing is None:
        raise RuntimeError("add_picture 没有生成 w:drawing")

    # 把 blip 的 r:embed 指向我们分配的 rid
    for blip in drawing.iter(_qn("http://schemas.openxmlformats.org/drawingml/2006/main", "blip")):
        blip.set(_r_attr("embed"), rid)
    return drawing


def replace_mathtype_ole_with_images(
    docx_bytes: bytes,
    soffice_cmd: str = "soffice",
    latex_cmd: str = "latex",
    timeout: int = 120,
) -> bytes:
    """把 docx 中的 MathType OLE 公式全部替换为渲染后的 PNG 图片。

    返回新的 docx 字节流。任意子步骤失败时返回原始字节流（不抛异常）。
    """
    try:
        formulas = extract_mathtype_formulas(docx_bytes)
    except Exception:
        return docx_bytes

    if not formulas:
        return docx_bytes

    with tempfile.TemporaryDirectory(prefix="mb_ole_render_") as tmp:
        work_dir = Path(tmp)
        latex_items = [(i, info.latex) for i, (_, info) in enumerate(formulas)]
        try:
            index_to_png = _render_latex_batch(
                latex_items, work_dir, soffice_cmd=soffice_cmd, latex_cmd=latex_cmd, timeout=timeout
            )
        except Exception:
            return docx_bytes

        if not index_to_png:
            return docx_bytes

        with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zin:
            rels_data = zin.read("word/_rels/document.xml.rels")
            rels_root = etree.fromstring(rels_data)
            max_rid = _find_max_rid(rels_root)

            doc_xml = zin.read("word/document.xml")
            doc_root = etree.fromstring(doc_xml)

            # 因为 extract_mathtype_formulas 里的 obj 是旧 doc_root 的元素，
            # 需要在新 doc_root 里重新定位对应元素（通过顺序索引）。
            objects = list(doc_root.iter(_qn(_NS_W, "object")))

            png_payloads: List[Tuple[str, bytes]] = []
            for idx, png_path in index_to_png.items():
                if idx >= len(objects):
                    continue
                obj = objects[idx]
                _, info = formulas[idx]

                max_rid += 1
                new_rid = f"rId{max_rid}"
                png_name = f"media/mb_formula_{idx}.png"

                new_rel = etree.SubElement(rels_root, _qn(_NS_REL, "Relationship"))
                new_rel.set("Id", new_rid)
                new_rel.set(
                    "Type",
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
                )
                new_rel.set("Target", png_name)

                try:
                    drawing = _make_drawing_element(png_path, info.width_pt, info.height_pt, new_rid)
                except Exception:
                    continue

                new_r = etree.Element(_qn(_NS_W, "r"))
                new_r.append(drawing)

                parent_r = obj
                while parent_r is not None and parent_r.tag != _qn(_NS_W, "r"):
                    parent_r = parent_r.getparent()
                target_el = parent_r if parent_r is not None else obj
                target_el.getparent().replace(target_el, new_r)

                png_payloads.append((png_name, png_path.read_bytes()))

            # 重建 docx
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w") as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename == "word/document.xml":
                        data = etree.tostring(
                            doc_root, xml_declaration=True, encoding="UTF-8", standalone=True
                        )
                    elif item.filename == "word/_rels/document.xml.rels":
                        data = etree.tostring(
                            rels_root, xml_declaration=True, encoding="UTF-8", standalone=True
                        )
                    zout.writestr(item, data)
                for png_name, png_bytes in png_payloads:
                    zout.writestr(f"word/{png_name}", png_bytes)

    new_docx_bytes = out.getvalue()
    return _add_png_content_type(new_docx_bytes)


__all__ = [
    "FormulaInfo",
    "extract_mathtype_formulas",
    "replace_mathtype_ole_with_images",
]
