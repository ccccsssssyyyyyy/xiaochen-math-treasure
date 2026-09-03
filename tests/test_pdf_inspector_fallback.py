# tests/test_pdf_inspector_fallback.py
# -*- coding: utf-8 -*-
"""回归测试：pdf-inspector 抽空却标 needs_ocr 时，应逐页回退 PyMuPDF 保住文本与选项。"""
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank import pdf_inspector_helper as helper  # noqa: E402


def _build_text_pdf(text: str) -> bytes:
    """构造一页含指定文本的文字版 PDF，返回字节。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class _FakePage:
    def __init__(self, page=0, markdown="", needs_ocr=True, ocr_reason="no text layer"):
        self.page = page
        self.markdown = markdown
        self.needs_ocr = needs_ocr
        self.ocr_reason = ocr_reason


class _FakeResult:
    def __init__(self, pages):
        self.pages = pages


def test_empty_markdown_falls_back_to_pymupdf(monkeypatch):
    """pdf-inspector 返回空 markdown + needs_ocr 时，应回退 PyMuPDF 抽到原生文本。"""
    pdf_bytes = _build_text_pdf(
        "1. Test question here\nA. one\nB. two\nC. three\nD. four\n"
    )
    monkeypatch.setattr(
        helper.pdf_inspector,
        "extract_pages_markdown",
        lambda *a, **k: _FakeResult([_FakePage()]),
    )
    res = helper.inspect_and_extract_pdf(pdf_bytes, task_id="t-fallback")
    assert res["pdf_type"] == "text_based"
    assert res["pages_needing_ocr"] == []
    pages = res["pages"]
    assert len(pages) == 1
    assert pages[0]["needs_ocr"] is False
    assert pages[0]["source"] == "pymupdf-fallback"
    assert "A. one" in pages[0]["markdown"]
    assert pages[0]["ocr_reason"] == "no text layer"


def test_blank_page_stays_ocr(monkeypatch):
    """pdf-inspector 与 PyMuPDF 都抽不出实质文本时，仍应保留 needs_ocr。"""
    pdf_bytes = _build_text_pdf("")  # 空白页，无文本层
    monkeypatch.setattr(
        helper.pdf_inspector,
        "extract_pages_markdown",
        lambda *a, **k: _FakeResult([_FakePage()]),
    )
    res = helper.inspect_and_extract_pdf(pdf_bytes, task_id="t-blank")
    pages = res["pages"]
    assert len(pages) == 1
    assert pages[0]["needs_ocr"] is True
    assert pages[0]["source"] == "pdf-inspector"


def test_real_pdf_options_recovered(monkeypatch):
    """真实试卷选项回溯验证（需本地样例 PDF，仓库不随附，故默认跳过）。

    若要在本地验证，把一份真实数学试卷 PDF 路径通过环境变量
    MATHBANK_REAL_PDF 提供即可启用本测试。
    """
    path = os.environ.get("MATHBANK_REAL_PDF")
    if not path or not os.path.exists(path):
        pytest.skip("未提供本地真实试卷 PDF（设置 MATHBANK_REAL_PDF 可启用）")
    with open(path, "rb") as f:
        pdf_bytes = f.read()
    monkeypatch.setattr(
        helper.pdf_inspector,
        "extract_pages_markdown",
        lambda *a, **k: _FakeResult([_FakePage()]),
    )
    res = helper.inspect_and_extract_pdf(pdf_bytes, task_id="t-real")
    assert res["pdf_type"] == "text_based"
    assert res["pages_needing_ocr"] == []
    import re
    opts = re.findall(r"(?:^|\n)\s*[A-D][.．、]", res.get("markdown") or "")
    assert len(opts) >= 30
