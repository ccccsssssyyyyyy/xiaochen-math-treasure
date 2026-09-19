# -*- coding: utf-8 -*-
"""mathtype_ole_renderer 模块契约测试。"""

import io
import os
import zipfile

import pytest

#: 含 MathType OLE 公式的样本 docx。默认从家目录取，可用环境变量
#: MATHBANK_MATHTYPE_FIXTURE 覆盖；文件不存在时相关用例自动跳过（CI 上即跳过）。
DOCX_FIXTURE = os.environ.get(
    "MATHBANK_MATHTYPE_FIXTURE",
    os.path.join(
        os.path.expanduser("~"),
        "Desktop", "成都七中试卷", "高一", "上学期",
        "2024-2025学年四川省成都七中高一（上）期中数学试卷.docx",
    ),
)


@pytest.mark.skipif(not os.path.exists(DOCX_FIXTURE), reason="需要本地成都七中试卷 docx")
def test_extract_mathtype_formulas_returns_expected_count():
    from mathbank.mathtype_ole_renderer import extract_mathtype_formulas

    with open(DOCX_FIXTURE, "rb") as f:
        docx_bytes = f.read()

    formulas = extract_mathtype_formulas(docx_bytes)
    # 该文档含 729 个 w:object，其中大部分应能 decode 出 LaTeX
    assert len(formulas) >= 500, f"期望至少 500 个公式，实际 {len(formulas)}"

    # 前几个已知结果校验
    _, info0 = formulas[0]
    assert info0.latex == "("
    assert info0.width_pt > 0
    assert info0.height_pt > 0
    assert info0.ole_target.startswith("embeddings/oleObject")

    _, info2 = formulas[2]
    assert "{x" in info2.latex and "25-2x" in info2.latex


@pytest.mark.skipif(not os.path.exists(DOCX_FIXTURE), reason="需要本地成都七中试卷 docx")
def test_replace_mathtype_ole_with_images_produces_valid_docx():
    from mathbank.mathtype_ole_renderer import replace_mathtype_ole_with_images

    with open(DOCX_FIXTURE, "rb") as f:
        docx_bytes = f.read()

    new_bytes = replace_mathtype_ole_with_images(docx_bytes)
    # 失败时返回原始 bytes；成功时也应为有效 docx
    assert new_bytes[:4] == b"PK\x03\x04"

    with zipfile.ZipFile(io.BytesIO(new_bytes), "r") as zin:
        names = zin.namelist()
        assert "word/document.xml" in names
        # 应至少写入一张渲染后的公式图
        pngs = [n for n in names if n.startswith("word/media/mb_formula_") and n.endswith(".png")]
        assert len(pngs) >= 3, f"期望至少 3 张公式 PNG，实际 {len(pngs)}"

        # 新 document.xml 中应出现 w:drawing（替换后的图片节点）
        doc_xml = zin.read("word/document.xml").decode("utf-8")
        assert "<w:drawing>" in doc_xml or "<w:drawing " in doc_xml


@pytest.mark.skipif(not os.path.exists(DOCX_FIXTURE), reason="需要本地成都七中试卷 docx")
def test_replace_keeps_original_on_failure():
    from mathbank.mathtype_ole_renderer import replace_mathtype_ole_with_images

    # 传入无效字节应原样返回
    assert replace_mathtype_ole_with_images(b"not a docx") == b"not a docx"
