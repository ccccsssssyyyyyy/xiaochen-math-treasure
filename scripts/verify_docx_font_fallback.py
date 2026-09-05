#!/usr/bin/env python3
"""验证 Plan X: LibreOffice 字体替换让 docx 转 PDF 后中文/数学符号不再空白。

用法示例:
    python scripts/verify_docx_font_fallback.py \
        "/Users/xxx/Desktop/成都七中试卷/高一/上学期/2024-2025学年...期中数学试卷.docx" \
        --page 7 --outdir /tmp/verify_fallback

默认会把输入文件复制到 ``outdir``、用项目独立 LibreOffice profile 转 PDF、
用 PyMuPDF 渲染指定页面,最终输出 PNG 供人工目检。同时会打印 PDF 内嵌字体
列表(若含 ``STHeiti Medium`` 说明替换已生效;若仍为 ``ArialUnicodeMS``
不代表失败,只要字形不再空白即达成目标)。
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mathbank.headless_libreoffice_profile import build_soffice_command, find_soffice


def parse_args():
    parser = argparse.ArgumentParser(description="验证 docx 转 PDF 字体替换效果")
    parser.add_argument("docx", type=Path, help="输入 docx 文件路径")
    parser.add_argument("--page", type=int, default=0, help="要渲染的页码(0-based)")
    parser.add_argument("--outdir", type=Path, default=Path("/tmp"), help="输出目录")
    parser.add_argument("--dpi", type=int, default=200, help="渲染 PNG 的 DPI")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.docx.exists():
        print(f"文件不存在: {args.docx}", file=sys.stderr)
        return 1

    soffice = find_soffice()
    if not soffice:
        print("未找到 LibreOffice/soffice,无法验证", file=sys.stderr)
        return 1

    args.outdir.mkdir(parents=True, exist_ok=True)
    stem = args.docx.stem
    tmp_docx = args.outdir / f"{stem}_verify.docx"
    pdf_path = args.outdir / f"{stem}_verify.pdf"
    png_path = args.outdir / f"{stem}_verify_page{args.page + 1}.png"

    shutil.copy2(args.docx, tmp_docx)
    if pdf_path.exists():
        pdf_path.unlink()

    cmd = build_soffice_command(
        soffice,
        "--headless", "--norestore", "--convert-to", "pdf",
        "--outdir", str(args.outdir), str(tmp_docx),
    )
    print(f"执行: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        print("LibreOffice 转换超时", file=sys.stderr)
        return 2
    if result.returncode != 0:
        print(f"LibreOffice 失败: {result.stderr}", file=sys.stderr)
        return 3

    if not pdf_path.exists():
        print(f"PDF 未生成: {pdf_path}", file=sys.stderr)
        return 4

    print(f"PDF 已生成: {pdf_path} ({pdf_path.stat().st_size} bytes)")

    # 渲染页面
    try:
        import fitz
        doc = fitz.open(pdf_path)
        if args.page >= len(doc):
            print(f"页码超出范围(共 {len(doc)} 页)", file=sys.stderr)
            return 5
        page = doc.load_page(args.page)
        pix = page.get_pixmap(dpi=args.dpi)
        pix.save(png_path)
        doc.close()
        print(f"渲染 PNG 已保存: {png_path}")
    except Exception as exc:
        print(f"渲染失败: {exc}", file=sys.stderr)
        return 6

    # 打印内嵌字体(方便排查)
    try:
        import fitz
        doc = fitz.open(pdf_path)
        fonts = set()
        for p in range(len(doc)):
            for f in doc.get_page_fonts(p):
                fonts.add(f[3])
        doc.close()
        print("PDF 内嵌字体:", ", ".join(sorted(fonts)))
    except Exception as exc:
        print(f"提取字体信息失败: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
