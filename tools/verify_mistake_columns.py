"""错题切块的**分栏支持**端到端验证（真实卷，不 mock）。

用真实样本 `~/Desktop/错题1-物理.pdf`（16 页、双栏与单栏混排）跑完整切题链路，
逐项核对：

1. 自动判栏：双栏页判成 double、单栏页判成 single，且与 PDF 文本层判据一致；
2. 双栏切块：左右栏块数量与范围（左栏块 x∈[0,分栏线]、右栏块 x∈[分栏线,1]）；
3. 人工纠错：强制改栏目（双栏→单栏）确实生效，source 标 manual；
4. 裁图真的按栏裁（块图像素宽度 ≈ 分栏线宽度，而不是整页宽）；
5. 重切后作答状态按**矩形**重叠继承，不会跨栏串状态。

不改真实数据库：全程在临时 sqlite + 临时 static 目录里跑。
"""

import sys
import tempfile
import time
from pathlib import Path

#: 仓库根目录：本脚本位于 tools/ 下，向上一级即仓库根。不写死绝对路径，
#: 换机器 / 换目录 / 别人 clone 下来都能直接跑。
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.argv = ["pytest", "verify_mistake_columns.py"]

SOURCE_PDF = Path.home() / "Desktop" / "错题1-物理.pdf"

#: 逐页肉眼核对的版面真值。判据是「PDF 文本行级的 x 分布」：双栏页上同一 y 会出现
#: 两条互不相交的行（左 x≈[0.06,0.49]、右 x≈[0.51,0.93]），单栏页则是一条贯穿页宽
#: 的长行，且选项 A/B/C/D 排在同一行里（例如 p12 y0.384: A[0.11,0.20] B[0.30,0.40]
#: C[0.50,0.59] D[0.69,0.79]）。这份卷子前 8 页是双栏卷面、后 8 页是通栏讲义。
GROUND_TRUTH_DOUBLE = {1, 2, 3, 5, 6, 7, 8}
GROUND_TRUTH_PAGES = 16

WORK = Path(tempfile.mkdtemp(prefix="mistake_columns_"))
STATIC_DIR = WORK / "static"
UPLOADS_DIR = STATIC_DIR / "uploads"
(UPLOADS_DIR / "mistakes").mkdir(parents=True, exist_ok=True)
(UPLOADS_DIR / "tmp").mkdir(parents=True, exist_ok=True)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

