"""错题工作台端到端验证（阶段二：真实 VLM 识别）。

阶段一用「直接写记录」代替了识别这一步，阶段二把它换成**真实的 API 调用**：
新建批次 → 切题 → 标记错题 → 调用识别任务（走项目既有多模态引擎链）
→ 校验题面确实被识别出来 → 导出错题本 PDF → 入库。

不 mock 任何业务逻辑，不打桩，不伪造结果。
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

#: 仓库根目录：本脚本位于 tools/ 下，向上一级即仓库根。不写死绝对路径，
#: 换机器 / 换目录 / 别人 clone 下来都能直接跑。
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
# 让 main 认为自己在 pytest 里跑：跳过运行时锁与启动清理线程
sys.argv = ["pytest", "verify_mistake_recognize.py"]

WORK = Path(tempfile.mkdtemp(prefix="mistake_recognize_"))
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

FAILURES = []


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
        time.sleep(1.0)
    return last


def build_sample_pdf() -> Path:
    import fitz
    from PIL import Image

    pages_dir = Path("/tmp/mistake_split_demo/pages")
    out = WORK / "sample_12p.pdf"
    doc = fitz.open()
    for index in range(1, 13):
        png = pages_dir / f"page_{index}.png"
        jpg = WORK / f"page_{index}.jpg"
        with Image.open(png) as image:
            width, height = image.size
            image.convert("RGB").save(jpg, format="JPEG", quality=80, optimize=True)
        page = doc.new_page(width=width * 72 / 200, height=height * 72 / 200)
        page.insert_image(page.rect, filename=str(jpg))
    doc.save(out, deflate=True)
    doc.close()
    return out


print("=" * 72)
print("阶段二：真实 VLM 识别链路验证")
print("=" * 72)

# ---- 0. 环境自检：确认真的读到了 Key（而不是悄悄走了假路径） ----
from mathbank.ai_providers import resolve_ocr_fallbacks  # noqa: E402

providers = resolve_ocr_fallbacks(os.getenv("OCR_PREFER_ENGINE", "siliconflow"))
check(
    "读到已配置的识图 Key",
    bool(providers),
    [f"{p.provider_label}/{p.model_name}" for p in providers],
)
if not providers:
    print("没有可用的识图 Key，阶段二无法进行。")
    sys.exit(1)

sample = build_sample_pdf()
print(f"样本 PDF: {sample} ({sample.stat().st_size / 1024 / 1024:.2f} MB)")

with open(sample, "rb") as handle:
    res = client.post(
        "/api/mistakes/batches",
        files={"file": ("sample_12p.pdf", handle, "application/pdf")},
        data={
            "subject": "physics",
            "title": "2026-09-12 物理错题",
            "batch_date": "2026-09-12",
            "student_name": "cyx",
            "student_grade": "高一",
        },
    )
batch_id = res.json()["batch"]["id"]
check("建批次", bool(batch_id), f"batch_id={batch_id}")

state = poll(client.post(f"/api/mistakes/batches/{batch_id}/cut").json()["task_id"])
check("切题完成", state.get("status") == "completed", state.get("log") or state.get("error"))

detail = client.get(f"/api/mistakes/batches/{batch_id}").json()
page1 = [item for item in detail["records"] if item["page_no"] == 1]
check("第 1 页切出题块", len(page1) >= 2, f"{len(page1)} 块")

# 把第 1 页前两块标为错题（识别只跑错题）
picked = page1[:2]
for item in picked:
    client.put(f"/api/mistakes/records/{item['id']}", json={"grad_status": "incorrect"})

res = client.post(f"/api/mistakes/batches/{batch_id}/recognize", json={})
recognize_payload = res.json()
check(
    "识别任务只收错题（默认口径）",
    res.status_code == 200 and recognize_payload.get("record_count") == len(picked),
    recognize_payload,
)
state = poll(recognize_payload["task_id"], timeout=900)
check(
    "识别任务完成",
    state.get("status") == "completed",
    f"status={state.get('status')} recognized={state.get('recognized')} failed={state.get('failed')} "
    f"providers={state.get('providers')} errors={state.get('errors')}",
)
check(
    "识别没有失败块",
    state.get("failed") == 0,
    state.get("errors"),
)
steps = {step["key"]: step["status"] for step in state.get("steps", [])}
check(
    "识别步骤条 3 步全 done",
    steps == {"recognize_blocks": "done", "normalize": "done", "write_records": "done"},
    steps,
)

detail = client.get(f"/api/mistakes/batches/{batch_id}").json()
recognized = [item for item in detail["records"] if item["recognize_status"] == "done"]
check("记录状态 = done", len(recognized) == len(picked), f"{len(recognized)} 条")
check(
    "题面已识别（非空 + 含 LaTeX）",
    all(item["content"] and ("$" in item["content"] or "\\" in item["content"]) for item in recognized),
    [item["content"][:60].replace("\n", " ") for item in recognized],
)
check(
    "题型/难度被归一",
    all(item["question_type"] in main.MISTAKE_QUESTION_TYPES for item in recognized),
    [(item["question_type"], item["difficulty"]) for item in recognized],
)
print("\n---- 真实识别结果（原文） ----")
for item in recognized:
    print(f"[记录 {item['id']} 题号 {item['question_no']!r} 题型 {item['question_type']}]")
    print(item["content"])
    print(f"  知识点: {item['knowledge_tags']} | 方法: {item['solve_method']}")
print("---- 识别结果结束 ----\n")

# ---- 导出（真实识别题面 → PDF） ----
res = client.post(
    f"/api/mistakes/batches/{batch_id}/export",
    json={"solution_space_cm": 6.0, "answer_mode": "none"},
)
export = res.json()
check(
    "真实识别题面生成 PDF",
    res.status_code == 200,
    export.get("message") or export.get("pdf_url"),
)
pdf_bytes = client.get(f"/api/mistakes/batches/{batch_id}/export/download").content
pdf_path = WORK / "handout_real.pdf"
pdf_path.write_bytes(pdf_bytes)
import fitz  # noqa: E402

with fitz.open(pdf_path) as doc:
    text = "".join(page.get_text() for page in doc)
    pages = doc.page_count
check("PDF 可打开", pages >= 1, f"pages={pages} size={len(pdf_bytes)}")
check(
    "PDF 含识别出的题干文字",
    any(
        fragment and fragment in text
        for fragment in [
            "".join(ch for ch in item["content"] if "\u4e00" <= ch <= "\u9fff")[:6]
            for item in recognized
        ]
    ),
    text[:120].replace("\n", " "),
)

# ---- 入库 ----
res = client.post(f"/api/mistakes/batches/{batch_id}/import-to-bank", json={})
payload = res.json()
check(
    "物理错题不进题库（一期规则）",
    res.status_code == 200 and payload.get("imported", 0) == 0 and len(payload.get("skipped", [])) == len(recognized),
    {k: payload.get(k) for k in ("imported", "already", "duplicates")},
)

print("=" * 72)
if FAILURES:
    print(f"❌ 失败 {len(FAILURES)} 项：")
    for item in FAILURES:
        print("   -", item)
else:
    print("✅ 阶段二全部通过（真实 API 识别）")
print(f"工作目录: {WORK}")
print("=" * 72)
sys.exit(1 if FAILURES else 0)
