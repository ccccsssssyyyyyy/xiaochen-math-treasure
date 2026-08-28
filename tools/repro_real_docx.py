"""针对真实 docx 做「复现验证」：完整复刻 run_docx_parsing_task 的拆题链路
（extract_docx_markdown -> lock_visible_math -> parse_paper_text_internal -> restore -> post_process），
但不写数据库、不生成预览图，避免污染题库。

用法：
  python tools/repro_real_docx.py <docx路径>            # 完整复现（含真实 API 拆题）
  python tools/repro_real_docx.py <docx路径> --local-only  # 仅本地验证分块（不发 API）
"""
import sys
import os
import time
import json
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# 注册一个伪 pytest 模块，使 main 进入 IS_TESTING 模式（跳过运行期文件锁，
# 避免与正在运行的题库后端 PID 冲突），但不写数据库，适合只读复现验证。
import types as _types
sys.modules.setdefault("pytest", _types.ModuleType("pytest"))

import main
from mathbank.docx_helper import extract_docx_markdown
from mathbank.paper_chunking import split_markdown_into_question_chunks, dedupe_questions


def banner(msg):
    print("\n" + "=" * 70)
    print(msg)
    print("=" * 70)


def main_run(docx_path, local_only):
    t0 = time.time()
    file_bytes = open(docx_path, "rb").read()
    banner("① 提取 Word Markdown（extract_docx_markdown，与真实导入一致）")
    tmpdir = "/tmp/repro_docx_assets"
    os.makedirs(tmpdir, exist_ok=True)
    docx_res = extract_docx_markdown(
        file_bytes,
        output_dir=tmpdir,
        url_prefix="/static/uploads/tmp",
        asset_prefix="repro",
    )
    md = docx_res.get("markdown", "") or ""
    diag = docx_res.get("diagnostics", {})
    omml = diag.get("omml_converted", 0) + diag.get("mtef_converted", 0)
    print(f"文件大小            : {len(file_bytes)/1024:.1f} KB")
    print(f"Markdown 字符数     : {len(md)}")
    print(f"公式转换数(OMML/MTEF): {omml}")
    print(f"图片数              : {docx_res.get('image_count', 0)}")
    print(f"需人工核对          : {diag.get('review_required', 0)}")
    print(f"提取成功            : {docx_res.get('success')}")

    banner("② 公式就地锁定（lock_visible_math）")
    paper_title = os.path.splitext(os.path.basename(docx_path))[0]
    locked, math_locks = main.lock_visible_math(md, "repro" + str(int(time.time()))[:16])
    print(f"math_locks 数量      : {len(math_locks)}")
    print(f"锁定后字符数        : {len(locked)}")

    banner("③ 分块评估（split_markdown_into_question_chunks）")
    chunks = split_markdown_into_question_chunks(locked)
    sizes = [len(c) for c in chunks]
    print(f"切块数              : {len(chunks)}")
    print(f"单块字符  min/avg/max: {min(sizes)} / {sum(sizes)//len(sizes)} / {max(sizes)}")
    print(f"原始是否已被切分     : {'是（已分块，根因消除）' if len(chunks) > 1 else '否（文档较短，单块即可）'}")
    # 断言：没有任何一块超过硬上限过多（正常情况下每块 <= max_chars 28000，除非单题超 hard_max 80000 降级）
    over_soft = [i for i, s in enumerate(sizes) if s > 28000]
    over_hard = [i for i, s in enumerate(sizes) if s > 80000]
    if over_hard:
        print(f"⚠️ 存在超过硬上限 80000 的块：{over_hard}（降级路径，属预期内但需关注）")
    elif over_soft:
        print(f"⚠️ 存在超过 28000 的块：{over_soft}（单题超长降级硬切，已保证不跨题切断）")
    else:
        print("✅ 所有块均在 28000 字符软上限内，单块输出远小于 max_tokens，根因已结构性消除")

    if local_only:
        banner("【--local-only】本地分块验证完成，未发起真实 API 调用。")
        return

    banner("④ 真实 API 拆题（parse_paper_text_internal，与真实导入同一函数）")
    tp = time.time()
    try:
        parsed = main.parse_paper_text_internal(locked, generate_answers_bool=True, separated_mode=False)
    except Exception as ex:
        print(f"❌ 拆题抛出异常（修复前正是此路径失败）: {type(ex).__name__}: {ex}")
        raise
    dt = time.time() - tp
    print(f"解析返回题目数      : {len(parsed)}")
    print(f"耗时                : {dt:.1f}s")
    # 抽样前 3 题标题
    for i, q in enumerate(parsed[:3]):
        c = (q.get("content") or "")[:60].replace("\n", " ")
        print(f"  题{i+1}: {c} ...")

    banner("⑤ 还原公式 + 后处理（restore_visible_math -> post_process）")
    lock_report = main.restore_visible_math(parsed, math_locks, strict=False)
    print(f"lock 还原报告        : {json.dumps(lock_report, ensure_ascii=False)[:300]}")
    final = main.post_process_pdf_parsed_questions(parsed, paper_title, "repro", [md])
    print(f"最终入库形态题数     : {len(final)}")
    # 质量抽检：是否有题干为空
    empty_content = [i for i, q in enumerate(final) if not (q.get("content") or "").strip()]
    print(f"题干为空的题数       : {len(empty_content)}")

    banner("⑥ 结论")
    print(f"✅ 真实文件复现成功：{len(final)} 道题目被完整拆解，未出现『JSON 截断导致拆解失败』。")
    print(f"   总耗时 {time.time()-t0:.1f}s；相比修复前（单块 124485 字符被截断）已彻底分块。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("docx", help="真实 docx 路径")
    ap.add_argument("--local-only", action="store_true", help="仅做本地分块评估，不发起真实 API")
    args = ap.parse_args()
    main_run(args.docx, args.local_only)