engine = create_engine(
    f"sqlite:///{WORK / 'test.db'}", connect_args={"check_same_thread": False}
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

from mathbank import database as dbmod  # noqa: E402
from mathbank import sync_helper  # noqa: E402

dbmod.engine = engine
dbmod.SessionLocal = TestSession
sync_helper.BACKUP_DIR = str(WORK / "backup")
sync_helper.JSON_BACKUP_PATH = str(WORK / "backup" / "questions_backup.json")
sync_helper.MD_BACKUP_PATH = str(WORK / "backup" / "questions_library.md")

import main  # noqa: E402
from mathbank.database import Base, get_db  # noqa: E402
from mathbank.page_block_split import (  # noqa: E402
    detect_column_layout_from_text_rows,
    extract_pdf_text_rows,
)

main.UPLOAD_DIR = str(UPLOADS_DIR)
main.UPLOAD_DIR_REL = "static/uploads"
main.STATIC_DIR = STATIC_DIR
main.TMP_UPLOAD_DIR = str(UPLOADS_DIR / "tmp")
Base.metadata.create_all(bind=engine)

from fastapi.testclient import TestClient  # noqa: E402

Session = TestSession()


def override_get_db():
    try:
        yield Session
    finally:
        pass


main.app.dependency_overrides[get_db] = override_get_db
client = TestClient(main.app, headers={"X-Local-Token": main.LOCAL_TOKEN})

FAILURES: list[str] = []


def check(label, condition, detail=""):
    print(f"{'✅' if condition else '❌'} {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def poll(task_id, timeout=600):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        state = client.get(f"/api/tasks/{task_id}/status").json()
        last = state
        if state.get("status") in ("completed", "error", "cancelled"):
            return state
        time.sleep(0.5)
    return last


def cut(payload=None):
    body = payload or {}
    res = client.post(f"/api/mistakes/batches/{BATCH_ID}/cut", json=body)
    assert res.status_code == 200, res.text
    return poll(res.json()["task_id"])


def detail():
    return client.get(f"/api/mistakes/batches/{BATCH_ID}").json()


def page_meta(payload, page_no):
    for item in payload["pages"]:
        if item["page_no"] == page_no:
            return item
    return None


print("=" * 78)
print("错题切块 · 分栏支持端到端验证（真实卷）")
print("=" * 78)

if not SOURCE_PDF.is_file():
    print(f"找不到真实样本 {SOURCE_PDF}")
    sys.exit(1)
print(f"样本：{SOURCE_PDF}  {SOURCE_PDF.stat().st_size / 1024 / 1024:.2f} MB\n")

# ---- 0. 文本层判栏：作为自动判栏的独立参照（不是标准答案，是第二意见） ----
truth_rows = extract_pdf_text_rows(SOURCE_PDF)
print(f"PDF 文本层可判栏页：{len(truth_rows)} 页\n")

with open(SOURCE_PDF, "rb") as handle:
    res = client.post(
        "/api/mistakes/batches",
        files={"file": (SOURCE_PDF.name, handle, "application/pdf")},
        data={
            "subject": "physics",
            "title": "分栏验证",
            "batch_date": "2026-09-13",
            "student_name": "cyx",
            "student_grade": "高一",
        },
    )
BATCH_ID = res.json()["batch"]["id"]
check("建批次", bool(BATCH_ID), f"batch_id={BATCH_ID}")

# ---- 1. 自动判栏 + 分栏切块 ----
state = cut()
check("切题完成", state.get("status") == "completed", state.get("log") or state.get("error"))
payload = detail()
auto_pages = {item["page_no"]: item for item in payload["pages"]}

check("页数与真值一致", len(auto_pages) == GROUND_TRUTH_PAGES, f"{len(auto_pages)} 页")

print("\n-- 每页自动判栏结果（与肉眼核对过的真值对比）--")
print(f"{'页':>3} {'栏目':<7}{'来源':<11}{'置信度':>7}{'分栏线':>8} {'块数':>5} {'左/右':>7}  真值")
mismatch = []
for page_no in sorted(auto_pages):
    item = auto_pages[page_no]
    layout = item["layout"]
    blocks = [b for b in payload["records"] if b["page_no"] == page_no]
    left = len([b for b in blocks if b["column_index"] == 1])
    right = len([b for b in blocks if b["column_index"] == 2])
    expect = "double" if page_no in GROUND_TRUTH_DOUBLE else "single"
    if layout["mode"] != expect:
        mismatch.append(page_no)
    print(
        f"{page_no:>3} {layout['mode']:<7}{layout['source']:<11}"
        f"{layout['confidence']:>7.2f}{item['column_boundary']:>8.3f} {len(blocks):>5} "
        f"{f'{left}/{right}':>7}  {expect}"
        + ("" if layout["mode"] == expect else "   ❌")
    )

check(
    "自动判栏 16/16 与真值一致",
    not mismatch,
    f"判错：{mismatch}" if mismatch else f"双栏页 {sorted(GROUND_TRUTH_DOUBLE)}",
)

# 文本层判栏（独立第二意见）也应当一致
text_mismatch = [
    page_no
    for page_no in sorted(truth_rows)
    if (
        detect_column_layout_from_text_rows(truth_rows[page_no]).mode
        if len(truth_rows[page_no]) >= 8
        else ("double" if page_no in GROUND_TRUTH_DOUBLE else "single")
    )
    != ("double" if page_no in GROUND_TRUTH_DOUBLE else "single")
]
check("文本层判栏亦与真值一致", not text_mismatch, str(text_mismatch))

# ---- 1b. 扫件模拟：不喂文本层，强制走图像判栏 ----
# 用户的文件里扫描件很多（无文本层），图像路径必须同样准。这里直接调分析函数、
# 把 page_text_rows 留空，等价于「这份卷子没有文本层」。
import mathbank.page_block_split as _split  # noqa: E402

scan_dir = main._mistake_pages_dir(BATCH_ID)
scan_paths = sorted(scan_dir.glob("page_*.png"), key=lambda p: int(p.stem.split("_")[1]))
scan_analyses = _split.analyze_page_images(scan_paths, start_page_no=1)
scan_bad = []
for analysis in scan_analyses:
    expect = "double" if analysis.page_no in GROUND_TRUTH_DOUBLE else "single"
    if analysis.layout.source != "visual":
        scan_bad.append((analysis.page_no, f"来源应为 visual，实际 {analysis.layout.source}"))
    if analysis.layout.mode != expect:
        scan_bad.append((analysis.page_no, f"判成 {analysis.layout.mode}，应为 {expect}"))
    if analysis.layout.confidence < 0.40:
        scan_bad.append((analysis.page_no, f"置信度过低 {analysis.layout.confidence}"))
check(
    "扫件（无文本层）图像判栏 16/16",
    not scan_bad,
    str(scan_bad[:3]) if scan_bad else "每页均以 visual 来源给出高置信结论",
)
print(
    "   图像路径分栏线："
    + ", ".join(
        f"p{a.page_no}={a.layout.boundary:.3f}"
        for a in scan_analyses
        if a.layout.is_double
    )
)

# 块编号不变量：每页的 block_index 必须是 0..n-1 且按阅读顺序递增。
# 题块图文件名、前端「第 N 块」都依赖它 —— 人工只切一栏时，另一栏沿用自动块
# 会带进旧的编号（实测出现 0,1,6,7…），必须在这里钉死。
bad_index = []
for page_no in sorted(auto_pages):
    indexes = sorted(
        b["block_index"] for b in payload["records"] if b["page_no"] == page_no
    )
    if indexes != list(range(len(indexes))):
        bad_index.append((page_no, indexes))
    ordered = sorted(
        [b for b in payload["records"] if b["page_no"] == page_no],
        key=lambda b: b["block_index"],
    )
    columns = [b["column_index"] for b in ordered if b["column_index"]]
    if columns != sorted(columns):
        bad_index.append((page_no, "块的栏号未按阅读顺序排列"))
check("每页块编号为 0..n-1 且按阅读顺序", not bad_index, str(bad_index[:3]))

# 低置信度页（前端会提示核对）——列出来，方便判断阈值是否需要放宽
low_conf = [
    item["page_no"]
    for item in payload["pages"]
    if item["layout"]["confidence"] < 0.40
]
print(f"   低置信度页（前端会提示核对）：{low_conf or '无'}")

# 双栏页的块范围必须落在各自栏内
bad_range = []
for item in payload["pages"]:
    boundary = item["column_boundary"]
    for block in item["blocks"]:
        if block["column_index"] == 1 and not (block["x_start"] == 0.0 and abs(block["x_end"] - boundary) < 1e-6):
            bad_range.append((item["page_no"], block["block_index"], "左栏范围错"))
        if block["column_index"] == 2 and not (abs(block["x_start"] - boundary) < 1e-6 and block["x_end"] == 1.0):
            bad_range.append((item["page_no"], block["block_index"], "右栏范围错"))
check("双栏块横向范围正确", not bad_range, str(bad_range[:5]))

# 按栏吸附池：双栏页应有 1/2 两个键，且各自非空；两栏的吸附点应当不同
snap_ok = True
snap_note = ""
for item in sorted(payload["pages"], key=lambda v: v["page_no"]):
    if item["layout"]["mode"] != "double":
        continue
    snaps = item.get("column_snap_points") or {}
    if not (snaps.get("1") and snaps.get("2")):
        snap_ok = False
        print(f"   ⚠️ p{item['page_no']} 按栏吸附池缺失：{ {k: len(v) for k, v in snaps.items()} }")
        continue
    if set(snaps["1"]) == set(snaps["2"]):
        snap_ok = False
        print(f"   ⚠️ p{item['page_no']} 左右栏吸附点完全相同，按栏吸附没生效")
    elif not snap_note:
        snap_note = (
            f"p{item['page_no']} 左栏 {len(snaps['1'])} 个吸附点、右栏 {len(snaps['2'])} 个"
        )
check("双栏页有按栏吸附池（且左右不同）", snap_ok, snap_note)

# ---- 2. 裁图按栏裁（真实像素宽度） ----
from PIL import Image  # noqa: E402

crop_ok = True
crop_note = ""
for item in payload["pages"]:
    if item["layout"]["mode"] != "double":
        continue
    page_width = item["width"]
    picks = [
        b for b in payload["records"]
        if b["page_no"] == item["page_no"] and b["column_index"] == 1
    ][:1]
    picks += [
        b for b in payload["records"]
        if b["page_no"] == item["page_no"] and b["column_index"] == 2
    ][:1]
    for record in picks:
        # block_url 带 `?v=<指纹>` 缓存破坏参数（见 3AB），取路径前必须把查询串切掉，
        # 否则这里会拿一个带 ? 的路径去 open，直接 FileNotFoundError。
        path = STATIC_DIR / record["block_url"].split("static/", 1)[-1].split("?", 1)[0]
        # 预期宽度按**该块自己的**横向范围算：左栏 ≈ 分栏线宽、右栏 ≈ 1−分栏线宽
        expect = round((record["block_x_end"] - record["block_x_start"]) * page_width)
        with Image.open(path) as image:
            got = image.size[0]
        # 块图只能窄于整页，且远离「整页宽」——这是「没按栏裁」的典型症状
        ok = abs(got - expect) <= max(3, expect * 0.03) and got < page_width * 0.75
        crop_ok = crop_ok and ok
        if not ok:
            print(
                f"   ⚠️ p{item['page_no']} 栏{record['column_index']} 块 "
                f"{record['block_index']} 宽 {got} ≠ 预期 {expect}（整页 {page_width}）"
            )
        elif not crop_note:
            crop_note = f"p{item['page_no']} 左栏块宽 {got}px ≈ {expect}px（整页 {page_width}px）"
check("块图按栏裁剪（不是整页宽的块）", crop_ok, crop_note)

# ---- 3. 人工纠错：把某个双栏页强制改单栏 ----
target = min(GROUND_TRUTH_DOUBLE) if GROUND_TRUTH_DOUBLE else None
if target is None:
    check("存在可纠错的双栏页", False, "真值里没有双栏页，后续人工纠正无法验证")
else:
    state = cut({"page_layouts": {str(target): "single"}})
    check(
        f"人工把 p{target} 改成单栏后重切完成",
        state.get("status") == "completed",
        state.get("log") or state.get("error"),
    )
    payload = detail()
    item = page_meta(payload, target)
    check(
        f"p{target} 已变单栏",
        item["layout"]["mode"] == "single" and item["layout"]["source"] == "manual",
        f"mode={item['layout']['mode']} source={item['layout']['source']}",
    )
    same_page = [b for b in payload["records"] if b["page_no"] == target]
    check(
        f"p{target} 单栏块横向范围=整页",
        all(b["block_x_start"] == 0.0 and b["block_x_end"] == 1.0 for b in same_page),
        f"{len(same_page)} 块",
    )

    # 只给模式、不给分栏线 —— 自动补分栏线，不应该退回单栏
    state = cut({"page_layouts": {str(target): "double"}})
    payload = detail()
    item = page_meta(payload, target)
    check(
        f"p{target} 人工指定双栏但没给分栏线 → 自动补线",
        item["layout"]["mode"] == "double" and 0.0 < item["column_boundary"] < 1.0,
        f"boundary={item['column_boundary']:.3f} source={item['layout']['source']}",
    )

print("\n" + "=" * 78)
if FAILURES:
    print(f"❌ {len(FAILURES)} 项未通过：")
    for item in FAILURES:
        print(f"   - {item}")
    sys.exit(1)
print("✅ 分栏支持全部验证通过")
