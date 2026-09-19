import os
import atexit
import io
import platform
import sys
import uuid
import json
import time
import re
import signal
import datetime
import threading
import requests
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request, Response, Header
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, text
from sqlalchemy import or_, text
from dotenv import load_dotenv

from mathbank.database import (
    MistakeBatch,
    MistakeRecord,
    Question,
    QuestionCurriculum,
    Paper,
    PaperQuestion,
    Student,
    engine,
    get_db,
    init_db,
    normalize_question_content,
)
from mathbank.duplicate_check import (
    duplicate_warning_payload,
    find_duplicate_question,
)

# 查重指纹的唯一权威实现位于 mathbank.database，此处仅保留兼容性别名
_normalize_question_content = normalize_question_content
from mathbank.paper_helper import build_latex_document, build_answer_sheet_latex, compile_tex_to_pdf, create_tex_zip_package, create_full_bundle_zip_package, collect_referenced_images, build_restricted_tex_environment
from mathbank.word_export_helper import build_word_document, create_word_bundle_zip
from mathbank.sync_helper import export_database_to_files
from mathbank.backup import (
    DEFAULT_RETENTION,
    PENDING_RESTORE_TTL_SECONDS,
    acquire_runtime_lock,
    apply_pending_restore,
    clear_pending_restore,
    create_full_backup,
    create_full_backup_if_due,
    create_pre_restore_backup,
    list_full_backups,
    read_pending_restore,
    resolve_full_backup,
    verify_full_backup,
    write_pending_restore,
)
from mathbank.sample_library import (
    DEFAULT_MARKER_SOURCE as SAMPLE_QUESTIONS_SOURCE,
    import_sample_questions,
    sample_question_count,
)
from mathbank.health import readiness_report
from mathbank.task_manager import (
    TaskCancelled,
    TaskManager,
    TaskQueueFull,
)
from mathbank.docx_helper import (
    extract_docx_markdown,
    compress_docx_image_links,
    decompress_docx_image_links_in_questions,
)
from mathbank.docx_font_normalizer import normalize_docx_fonts
from mathbank.content_locks import lock_visible_math, restore_visible_math
from mathbank.tex_helper import (
    MAX_TEX_BYTES,
    decode_and_prepare_tex,
    prepare_tex_source,
    tex_asset_basename,
    tex_asset_references_match,
)
from mathbank.latex_diagnostics import (
    build_local_latex_diagnostic,
    is_xelatex_missing,
    merge_ai_latex_diagnostic,
)
from mathbank.ai_json import parse_ai_json, detect_truncation_signals
from mathbank.paper_chunking import (
    DEFAULT_MAX_QUESTIONS,
    dedupe_questions,
    split_chunks_by_question_count,
    _question_number_positions,
    split_markdown_into_question_chunks,
)
from mathbank.ai_http import (
    post_chat_completion,
)
from mathbank.ai_providers import (
    MultimodalProviderConfig,
    apply_bailian_thinking_policy,
    inject_reasoning_effort,
    resolve_draw_provider,
    resolve_ocr_fallbacks,
    resolve_ocr_provider,
    resolve_text_provider,
)
from mathbank.free_model_routing import (
    decide_parse_model,
    decide_classify_model,
)
from mathbank.curriculums import (
    CURRICULUM_NAMES,
    DEFAULT_QUESTION_TYPES,
    DIFFICULTY_VALUES,
    SUBJECT_LABELS,
    SUBJECT_ORDER,
    build_default_metadata,
    default_version_for_subject,
    get_curriculum_preset,
    load_curriculum,
    normalize_difficulty,
    normalize_subject,
    normalize_tag_list,
    versions_for_subject,
)
from mathbank.prompts import (
    COMMON_OCR_PROMPT,
    ILLUSTRATION_BOX_PROMPT,
    build_ai_solve_prompts,
    build_answer_rule,
    build_classification_system_prompt,
    build_import_parse_system_prompt,
    build_latex_error_explanation_prompts,
    build_mistake_block_system_prompt,
    build_paper_selection_prompts,
    build_pdf_parse_system_prompt,
    build_tikz_correction_prompt,
    build_tikz_draw_prompt,
)
from mathbank.page_block_split import (
    DEFAULT_MAX_PAGES as MISTAKE_MAX_SOURCE_PAGES,
    DEFAULT_RENDER_DPI as MISTAKE_RENDER_DPI,
    COLUMN_DOUBLE,
    COLUMN_SINGLE,
    BlockRegion,
    ColumnLayout,
    PageSplitError,
    analyze_page_images,
    clean_manual_boxes,
    crop_region,
    extract_pdf_text_lines,
    extract_pdf_text_rows,
    manual_column_layout,
    merge_manual_boxes,
    render_source_to_pages,
)
from mathbank.mistake_handout import (
    HandoutOptions,
    build_mistake_handout_latex,
)
from mathbank.mistake_vocabulary import (
    list_mistake_reasons,
    normalize_mistake_reason_list,
)
from mathbank.question_types import (
    detect_structured_question_form,
    normalize_ai_question_form,
)
from mathbank.latex_normalize import normalize_choice_options_to_latex
from mathbank.choice_recovery import recover_missing_choices
from mathbank.source_normalize import normalize_source, CANONICAL_MAP, SCHOOL_ALIASES
from mathbank.headless_libreoffice_profile import (
    build_soffice_command as _build_soffice_command,
    find_soffice as _find_soffice,
)
import shutil
from mathbank.pdf_inspector_helper import (
    is_pdf_inspector_available,
    inspect_and_extract_pdf,
    merge_pdf_page_texts,
)
from mathbank.paths import (
    DATABASE_PATH,
    DATA_BACKUP_DIR,
    ENV_FILE,
    FULL_BACKUP_DIR,
    PROJECT_ROOT,
    STATIC_CSS_DIR,
    STATIC_DIR,
    STATIC_JS_DIR,
    SYSTEM_GENERATED_DIR,
    TEST_UPLOADS_DIR,
    UPLOADS_DIR,
)
from mathbank.asset_security import (
    AssetSecurityError,
    InvalidImageError,
    MAX_OCR_IMAGE_BYTES,
    MAX_PDF_BYTES,
    MAX_SINGLE_IMAGE_BYTES,
    UploadTooLargeError,
    harden_private_path,
    normalize_raster_image,
    normalize_upload_asset_reference,
    normalize_upload_asset_references,
    read_stream_limited,
    resolve_upload_asset,
    write_private_text_atomic,
)

# Load environment variables
try:
    load_dotenv(ENV_FILE)
    harden_private_path(ENV_FILE)
except Exception as e:
    print(f"[WARN] 无法加载/加固 .env 文件（{e}）；将使用已存在的环境变量或默认值继续。")

# Unique server instance ID generated per process launch/restart
SERVER_INSTANCE_ID = str(uuid.uuid4())

# Hold an OS-backed project lock before the first database access.  The restore
# CLI takes the same lock, so a manually started uvicorn process is protected
# even when no launcher PID file exists.  Unit tests use isolated databases and
# exercise the lock helper directly instead of holding the production lock.
IS_TESTING = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv)
_RUNTIME_LOCK = None if IS_TESTING else acquire_runtime_lock()
if _RUNTIME_LOCK is not None:
    atexit.register(_RUNTIME_LOCK.close)

# 延迟还原：待还原请求只能在「服务已停止」的窗口里落地。此刻锁刚拿到、数据库还
# 没打开，是唯一的合法时机；若放到数据库初始化之后，会先按旧库跑完迁移再换库。
# 测试环境既不持锁、也不该动开发者本机的真实库，因此整段跳过。
if _RUNTIME_LOCK is not None:
    try:
        _pending_restore_result = apply_pending_restore()
    except Exception as exc:  # noqa: BLE001 - 还原失败不能让服务彻底起不来
        print(
            "[Restore Error] 待还原请求未完成，已按现有数据继续启动："
            f"{type(exc).__name__}: {exc}"
        )
    else:
        if _pending_restore_result:
            print(f"[Restore] 已按待还原请求切回 {_pending_restore_result['archive']}")

# Initialize DB
init_db()


def schedule_database_export(
    background_tasks: BackgroundTasks, *, operation: str
) -> None:
    """Best-effort export scheduling after a database transaction commits."""

    def run_export_safely() -> None:
        try:
            export_database_to_files()
        except Exception as exc:
            print(
                f"[Database Export] Post-commit export failed for {operation} "
                f"(type={type(exc).__name__}); the next write/startup export can retry."
            )

    try:
        background_tasks.add_task(run_export_safely)
    except Exception as exc:
        print(
            f"[Database Export] Post-commit scheduling failed for {operation} "
            f"(type={type(exc).__name__}); the next write/startup export can retry."
        )


def print_startup_diagnostics():
    """打印美观的系统环境、虚拟环境与加速引擎自检面板"""
    is_venv = sys.prefix != sys.base_prefix
    env_type = f"虚拟环境 ({os.path.basename(sys.prefix)})" if is_venv else "全局/系统环境"
    pdf_insp_ok = is_pdf_inspector_available()
    
    # 检查 LaTeX 编译器
    latex_engine = "未检测到 (需安装 TeX Live / MacTeX)"
    if shutil.which("xelatex"):
        latex_engine = "XeLaTeX (已就绪)"
    elif shutil.which("pdflatex"):
        latex_engine = "pdfLaTeX (已就绪)"

    pandoc_path = os.getenv("MATHBANK_PANDOC_PATH", "").strip() or shutil.which("pandoc")
    pandoc_status = f"已就绪 ({pandoc_path})" if pandoc_path else "未检测到 (Word 公式将使用图片兜底)"

    # 检查 LibreOffice（Word 转 PDF 预览 / docx 渲染依赖）
    soffice_path = shutil.which("soffice") or shutil.which("libreoffice") or ""
    libreoffice_status = f"已就绪 ({soffice_path})" if soffice_path else "未检测到 (Word 原卷预览转 PDF 不可用)"

    # 检查 PyMuPDF
    fitz_ok = False
    try:
        import fitz
        fitz_ok = True
    except ImportError:
        pass

    print("=" * 64, flush=True)
    print("      小陈的数学宝藏 · 本地数学题库与备课工作台 —— 启动自检面板", flush=True)
    print("=" * 64, flush=True)
    print(f"  • Python 运行环境   : {sys.version.split()[0]} [{env_type}]", flush=True)
    print(f"  • Python 可执行路径 : {sys.executable}", flush=True)
    if is_venv:
        print(f"  • 虚拟环境根目录   : {sys.prefix}", flush=True)
    print(f"  • PDF Inspector 引擎: {'🚀 已就绪 (原生矢量试卷毫秒级直提)' if pdf_insp_ok else '⚠️ 未安装 (已自动平滑降级至 VLM 多模态 OCR)'}", flush=True)
    print(f"  • PyMuPDF 渲染器    : {'✅ 已就绪' if fitz_ok else '❌ 未安装 (建议 pip install pymupdf)'}", flush=True)
    print(f"  • LaTeX 编译排版    : {latex_engine}", flush=True)
    print(f"  • Word 原生公式转换 : Pandoc {pandoc_status}", flush=True)
    print(f"  • Word 转 PDF 预览  : LibreOffice {libreoffice_status}", flush=True)
    print(f"  • SQLite 本地数据库 : {DATABASE_PATH}", flush=True)
    print(f"  • 项目静态与根路径 : {PROJECT_ROOT}", flush=True)
    print("=" * 64, flush=True)


print_startup_diagnostics()

def heal_database_curriculum_names():
    from mathbank.database import SessionLocal
    db = SessionLocal()
    try:
        mappings = {
            "选择性必修一": "选修一",
            "选择性必修二": "选修二",
            "选择性必修三": "选修三",
            "必修第一册": "必修一",
            "必修第二册": "必修二",
            "必修第三册": "必修三",
            "必修第四册": "必修四",
        }
        updated_questions = 0
        for old, new in mappings.items():
            res = db.query(Question).filter(Question.category_compulsory == old).update(
                {Question.category_compulsory: new}, synchronize_session=False
            )
            updated_questions += res
            
        updated_mappings = 0
        for old, new in mappings.items():
            res = db.query(QuestionCurriculum).filter(QuestionCurriculum.compulsory == old).update(
                {QuestionCurriculum.compulsory: new}, synchronize_session=False
            )
            updated_mappings += res

        # 清理在主表 questions 及镜像表 question_curriculums 中残留的不属于各自大纲小节列表的旧章名/错位知识点
        curr = get_current_curriculum()
        healed_know_count = 0
        # 只自愈数学题：下面这条校验的是「数学的活动大纲」（物理挂教科版、化学挂
        # 人教版，是两套独立目录树）。拿数学树去校验物化题会把物化章节判成非法，
        # 然后在每次启动时静默清空它们的知识点。
        all_qs = db.query(Question).all()
        for q in all_qs:
            if (q.subject or "math") != "math":
                continue
            comp = q.category_compulsory
            chap = q.category_chapter
            know = q.category_knowledge
            if know:
                valid_knows = curr.get(comp, {}).get(chap, [])
                if know not in valid_knows:
                    q.category_knowledge = ""
                    healed_know_count += 1

        all_qcs = db.query(QuestionCurriculum).all()
        for qc in all_qcs:
            if qc.knowledge:
                try:
                    c_tree = load_curriculum(qc.version_code)
                except ValueError:
                    c_tree = curr
                valid_knows = c_tree.get(qc.compulsory, {}).get(qc.chapter, [])
                if qc.knowledge not in valid_knows:
                    qc.knowledge = ""
                    healed_know_count += 1
            
        if updated_questions > 0 or updated_mappings > 0 or healed_know_count > 0:
            db.commit()
            print(f"[Self-Healing DB] Migrated {updated_questions} questions, {updated_mappings} mappings, and cleaned {healed_know_count} mismatched knowledge values.")
    except Exception as e:
        db.rollback()
        print(f"[Self-Healing DB Error] Failed to run database book names migration: {e}")
    finally:
        db.close()

UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"
UPLOAD_DIR = str(TEST_UPLOADS_DIR if IS_TESTING else UPLOADS_DIR)

def load_or_create_local_token() -> str:
    token_dir = str(SYSTEM_GENERATED_DIR)
    os.makedirs(token_dir, exist_ok=True)
    harden_private_path(token_dir, directory=True)
    token_file = os.path.join(token_dir, "local_token")
    if os.path.exists(token_file):
        try:
            harden_private_path(token_file)
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token and len(token) >= 16:
                    return token
        except Exception as e:
            print(f"[Security] Failed to read persistent token: {e}")
            
    # Generate new token
    token = secrets.token_hex(16)
    try:
        write_private_text_atomic(token_file, token)
    except Exception as e:
        print(f"[Security] Failed to write persistent token: {e}")
    return token

LOCAL_TOKEN = load_or_create_local_token()

app = FastAPI(title="本地化数学题库管理系统 API")

# Enable CORS for local development (restrict allowed origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Heartbeat & Security Middleware -----------------

@app.middleware("http")
async def security_and_heartbeat_middleware(request: Request, call_next):
    # Verify local security token for modifying operations
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.url.path != "/api/heartbeat":
            token = request.headers.get("X-Local-Token")
            if not token or not secrets.compare_digest(token, LOCAL_TOKEN):
                print(f"[Security Alert] Blocked {request.method} {request.url.path} - invalid local token")
                return JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "Forbidden: Invalid or missing local token."}
                )

    response = await call_next(request)
    return response


@app.post("/api/heartbeat")
def api_heartbeat():
    return {"status": "success"}


# 启动看门狗后台守护线程 (daemon=True 确保主线程消亡时其也随之释放)
# 原 watchdog_loop 因 macOS headless / 沙箱环境限制已弃用，保留注释便于追溯。
# threading.Thread(target=watchdog_loop, daemon=True).start()

# ----------------- 启动自愈：后台静默清理孤儿临时图片 -----------------
def clean_orphaned_images():
    """扫描 static/uploads 目录及其 tmp 子目录，安全彻底擦除未被数据库引用的孤儿图片与旧残留临时图片"""
    try:
        from mathbank.database import SessionLocal, Question
        db = SessionLocal()
        try:
            # 1. 搜集数据库中所有题目引用的图片路径
            questions = db.query(Question._image_paths).all()
            referenced_images = set()
            for (img_paths_str,) in questions:
                if img_paths_str:
                    try:
                        paths = json.loads(img_paths_str)
                        for path in paths:
                            referenced_images.add(path.lstrip("/").lower())
                    except Exception:
                        pass
                        
            # 2. 遍历本地图片目录及 tmp 子目录
            upload_dir = UPLOAD_DIR
            if not os.path.exists(upload_dir):
                return
                
            cleaned_count = 0
            now = time.time()
            one_hour_seconds = 3600
            
            # 清理 static/uploads/ 根目录下未引用的孤儿图片
            for filename in os.listdir(upload_dir):
                full_path = os.path.join(upload_dir, filename)
                if os.path.isfile(full_path) and not filename.startswith("."):
                    local_rel_path = f"{UPLOAD_DIR_REL}/{filename}".lower()
                    if local_rel_path not in referenced_images:
                        try:
                            mtime = os.path.getmtime(full_path)
                            if now - mtime > one_hour_seconds:
                                os.remove(full_path)
                                cleaned_count += 1
                        except Exception:
                            pass

            # 清理 static/uploads/tmp/ 子目录下残留的所有旧拆卷/OCR临时切片图
            tmp_dir = os.path.join(upload_dir, "tmp")
            if os.path.exists(tmp_dir):
                for filename in os.listdir(tmp_dir):
                    full_path = os.path.join(tmp_dir, filename)
                    if os.path.isfile(full_path) and not filename.startswith("."):
                        local_rel_path = f"{UPLOAD_DIR_REL}/tmp/{filename}".lower()
                        if local_rel_path not in referenced_images:
                            try:
                                mtime = os.path.getmtime(full_path)
                                if now - mtime > 600:  # 超过 10 分钟未被使用的 tmp 切片立即清理
                                    os.remove(full_path)
                                    cleaned_count += 1
                            except Exception:
                                pass
                        
            if cleaned_count > 0:
                print(f"[Storage Cleanup] 成功检测并清除 {cleaned_count} 个残留的旧临时图片与孤儿文件，磁盘无痕瘦身成功！")
        finally:
            db.close()
    except Exception as e:
        print(f"[Storage Cleanup Error] 执行静默图片净化时发生异常: {str(e)}")

def recalibrate_usage_counts():
    """自动校准全库题目的引用频次 usage_count，修正由于历史删除试卷遗留的计数差异"""
    try:
        from mathbank.database import SessionLocal, Question, PaperQuestion
        from sqlalchemy import func
        db = SessionLocal()
        try:
            counts = db.query(PaperQuestion.question_id, func.count(PaperQuestion.id)).group_by(PaperQuestion.question_id).all()
            ref_map = dict(counts)
            questions = db.query(Question).all()
            changed = False
            for q in questions:
                actual_ref = ref_map.get(q.id, 0)
                if (q.usage_count or 0) != actual_ref:
                    q.usage_count = actual_ref
                    changed = True
            if changed:
                db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[Usage Calibration Error] {e}")

def start_startup_cleanup():
    # 稍等 2.5 秒，让主服务先启动完毕并打开浏览器，不占用首屏加载时间
    time.sleep(2.5)
    clean_orphaned_images()
    recalibrate_usage_counts()
    try:
        backup_path = create_full_backup_if_due()
        if backup_path:
            print(f"[Backup] 已创建并验证每日完整备份: {backup_path.name}")
    except Exception as exc:
        print(f"[Backup Error] 每日完整备份失败: {type(exc).__name__}: {exc}")

# 仅在非测试环境下启动静默自愈清理后台守护线程
if not IS_TESTING:
    threading.Thread(target=start_startup_cleanup, daemon=True).start()


# Ensure directories exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
TMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "tmp")
os.makedirs(TMP_UPLOAD_DIR, exist_ok=True)

# Bounded document task manager shared by PDF and Word imports.
DOCUMENT_TASKS = TaskManager(
    max_workers=2,
    max_queue=4,
    terminal_ttl_seconds=3600,
    temp_asset_cleanup=lambda paths: _delete_task_temp_assets(paths),
)
PDF_OCR_SEMAPHORE = threading.BoundedSemaphore(4)
MAX_PDF_TASK_PAGES = 80

def get_seq_mapping(db: Session, question_ids=None):
    """Map physical ID order to the user-facing **per-subject** sequence number.

    编号按科目各自从 1 起：数学 #1..#N、物理 #1..#M、化学 #1..#K —— 跨科目不连续。
    （2026-09-19 需求：混科题库里物理第一题就该是 #1，而不是接着数学的号往下排。）

    这是纯展示口径、不落库：库里保留主键 ``id`` 作为唯一标识（错题图片目录、试卷归档、
    关联题组都认 id），编号只表示「该科第几道题」，所以删题后该科后续编号会前移 ——
    与改造前的全局排名口径性质一致，不需要任何数据迁移。

    当 ``question_ids`` 较小时，用相关子查询 ``COUNT(*) WHERE id <= q.id AND 同科目``
    替代全表 ``ROW_NUMBER() OVER`` 再过滤，避免对全表做窗口函数扫描。
    """

    from sqlalchemy import case, func, select

    question_table = Question.__table__
    # 科目口径与 curriculums.normalize_subject 完全一致：空值 / 未知值一律并入数学。
    # 否则同一条题在题库 Tab 里算数学、在这里却自成「一门课」，编号会凭空从 1 重来。
    _raw_subject = func.lower(func.trim(func.coalesce(question_table.c.subject, "")))
    subject_key = case(
        (_raw_subject.in_(list(SUBJECT_ORDER)), _raw_subject),
        else_=SUBJECT_ORDER[0],
    )

    if question_ids is None:
        rows = (
            db.query(Question.id, subject_key.label("subject_key"))
            .order_by(subject_key.asc(), Question.id.asc())
            .all()
        )
        counters: dict[str, int] = {}
        mapping: dict[int, int] = {}
        for q_id, key in rows:
            name = str(key)
            counters[name] = counters.get(name, 0) + 1
            mapping[int(q_id)] = counters[name]
        return mapping

    normalized_ids = {int(question_id) for question_id in question_ids}
    if not normalized_ids:
        return {}

    # 单个 id 的 seq = 同科目内 id <= 它的题目数（相关子查询一次性算完）。
    # 注意：这里依赖 Question.id 是升序主键；若主键不连续，seq 仍是科目内的排序位置。
    filtered = select(
        question_table.c.id,
        subject_key.label("subject_key"),
    ).where(question_table.c.id.in_(normalized_ids)).alias("filtered")
    seq_expr = select(func.count()).where(
        question_table.c.id <= filtered.c.id,
        subject_key == filtered.c.subject_key,
    ).correlate(filtered).scalar_subquery()
    stmt = select(filtered.c.id, seq_expr.label("seq_num"))
    rows = db.execute(stmt).fetchall()
    return {int(question_id): int(seq_num) for question_id, seq_num in rows}

# ----------------- Static Files & Index -----------------


@app.get("/healthz", include_in_schema=False)
def healthz():
    report = readiness_report(engine)
    return JSONResponse(report, status_code=200 if report["ready"] else 503)

@app.get("/")
def read_index():
    index_path = str(STATIC_DIR / "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # Inject dynamic cache-busting version parameter based on file mtime
        # 新增 JS 文件时必须同步登记，否则它的 ?v= 永远停在源码里的占位值，
        # 浏览器会一直用缓存，后续修复推不到用户手里（有测试钉住这一点）。
        js_files = ["math-render.js", "api.js", "editor.js", "ocr.js", "import.js", "paper.js", "mistake.js", "onboarding.js", "backup.js"]
        for js in js_files:
            js_path = str(STATIC_JS_DIR / js)
            mtime = int(os.path.getmtime(js_path)) if os.path.exists(js_path) else 0
            # Replace whatever placeholder version the tag carries (v=1.0.0 / v=1.0.1 / v=1.0.2 ...).
            # 原先写死匹配 "?v=1.0.1"，导致写成其它版本号的标签（api.js / onboarding.js）
            # 永远拿不到 mtime，改了 JS 浏览器仍在用缓存。
            html_content = re.sub(
                r"/static/js/" + re.escape(js) + r"\?v=[^\"']*",
                f"/static/js/{js}?v={mtime}",
                html_content,
            )
            # Also handle plain scripts references if they exist
            html_content = html_content.replace(f'src="/static/js/{js}"', f'src="/static/js/{js}?v={mtime}"')
            
        # Inject dynamic cache-busting version parameter for app.css and favicon assets
        css_path = str(STATIC_CSS_DIR / "app.css")
        css_mtime = int(os.path.getmtime(css_path)) if os.path.exists(css_path) else 0
        html_content = html_content.replace('/static/css/app.css', f'/static/css/app.css?v={css_mtime}')

        fav_path = str(STATIC_DIR / "favicon.png")
        fav_mtime = int(os.path.getmtime(fav_path)) if os.path.exists(fav_path) else 0
        html_content = html_content.replace('/static/favicon.png', f'/static/favicon.png?v={fav_mtime}')
            
        # Inject the token and server_instance_id directly into index.html to bypass any cookie blocking policies
        token_script = f'<script>window.__localToken = "{LOCAL_TOKEN}"; window.__serverInstanceId = "{SERVER_INSTANCE_ID}";</script>'
        html_content = html_content.replace('<head>', f'<head>\n    {token_script}')
            
        res = HTMLResponse(content=html_content)
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        
        res.set_cookie(
            key="local_token",
            value=LOCAL_TOKEN,
            httponly=False,  # JavaScript must be able to read this cookie to send it back via headers
            samesite="lax",
            secure=False
        )
        return res
    return JSONResponse(
        content={"status": "error", "message": "static/index.html not found. Please create it."},
        status_code=404
    )

@app.get("/favicon.ico", include_in_schema=False)
def read_favicon():
    favicon_path = str(STATIC_DIR / "favicon.ico")
    if os.path.exists(favicon_path):
        res = FileResponse(favicon_path, media_type="image/x-icon")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    favicon_png_path = str(STATIC_DIR / "favicon.png")
    if os.path.exists(favicon_png_path):
        res = FileResponse(favicon_png_path, media_type="image/png")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon not found."},
        status_code=404
    )

@app.get("/favicon.svg", include_in_schema=False)
def read_favicon_svg():
    svg_path = str(STATIC_DIR / "favicon.svg")
    if os.path.exists(svg_path):
        res = FileResponse(svg_path, media_type="image/svg+xml")
        res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        res.headers["Pragma"] = "no-cache"
        res.headers["Expires"] = "0"
        return res
    return JSONResponse(
        content={"status": "error", "message": "favicon.svg not found."},
        status_code=404
    )

@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def read_apple_touch_icon():
    for name in ["apple-touch-icon.png", "favicon.png"]:
        icon_path = str(STATIC_DIR / name)
        if os.path.exists(icon_path):
            res = FileResponse(icon_path, media_type="image/png")
            res.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            res.headers["Pragma"] = "no-cache"
            res.headers["Expires"] = "0"
            return res
    return JSONResponse(
        content={"status": "error", "message": "apple touch icon not found."},
        status_code=404
    )

# ----------------- Upload API -----------------

@app.post("/api/upload")
def upload_image(file: UploadFile = File(...)):
    try:
        raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
        normalized = normalize_raster_image(raw)

        # Never trust the client suffix.  The server-generated extension and
        # re-encoded bytes prevent HTML/SVG/polyglot files being served same-origin.
        filename = f"{uuid.uuid4().hex}{normalized.extension}"
        filepath = os.path.join(UPLOAD_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(normalized.data)

        relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
        return {
            "status": "success",
            "file_path": relative_path,
            "filename": file.filename
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "图片过大，请上传 10MB 以内的文件。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"图片上传失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        print(f"[Upload Error] {type(e).__name__}: {e}")
        return JSONResponse(
            content={"status": "error", "message": "文件上传失败，请检查文件后重试。"},
            status_code=500
        )

# ----------------- OCR API -----------------

def auto_crop_image(image):
    try:
        from PIL import ImageOps, ImageStat
        # 估算灰度均值，判断主色调（暗色背景还是亮色背景）
        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        mean_val = stat.mean[0]
        
        if mean_val < 100:  # 偏暗，可能含有大面积黑边背景
            bbox = image.getbbox()
            if bbox:
                # 留出 8 像素的边距以防文字贴边影响识别
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
        elif mean_val > 220:  # 偏亮，可能含有大面积白边背景
            inverted = ImageOps.invert(image.convert("RGB"))
            bbox = inverted.getbbox()
            if bbox:
                w, h = image.size
                left = max(0, bbox[0] - 8)
                upper = max(0, bbox[1] - 8)
                right = min(w, bbox[2] + 8)
                lower = min(h, bbox[3] + 8)
                return image.crop((left, upper, right, lower))
    except Exception as e:
        print(f"[Auto Crop] 裁剪失败，返回原图. Error: {str(e)}")
    return image


def ocr_via_provider(
    image_path: str,
    provider: MultimodalProviderConfig,
    include_illustration_box: bool = False,
) -> str:
    """Use one resolved multimodal provider for formula and text OCR."""
    import base64

    print(
        f"[OCR Flow] 正在向 {provider.provider_label} 提交多模态识别任务: "
        f"{image_path} (模型: {provider.model_name})..."
    )
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对图片进行 Base64 编码失败: {str(e)}")

    prompt = COMMON_OCR_PROMPT
    if include_illustration_box:
        prompt += ILLUSTRATION_BOX_PROMPT

    payload = {
        "model": provider.model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded_string}"
                        }
                    }
                ]
            }
        ],
        "stream": False
    }

    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="ocr",
    )

    timeout = 240
    # Chat-completion POSTs are not idempotent: a read timeout can happen after
    # the provider has accepted (and billed) the request.  Do not automatically
    # send the same image two or three times.  The shared transport still
    # retries a connection-establishment failure where no response was read.
    response = post_chat_completion(
        provider,
        payload,
        timeout=timeout,
        check_status=False,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} API 识别失败: HTTP {response.status_code}"
        )

    res_json = response.json()
    try:
        choices = res_json.get("choices", [])
        if choices and len(choices) > 0:
            content = choices[0].get("message", {}).get("content", "")
            return content.strip()
        else:
            raise RuntimeError(
                f"{provider.provider_label} 返回的数据中未包含 Choices 结果。"
            )
    except Exception as e:
        raise RuntimeError(
            f"解析 {provider.provider_label} 响应数据失败: {str(e)}"
        )


def draw_tikz_via_high_model(image_path: str, prefer_draw: str, latex_content: str = None) -> str:
    """使用指定的高级绘图模型（多模态或纯文本自适应）生成 TikZ 代码"""
    import base64
    import re

    provider = resolve_draw_provider(prefer_draw)
    if not provider.api_key:
        print(
            f"[High Model Draw] 未配置 {provider.credential_label}，降级跳过。"
        )
        return None

    model_name = provider.model_name
    is_multimodal = provider.supports_image_input

    if is_multimodal:
        # 多模态图文输入模式
        try:
            with open(image_path, "rb") as f:
                encoded_image = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            print(f"[High Model Draw] 读取裁剪小图 Base64 失败: {str(e)}")
            return None
            
        prompt = build_tikz_draw_prompt(latex_content, multimodal=True)
        
        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_image}"
                }
            }
        ]
    else:
        # 纯文本推理模式 (如 Qwen3.5-397B)，我们把题目题干文字作为逻辑来源
        if not latex_content:
            print("[High Model Draw] 纯文本高级绘图模型未获得输入文本，跳过。")
            return None
            
        prompt = build_tikz_draw_prompt(latex_content, multimodal=False)
        content_payload = prompt

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": content_payload
            }
        ],
        "stream": False
    }
    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="draw",
    )

    try:
        response = post_chat_completion(
            provider,
            payload,
            timeout=120,
            check_status=False,
        )
        if response.status_code == 200:
            res_json = response.json()
            choices = res_json.get("choices", [])
            if choices:
                ai_message = choices[0].get("message", {}).get("content", "")
                match = re.search(r"\\begin{tikzpicture}.*?\\end{tikzpicture}", ai_message, re.DOTALL | re.IGNORECASE)
                if match:
                    return match.group(0)
                match_block = re.search(r"```(?:latex)?(.*?)```", ai_message, re.DOTALL | re.IGNORECASE)
                if match_block:
                    code = match_block.group(1).strip()
                    if "tikzpicture" not in code:
                        code = f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"
                    return code
                return ai_message.strip()
        else:
            print(f"[High Model Draw Error] 接口返回 HTTP {response.status_code}")
    except Exception as e:
        print(f"[High Model Draw Error] 大模型请求发生异常: {str(e)}")
    return None


@app.post("/api/ocr")
def ocr_formula(
    file: UploadFile = File(...),
    engine: str = Form(None),
    skip_tikz: bool = Form(False)
):
    import re
    temp_filepath = None
    try:
        # OCR route is synchronous and runs in FastAPI's worker pool.  Stream
        # only up to the endpoint cap, then fully decode/re-encode the image.
        file_bytes = read_stream_limited(file.file, MAX_OCR_IMAGE_BYTES)
        normalized = normalize_raster_image(file_bytes)
        image = Image.open(io.BytesIO(normalized.data)).convert("RGB")
        
        # 1. 运行自适应图像去噪/自动切边预处理
        image = auto_crop_image(image)
        
        # 将裁剪后的图片保存为持久化 OCR 文件，未来可作为题目配图
        filename = f"ocr_original_{uuid.uuid4().hex[:12]}.png"
        temp_filepath = os.path.join(UPLOAD_DIR, filename)
        image.save(temp_filepath, format="PNG")
        
        # 确定调用的具体引擎。
        # 临时传参 engine 取值: default, siliconflow, simpletex, ali_bailian
        if not engine or engine == "default":
            engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
            
        print(f"[OCR Flow] 当前决策分配识图引擎: {engine}")
        
        latex_content = None
        confidence = 0.95
        provider = ""

        known_ocr_engines = {
            "siliconflow",
            "ali_bailian",
            "bailian",
            "zhongzhan",
            "zhongzhan_gpt",
            "zhongzhan_claude",
        }
        if engine in known_ocr_engines:
            ocr_provider = resolve_ocr_provider(engine)
            if ocr_provider.api_key and ocr_provider.api_key.strip():
                try:
                    latex_content = ocr_via_provider(
                        temp_filepath,
                        ocr_provider,
                        include_illustration_box=False,
                    )
                    confidence = 0.99
                    provider = (
                        f"{ocr_provider.provider_label} "
                        f"({ocr_provider.model_name})"
                    )
                except Exception as e:
                    print(
                        f"[{ocr_provider.provider_label} 识别失败] "
                        f"发生异常: {str(e)}"
                    )
            else:
                print(
                    f"[OCR Flow Warning] 未配置 {ocr_provider.credential_label}，"
                    "当前识图引擎无法启动！"
                )

        if not latex_content:
            raise RuntimeError("当前分配的识图引擎均无法启动或识别失败。请检查右上角「API设置」中是否正确配置了 硅基流动(SiliconFlow) 或是 阿里百炼(Alibaba Bailian) 的 API Key。")

        # 成功，返回且进一步清洗
        if latex_content:
            # 过滤干扰字符
            latex_content = latex_content.replace("\\,", "").replace("\\!", "")
            # 自动清洗规范化下划线/连续划线/任何 \underline 变体为标准的 \fillin 宏
            latex_content = normalize_fillin_macro(latex_content)
            # 选择题选项统一为 choices 网格环境（单题 OCR 亦适用，避免内联选项进入编辑器）
            latex_content = normalize_choice_options_to_latex(latex_content)

        # ----------------- OCR 插图标记清洗（自动 TikZ 重画已按产品设计关闭） -----------------
        # 前端 TikZ 绘图入口已隐藏，OCR 不再请求模型输出 ILLUSTRATION_BOX 标记
        # （include_illustration_box=False）。此处仅做兜底清洗，擦除任何可能由
        # 模型幻觉产生的残留标记，避免乱入题干文本。后端 /api/render_tikz、
        # /api/ai/draw_tikz_from_image 等接口保留不动，便于日后恢复前端入口。
        tikz_code_from_high_model = None
        tikz_image_path = None

        if latex_content:
            import re
            latex_content = re.sub(r"\[ILLUSTRATION_BOX:.*?\]", "", latex_content).strip()
            # 同一口径：OCR 结果里若混进模型自绘的 TikZ，换算成占位符。解析截图识别
            # 走的正是这条 `/api/ocr`，不在这拦一道，那串代码就会原样落进审校页的解析框。
            latex_content = replace_drawn_figures_with_placeholders(latex_content)

        # 将 temp_filepath 置为 None，避免在 finally 块中被删除
        saved_filepath = temp_filepath
        temp_filepath = None

        return {
            "status": "success",
            "latex": latex_content,
            "confidence": confidence,
            "provider": provider,
            "image_path": f"/{UPLOAD_DIR_REL}/{os.path.basename(saved_filepath)}",
            "tikz_code": tikz_code_from_high_model,
            "tikz_image_path": tikz_image_path
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "公式识图失败: 图片不能超过 10MB。"},
            status_code=413,
        )
    except InvalidImageError as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"公式识图失败: {str(e)}"},
            status_code=500
        )
    finally:
        # 确保清理临时文件
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except Exception as e_cleanup:
                print(f"[OCR Flow Cleanup Error] 无法删除临时文件 {temp_filepath}: {str(e_cleanup)}")

# ----------------- DeepSeek AI Solve API -----------------

@app.post("/api/ai/solve")
def ai_solve(
    content: str = Form(...),
    question_type: str = Form("detailed_answer"),
    ocr_result: str = Form(""),
    custom_prompt: str = Form(""),
    thinking: str = Form("enabled"),
    model: str = Form("deepseek-v4-pro"),
    stream: str = Form("false")
):
    provider = resolve_text_provider(model)
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法智能解答！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        system_instructions, user_prompt = build_ai_solve_prompts(
            question_type=question_type,
            content=content,
            ocr_result=ocr_result,
            custom_prompt=custom_prompt,
        )

        # Keep the legacy fallback cap for older Bailian models. Current
        # Qwen3.7/3.8 requests are converted below to max_completion_tokens.
        max_output_tokens = 8192 if provider.provider_code == "bailian" else 16384
            
        explicit_effort = provider.reasoning_effort

        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_output_tokens
        }
        
        # Configure thinking parameter if specified (only for DeepSeek models/endpoints, excluding legacy models that don't support it)
        is_deepseek = ("deepseek" in model_name.lower() or "deepseek" in api_base.lower()) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
        is_siliconflow = api_base and "siliconflow" in api_base.lower()
        
        is_bailian = provider.provider_code == "bailian"
        if is_bailian:
            # Connect the front-end '深度思考' toggle button to Alibaba Bailian's 'enable_thinking' API parameter
            if thinking == "enabled":
                data["enable_thinking"] = True
            else:
                data["enable_thinking"] = False

        if is_siliconflow:
            # Native R1 models on SiliconFlow do not use enable_thinking (they are always reasoning)
            # Other models (V3, V4 Pro, Flash, etc.) use enable_thinking and reasoning_effort
            if "r1" not in model_name.lower():
                is_deepseek = False  # Bypass OpenAI standard thinking parameter
                if thinking == "enabled":
                    data["enable_thinking"] = True
                    if "v4" in model_name.lower():
                        data["reasoning_effort"] = "max"
                else:
                    data["enable_thinking"] = False

        # Support OpenAI reasoning models (gpt-5, o1, o3, etc.) on transit APIs
        is_openai_reasoning = ("gpt-5" in model_name.lower() or "o1" in model_name.lower() or "o3" in model_name.lower())
        if is_openai_reasoning:
            is_deepseek = False  # Bypass DeepSeek thinking parameter
            if thinking == "enabled":
                data["reasoning_effort"] = "high"    # Maximum mathematical depth and verification
            else:
                data["reasoning_effort"] = "medium"  # Balanced speed and analytical quality
        
        if is_deepseek and thinking in ["enabled", "disabled"]:
            data["thinking"] = {"type": thinking}
            
        # The 7:3 model selector may provide an explicit allowlisted effort.
        # Apply it last so it intentionally overrides the generic toggle.
        data = inject_reasoning_effort(data, explicit_effort)
        data = apply_bailian_thinking_policy(
            data,
            provider_code=provider.provider_code,
            model_name=model_name,
            task="solve",
            thinking_enabled=thinking == "enabled",
        )
            
        # When thinking mode is active, temperature is ignored/deprecated by DeepSeek.
        # But when thinking is disabled or non-DeepSeek model, specify it.
        if not is_deepseek or thinking == "disabled":
            data["temperature"] = 0.2
            
        if stream == "true":
            def event_generator():
                data["stream"] = True
                try:
                    response = post_chat_completion(
                        provider,
                        data,
                        timeout=300,
                        stream=True,
                        check_status=False,
                    )
                    if response.status_code != 200:
                        error_msg = f"{provider_name} 接口错误: HTTP {response.status_code}"
                        yield f"data: {json.dumps({'status': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
                        return
                    
                    reasoning_count = 0
                    content_count = 0
                    
                    for line in response.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8").strip()
                        if line_str.startswith("data:"):
                            data_content = line_str[5:].strip()
                            if data_content == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_content)
                                
                                # 优先读取接口可能返回的官方 usage 统计
                                usage = chunk_json.get("usage")
                                if usage and isinstance(usage, dict):
                                    c_tok = usage.get("completion_tokens")
                                    r_tok = usage.get("completion_tokens_details", {}).get("reasoning_tokens") if isinstance(usage.get("completion_tokens_details"), dict) else None
                                    if c_tok is not None:
                                        content_count = max(content_count, c_tok)
                                    if r_tok is not None:
                                        reasoning_count = max(reasoning_count, r_tok)

                                chunk_choices = chunk_json.get("choices")
                                if not chunk_choices:
                                    continue
                                delta = chunk_choices[0].get("delta", {})
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                content_piece = delta.get("content") or ""
                                
                                # 针对不同模型的流式数据块进行动态 Token 数量估算（兼容大 Chunk 输出模型如 Gemini Flash）
                                if reasoning:
                                    cjk_c = sum(1 for c in reasoning if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(reasoning) - cjk_c
                                    reasoning_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                if content_piece:
                                    cjk_c = sum(1 for c in content_piece if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
                                    oth_c = len(content_piece) - cjk_c
                                    content_count += max(1, int(cjk_c * 1.2 + oth_c / 4.0 + 0.99))
                                    
                                if reasoning or content_piece:
                                    yield f"data: {json.dumps({'status': 'processing', 'reasoning': reasoning, 'content': content_piece, 'reasoning_count': reasoning_count, 'content_count': content_count}, ensure_ascii=False)}\n\n"
                            except Exception:
                                continue
                    yield f"data: {json.dumps({'status': 'done'}, ensure_ascii=False)}\n\n"
                except requests.exceptions.Timeout:
                    friendly_msg = (
                        f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
                        f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
                    )
                    yield f"data: {json.dumps({'status': 'error', 'message': friendly_msg}, ensure_ascii=False)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'AI 解析生成出错: {str(e)}'}, ensure_ascii=False)}\n\n"
            
            return StreamingResponse(event_generator(), media_type="text/event-stream")

        # Generous 300 seconds timeout (5 minutes) for high-school math reasoning and network proxies
        response = post_chat_completion(
            provider,
            data,
            timeout=300,
            provider_name=provider_name,
        )
            
        res_json = response.json()
        
        msg_obj = res_json.get("choices", [{}])[0].get("message", {})
        ai_message = msg_obj.get("content") or ""
        reasoning_content = msg_obj.get("reasoning_content") or ""
        
        # Robust fallback: if content is empty but reasoning is present, use reasoning as explanation
        if not ai_message and reasoning_content:
            ai_message = f"【深度思考推理过程】\n{reasoning_content}\n\n【参考解析】已成功生成推理步骤。如果需要标准的三板块排版，请尝试在控制面板中关闭「AI 深度思考推理」再次生成。"
            
        if not ai_message:
            print(
                f"[Solve API] Provider returned an empty message "
                f"(provider={provider.provider_code}, status={response.status_code})."
            )
            raise Exception(f"{provider_name} 返回了空消息，请检查 API 或账户余额。")
            
        return {
            "status": "success",
            "solution": ai_message
        }
    except requests.exceptions.Timeout:
        friendly_msg = (
            f"AI 解析生成超时（限制为 300 秒）。这通常是因为 {provider_name} "
            f"服务端当前排队拥堵或推理速度过慢。建议您稍后再试，或在设置中切换为「DeepSeek 官方」或「阿里百炼」等更稳定的接口平台。"
        )
        return JSONResponse(
            content={"status": "error", "message": friendly_msg},
            status_code=500
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 解析生成出错: {str(e)}"},
            status_code=500
        )

# ----------------- Save ENV Settings from UI -----------------

@app.get("/api/settings")
def get_settings():
    ds_key = os.getenv("DEEPSEEK_API_KEY", "")
    sf_key = os.getenv("SILICONFLOW_API_KEY", "")
    ali_key = os.getenv("ALI_BAILIAN_API_KEY", "")
    
    # 兼容老版 ZHONGZHAN 环境变量
    zz_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
    zz_gpt_base = os.getenv("ZHONGZHAN_GPT_BASE_URL") or os.getenv("ZHONGZHAN_BASE_URL", "")
    zz_gpt_ocr_model = os.getenv("ZHONGZHAN_GPT_OCR_MODEL") or os.getenv("ZHONGZHAN_OCR_MODEL", "gpt-4o")
    
    zz_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")
    zz_claude_base = os.getenv("ZHONGZHAN_CLAUDE_BASE_URL", "")
    zz_claude_ocr_model = os.getenv("ZHONGZHAN_CLAUDE_OCR_MODEL", "claude-3-5-sonnet")
    
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    sf_model = os.getenv("SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
    ali_model = os.getenv("ALI_BAILIAN_OCR_MODEL", "qwen3.7-flash")
    prefer_solve_model = os.getenv("PREFER_SOLVE_MODEL", "deepseek-v4-pro")
    prefer_parse_model = os.getenv("PREFER_PARSE_MODEL", "deepseek-flash")
    prefer_classify_model = os.getenv("PREFER_CLASSIFY_MODEL") or os.getenv("DEEPSEEK_CLASSIFY_MODEL", "deepseek-flash")
    prefer_draw_model = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    prefer_free_parse_model = os.getenv("PREFER_FREE_PARSE_MODEL", "")
    prefer_free_classify_model = os.getenv("PREFER_FREE_CLASSIFY_MODEL", "")
    prefer_free_solve_model = os.getenv("PREFER_FREE_SOLVE_MODEL", "")
    prefer_free_eval_model = os.getenv("PREFER_FREE_EVAL_MODEL", "")

    masked_ds = ""
    if ds_key:
        masked_ds = ds_key[:4] + "••••" + ds_key[-4:] if len(ds_key) > 8 else "••••••••"
        
    masked_sf = ""
    if sf_key:
        masked_sf = sf_key[:4] + "••••" + sf_key[-4:] if len(sf_key) > 8 else "••••••••"
        
    masked_ali = ""
    if ali_key:
        masked_ali = ali_key[:4] + "••••" + ali_key[-4:] if len(ali_key) > 8 else "••••••••"

    masked_zz_gpt = ""
    if zz_gpt_key:
        masked_zz_gpt = zz_gpt_key[:4] + "••••" + zz_gpt_key[-4:] if len(zz_gpt_key) > 8 else "••••••••"
        
    masked_zz_claude = ""
    if zz_claude_key:
        masked_zz_claude = zz_claude_key[:4] + "••••" + zz_claude_key[-4:] if len(zz_claude_key) > 8 else "••••••••"
        
    return {
        "deepseek_key": masked_ds,
        "siliconflow_key": masked_sf,
        "ali_bailian_key": masked_ali,
        "zhongzhan_gpt_key": masked_zz_gpt,
        "zhongzhan_gpt_base_url": zz_gpt_base,
        "zhongzhan_gpt_ocr_model": zz_gpt_ocr_model,
        "zhongzhan_claude_key": masked_zz_claude,
        "zhongzhan_claude_base_url": zz_claude_base,
        "zhongzhan_claude_ocr_model": zz_claude_ocr_model,
        "prefer_engine": prefer_engine,
        "siliconflow_model": sf_model,
        "ali_bailian_model": ali_model,
        "prefer_solve_model": prefer_solve_model,
        "prefer_parse_model": prefer_parse_model,
        "prefer_classify_model": prefer_classify_model,
        "prefer_draw_model": prefer_draw_model,
        "prefer_free_parse_model": prefer_free_parse_model,
        "prefer_free_classify_model": prefer_free_classify_model,
        "prefer_free_solve_model": prefer_free_solve_model,
        "prefer_free_eval_model": prefer_free_eval_model
    }

@app.post("/api/settings/save")
def save_settings(
    deepseek_key: str = Form(""),
    siliconflow_key: str = Form(""),
    ali_bailian_key: str = Form(""),
    zhongzhan_gpt_key: str = Form(""),
    zhongzhan_gpt_base_url: str = Form(""),
    zhongzhan_gpt_ocr_model: str = Form(""),
    zhongzhan_claude_key: str = Form(""),
    zhongzhan_claude_base_url: str = Form(""),
    zhongzhan_claude_ocr_model: str = Form(""),
    prefer_engine: str = Form("siliconflow"),
    siliconflow_model: str = Form("Qwen/Qwen3-VL-8B-Instruct"),
    ali_bailian_model: str = Form("qwen3.7-flash"),
    prefer_solve_model: str = Form("deepseek-v4-pro"),
    prefer_parse_model: str = Form("deepseek-flash"),
    prefer_classify_model: str = Form("deepseek-flash"),
    prefer_draw_model: str = Form("Qwen/Qwen3-VL-32B-Instruct"),
    prefer_free_parse_model: str = Form(""),
    prefer_free_classify_model: str = Form(""),
    prefer_free_solve_model: str = Form(""),
    prefer_free_eval_model: str = Form("")
):
    try:
        settings_values = {
            "deepseek_key": deepseek_key,
            "siliconflow_key": siliconflow_key,
            "ali_bailian_key": ali_bailian_key,
            "zhongzhan_gpt_key": zhongzhan_gpt_key,
            "zhongzhan_gpt_base_url": zhongzhan_gpt_base_url,
            "zhongzhan_gpt_ocr_model": zhongzhan_gpt_ocr_model,
            "zhongzhan_claude_key": zhongzhan_claude_key,
            "zhongzhan_claude_base_url": zhongzhan_claude_base_url,
            "zhongzhan_claude_ocr_model": zhongzhan_claude_ocr_model,
            "prefer_engine": prefer_engine,
            "siliconflow_model": siliconflow_model,
            "ali_bailian_model": ali_bailian_model,
            "prefer_solve_model": prefer_solve_model,
            "prefer_parse_model": prefer_parse_model,
            "prefer_classify_model": prefer_classify_model,
            "prefer_draw_model": prefer_draw_model,
            "prefer_free_parse_model": prefer_free_parse_model,
            "prefer_free_classify_model": prefer_free_classify_model,
            "prefer_free_solve_model": prefer_free_solve_model,
            "prefer_free_eval_model": prefer_free_eval_model,
        }
        if any("\r" in value or "\n" in value for value in settings_values.values()):
            raise ValueError("配置值不能包含换行符。")

        # If masked, preserve current key
        if "••••" in deepseek_key:
            deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if "••••" in siliconflow_key:
            siliconflow_key = os.getenv("SILICONFLOW_API_KEY", "")
        if "••••" in ali_bailian_key:
            ali_bailian_key = os.getenv("ALI_BAILIAN_API_KEY", "")
        if "••••" in zhongzhan_gpt_key:
            zhongzhan_gpt_key = os.getenv("ZHONGZHAN_GPT_API_KEY") or os.getenv("ZHONGZHAN_API_KEY", "")
        if "••••" in zhongzhan_claude_key:
            zhongzhan_claude_key = os.getenv("ZHONGZHAN_CLAUDE_API_KEY", "")
            
        # Read current .env
        env_lines = []
        if ENV_FILE.exists():
            with ENV_FILE.open("r", encoding="utf-8") as f:
                env_lines = f.readlines()
        
        keys_replaced = {
            "DEEPSEEK_API_KEY": False,
            "SILICONFLOW_API_KEY": False,
            "ALI_BAILIAN_API_KEY": False,
            "ZHONGZHAN_GPT_API_KEY": False,
            "ZHONGZHAN_GPT_BASE_URL": False,
            "ZHONGZHAN_GPT_OCR_MODEL": False,
            "ZHONGZHAN_CLAUDE_API_KEY": False,
            "ZHONGZHAN_CLAUDE_BASE_URL": False,
            "ZHONGZHAN_CLAUDE_OCR_MODEL": False,
            "OCR_PREFER_ENGINE": False,
            "SILICONFLOW_OCR_MODEL": False,
            "ALI_BAILIAN_OCR_MODEL": False,
            "PREFER_SOLVE_MODEL": False,
            "PREFER_PARSE_MODEL": False,
            "PREFER_CLASSIFY_MODEL": False,
            "PREFER_DRAW_MODEL": False,
            "PREFER_FREE_PARSE_MODEL": False,
            "PREFER_FREE_CLASSIFY_MODEL": False,
            "PREFER_FREE_SOLVE_MODEL": False,
            "PREFER_FREE_EVAL_MODEL": False
        }
        new_lines = []
        
        for line in env_lines:
            line_strip = line.strip()
            # Skip old Pix2Text settings to clean .env
            if line_strip.startswith("PIX2TEXT_API_KEY=") or line_strip.startswith("PIX2TEXT_SERVER_TYPE="):
                continue
                
            if line_strip.startswith("DEEPSEEK_API_KEY="):
                new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
                keys_replaced["DEEPSEEK_API_KEY"] = True
            elif line_strip.startswith("SILICONFLOW_API_KEY="):
                new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
                keys_replaced["SILICONFLOW_API_KEY"] = True
            elif line_strip.startswith("ALI_BAILIAN_API_KEY="):
                new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
                keys_replaced["ALI_BAILIAN_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_API_KEY="):
                new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
                keys_replaced["ZHONGZHAN_GPT_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
                keys_replaced["ZHONGZHAN_GPT_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_GPT_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
                keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_API_KEY="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_BASE_URL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"] = True
            elif line_strip.startswith("ZHONGZHAN_CLAUDE_OCR_MODEL="):
                new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
                keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"] = True
            elif line_strip.startswith("OCR_PREFER_ENGINE="):
                new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
                keys_replaced["OCR_PREFER_ENGINE"] = True
            elif line_strip.startswith("SILICONFLOW_OCR_MODEL="):
                new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
                keys_replaced["SILICONFLOW_OCR_MODEL"] = True
            elif line_strip.startswith("ALI_BAILIAN_OCR_MODEL="):
                new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
                keys_replaced["ALI_BAILIAN_OCR_MODEL"] = True
            elif line_strip.startswith("PREFER_SOLVE_MODEL="):
                new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
                keys_replaced["PREFER_SOLVE_MODEL"] = True
            elif line_strip.startswith("PREFER_PARSE_MODEL="):
                new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
                keys_replaced["PREFER_PARSE_MODEL"] = True
            elif line_strip.startswith("PREFER_CLASSIFY_MODEL=") or line_strip.startswith("DEEPSEEK_CLASSIFY_MODEL="):
                new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
                keys_replaced["PREFER_CLASSIFY_MODEL"] = True
            elif line_strip.startswith("PREFER_DRAW_MODEL="):
                new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
                keys_replaced["PREFER_DRAW_MODEL"] = True
            elif line_strip.startswith("PREFER_FREE_PARSE_MODEL="):
                new_lines.append(f"PREFER_FREE_PARSE_MODEL={prefer_free_parse_model}\n")
                keys_replaced["PREFER_FREE_PARSE_MODEL"] = True
            elif line_strip.startswith("PREFER_FREE_CLASSIFY_MODEL="):
                new_lines.append(f"PREFER_FREE_CLASSIFY_MODEL={prefer_free_classify_model}\n")
                keys_replaced["PREFER_FREE_CLASSIFY_MODEL"] = True
            elif line_strip.startswith("PREFER_FREE_SOLVE_MODEL="):
                new_lines.append(f"PREFER_FREE_SOLVE_MODEL={prefer_free_solve_model}\n")
                keys_replaced["PREFER_FREE_SOLVE_MODEL"] = True
            elif line_strip.startswith("PREFER_FREE_EVAL_MODEL="):
                new_lines.append(f"PREFER_FREE_EVAL_MODEL={prefer_free_eval_model}\n")
                keys_replaced["PREFER_FREE_EVAL_MODEL"] = True
            else:
                new_lines.append(line)
                
        # Append keys if not replaced
        if not keys_replaced["DEEPSEEK_API_KEY"]:
            new_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}\n")
        if not keys_replaced["SILICONFLOW_API_KEY"]:
            new_lines.append(f"SILICONFLOW_API_KEY={siliconflow_key}\n")
        if not keys_replaced["ALI_BAILIAN_API_KEY"]:
            new_lines.append(f"ALI_BAILIAN_API_KEY={ali_bailian_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_GPT_API_KEY={zhongzhan_gpt_key}\n")
        if not keys_replaced["ZHONGZHAN_GPT_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_GPT_BASE_URL={zhongzhan_gpt_base_url}\n")
        if not keys_replaced["ZHONGZHAN_GPT_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_GPT_OCR_MODEL={zhongzhan_gpt_ocr_model}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_API_KEY"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_API_KEY={zhongzhan_claude_key}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_BASE_URL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_BASE_URL={zhongzhan_claude_base_url}\n")
        if not keys_replaced["ZHONGZHAN_CLAUDE_OCR_MODEL"]:
            new_lines.append(f"ZHONGZHAN_CLAUDE_OCR_MODEL={zhongzhan_claude_ocr_model}\n")
        if not keys_replaced["OCR_PREFER_ENGINE"]:
            new_lines.append(f"OCR_PREFER_ENGINE={prefer_engine}\n")
        if not keys_replaced["SILICONFLOW_OCR_MODEL"]:
            new_lines.append(f"SILICONFLOW_OCR_MODEL={siliconflow_model}\n")
        if not keys_replaced["ALI_BAILIAN_OCR_MODEL"]:
            new_lines.append(f"ALI_BAILIAN_OCR_MODEL={ali_bailian_model}\n")
        if not keys_replaced["PREFER_SOLVE_MODEL"]:
            new_lines.append(f"PREFER_SOLVE_MODEL={prefer_solve_model}\n")
        if not keys_replaced["PREFER_PARSE_MODEL"]:
            new_lines.append(f"PREFER_PARSE_MODEL={prefer_parse_model}\n")
        if not keys_replaced["PREFER_CLASSIFY_MODEL"]:
            new_lines.append(f"PREFER_CLASSIFY_MODEL={prefer_classify_model}\n")
        if not keys_replaced["PREFER_DRAW_MODEL"]:
            new_lines.append(f"PREFER_DRAW_MODEL={prefer_draw_model}\n")
        if not keys_replaced["PREFER_FREE_PARSE_MODEL"]:
            new_lines.append(f"PREFER_FREE_PARSE_MODEL={prefer_free_parse_model}\n")
        if not keys_replaced["PREFER_FREE_CLASSIFY_MODEL"]:
            new_lines.append(f"PREFER_FREE_CLASSIFY_MODEL={prefer_free_classify_model}\n")
        if not keys_replaced["PREFER_FREE_SOLVE_MODEL"]:
            new_lines.append(f"PREFER_FREE_SOLVE_MODEL={prefer_free_solve_model}\n")
        if not keys_replaced["PREFER_FREE_EVAL_MODEL"]:
            new_lines.append(f"PREFER_FREE_EVAL_MODEL={prefer_free_eval_model}\n")
            
        write_private_text_atomic(ENV_FILE, "".join(new_lines))
            
        # Clean current process env
        os.environ.pop("PIX2TEXT_API_KEY", None)
        os.environ.pop("PIX2TEXT_SERVER_TYPE", None)
        
        # 仅当表单提交非空时才覆盖进程环境变量；
        # 空值（用户未填写）保留 shell / 启动时注入的既有键值，避免误清空。
        _env_updates = {
            "DEEPSEEK_API_KEY": deepseek_key,
            "SILICONFLOW_API_KEY": siliconflow_key,
            "ALI_BAILIAN_API_KEY": ali_bailian_key,
            "ZHONGZHAN_GPT_API_KEY": zhongzhan_gpt_key,
            "ZHONGZHAN_GPT_BASE_URL": zhongzhan_gpt_base_url,
            "ZHONGZHAN_GPT_OCR_MODEL": zhongzhan_gpt_ocr_model,
            "ZHONGZHAN_CLAUDE_API_KEY": zhongzhan_claude_key,
            "ZHONGZHAN_CLAUDE_BASE_URL": zhongzhan_claude_base_url,
            "ZHONGZHAN_CLAUDE_OCR_MODEL": zhongzhan_claude_ocr_model,
            "OCR_PREFER_ENGINE": prefer_engine,
            "SILICONFLOW_OCR_MODEL": siliconflow_model,
            "ALI_BAILIAN_OCR_MODEL": ali_bailian_model,
            "PREFER_SOLVE_MODEL": prefer_solve_model,
            "PREFER_PARSE_MODEL": prefer_parse_model,
            "PREFER_CLASSIFY_MODEL": prefer_classify_model,
            "PREFER_DRAW_MODEL": prefer_draw_model,
            "PREFER_FREE_PARSE_MODEL": prefer_free_parse_model,
            "PREFER_FREE_CLASSIFY_MODEL": prefer_free_classify_model,
            "PREFER_FREE_SOLVE_MODEL": prefer_free_solve_model,
            "PREFER_FREE_EVAL_MODEL": prefer_free_eval_model,
        }
        for _env_name, _env_val in _env_updates.items():
            if _env_val:
                os.environ[_env_name] = _env_val
        
        return {"status": "success", "message": "API 与首选大模型配置已成功保存并即时生效！"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"保存配置失败: {str(e)}"},
            status_code=500
        )

# ----------------- Version & Update Check API -----------------

def parse_version_tuple(v_str: str):
    """Parse version string like 'v2.0.1' or '2.0.1' into integer tuple for comparison."""
    if not v_str:
        return (0, 0, 0)
    cleaned = v_str.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in cleaned.split("."):
        try:
            parts.append(int(re.sub(r"\D", "", p) or "0"))
        except Exception:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def macos_asset_architecture(asset_name: str) -> str:
    """Return the chip an installer name targets: 'arm64', 'x86_64' or ''.

    Releases from 2.2.1 ship two macOS packages, so the download link must be
    chosen by chip.  '' means a single-package release (2.2.0 and earlier).
    """
    lowered = (asset_name or "").lower()
    if any(token in lowered for token in ("applesilicon", "apple-silicon", "arm64", "aarch64")):
        return "arm64"
    if any(token in lowered for token in ("intel", "x86_64", "amd64")):
        return "x86_64"
    return ""


def local_macos_architecture() -> str:
    """Return this machine's chip family, matching macos_asset_architecture()."""
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return "x86_64"


@app.get("/api/version")
def get_version_info():
    """Return local version info."""
    from mathbank import __version__, GITHUB_REPO
    is_git_repo = (PROJECT_ROOT / ".git").exists()
    return {
        "current_version": __version__,
        "repo": GITHUB_REPO,
        "is_git_repo": is_git_repo
    }

@app.get("/api/environment")
def get_environment_status():
    """外部依赖自检：LaTeX / LibreOffice / Pandoc / PDF Inspector。

    前端用它生成「缺什么、影响什么、怎么装」的引导面板，取代过去只把
    「未检测到 xelatex 编译器」抛给用户的做法。
    """
    latex_engine = ""
    if shutil.which("xelatex"):
        latex_engine = "xelatex"
    elif shutil.which("pdflatex"):
        latex_engine = "pdflatex"
    latex_path = shutil.which(latex_engine) if latex_engine else ""

    soffice_path = shutil.which("soffice") or shutil.which("libreoffice") or ""
    if not soffice_path:
        # macOS / Windows 默认安装位置的兜底探测（未加入 PATH 也能识别）
        for candidate in (
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ):
            if Path(candidate).exists():
                soffice_path = candidate
                break

    pandoc_path = os.getenv("MATHBANK_PANDOC_PATH", "").strip() or shutil.which("pandoc") or ""

    return {
        "status": "success",
        "platform": platform.system(),
        "latex": {"available": bool(latex_path), "path": latex_path, "engine": latex_engine},
        "libreoffice": {"available": bool(soffice_path), "path": soffice_path},
        "pandoc": {"available": bool(pandoc_path), "path": pandoc_path},
        "pdf_inspector": {"available": bool(is_pdf_inspector_available())},
    }


@app.post("/api/backup")
def create_backup_snapshot():
    """手动创建完整备份（升级向导调用；不含 .env 与密钥）。"""
    try:
        archive = create_full_backup()
    except Exception as exc:  # noqa: BLE001 - 备份失败必须回报原因而不是 500
        return {"status": "error", "message": f"备份失败：{exc}"}
    return {
        "status": "success",
        "file": archive.name,
        "dir": str(archive.parent),
        "message": f"已创建完整备份：{archive.name}",
    }


def _snapshot_database_summary(manifest: dict) -> dict:
    """从 manifest 里抽出界面要展示的那几个数，避免前端猜结构。"""

    database = manifest.get("database")
    if not isinstance(database, dict):
        database = {}
    row_counts = database.get("row_counts")
    if not isinstance(row_counts, dict):
        row_counts = {}
    return {
        "created_at": manifest.get("created_at"),
        "app_version": manifest.get("app_version"),
        "schema_version": database.get("schema_version"),
        "row_counts": row_counts,
        "upload_file_count": manifest.get("upload_file_count"),
        "metadata_included": bool(manifest.get("metadata_included")),
    }


@app.get("/api/backups")
def list_backup_snapshots():
    """列出可用快照、上次备份时间与待还原请求（设置 → 备份与还原）。"""

    try:
        snapshots = list_full_backups()
    except Exception as exc:  # noqa: BLE001 - 列表读不出来也要给界面一个可读原因
        return JSONResponse(
            content={
                "status": "error",
                "message": f"读取快照列表失败：{exc}",
                "snapshots": [],
            },
            status_code=500,
        )

    last_backup_at = None
    for snapshot in snapshots:
        manifest = snapshot.get("manifest") or {}
        if manifest.get("readable") and manifest.get("created_at"):
            last_backup_at = manifest["created_at"]
            break

    return {
        "status": "success",
        "dir": str(FULL_BACKUP_DIR),
        "retention": DEFAULT_RETENTION,
        "last_backup_at": last_backup_at,
        "snapshots": snapshots,
        "pending_restore": read_pending_restore(),
        "restore_ttl_seconds": PENDING_RESTORE_TTL_SECONDS,
    }


@app.post("/api/backup/verify")
def verify_backup_snapshot(payload: dict):
    """完整校验一个快照（哈希 + SQLite 完整性 + 图片引用），不做任何写入。"""

    data = payload if isinstance(payload, dict) else {}
    try:
        archive = resolve_full_backup(str(data.get("file", "")).strip())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=400
        )

    try:
        manifest = verify_full_backup(archive)
    except Exception as exc:  # noqa: BLE001 - 校验失败是预期结果，不是 500
        return JSONResponse(
            content={
                "status": "error",
                "file": archive.name,
                "message": f"快照校验未通过：{exc}",
            },
            status_code=422,
        )

    return {
        "status": "success",
        "file": archive.name,
        "size_bytes": archive.stat().st_size,
        "message": "快照完整性与数据库校验通过",
        **_snapshot_database_summary(manifest),
    }


@app.post("/api/backup/restore")
def request_backup_restore(payload: dict):
    """登记「延迟还原」请求：先在线校验并备份当前库，真正的还原在下次启动时落地。

    为什么不在这里直接还原：服务持有 runtime lock，而还原需要同一把锁；同进程再开
    一个 fd 也会撞锁。拆锁就等于允许「一边写、一边换库」，所以宁可多一次重启。
    """

    data = payload if isinstance(payload, dict) else {}
    if data.get("confirm") is not True:
        return JSONResponse(
            content={"status": "error", "message": "缺少二次确认，已忽略该请求"},
            status_code=400,
        )

    try:
        archive = resolve_full_backup(str(data.get("file", "")).strip())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=400
        )

    try:
        verify_full_backup(archive)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={
                "status": "error",
                "message": f"快照校验未通过，未登记还原请求：{exc}",
            },
            status_code=422,
        )

    try:
        safety_backup = create_pre_restore_backup()
    except Exception as exc:  # noqa: BLE001 - 备份不出来就不允许进入还原
        return JSONResponse(
            content={
                "status": "error",
                "message": f"还原前的当前题库备份失败，已中止：{exc}",
            },
            status_code=500,
        )

    try:
        request = write_pending_restore(archive.name, safety_backup=safety_backup)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={"status": "error", "message": f"登记待还原请求失败：{exc}"},
            status_code=500,
        )

    minutes = max(1, PENDING_RESTORE_TTL_SECONDS // 60)
    return {
        "status": "success",
        "file": archive.name,
        "safety_backup": request["safety_backup"],
        "expires_at": request["expires_at"],
        "ttl_seconds": PENDING_RESTORE_TTL_SECONDS,
        "message": (
            f"已登记还原请求。请关闭题库并重新启动，{minutes} 分钟内启动即会自动完成还原。"
        ),
    }


@app.delete("/api/backup/restore")
def cancel_backup_restore():
    """撤销待还原请求：重启后不会再改动数据。"""

    if not clear_pending_restore(reason="用户取消"):
        return {"status": "success", "removed": False, "message": "当前没有待还原请求"}
    return {
        "status": "success",
        "removed": True,
        "message": "已取消待还原请求；下次重启不会再改动数据",
    }


@app.get("/api/sample-questions")
def get_sample_questions_info(db: Session = Depends(get_db)):
    """内置示例题库的可用性与导入状态（供首次启动引导使用）。"""
    marker = normalize_source(SAMPLE_QUESTIONS_SOURCE)
    imported = db.query(Question).filter(Question.source == marker).count()
    return {
        "status": "success",
        "available": sample_question_count(),
        "imported": imported,
        "source": marker,
    }


@app.post("/api/sample-questions/import")
def import_sample_questions_endpoint(
    force: str = Form("false"),
    db: Session = Depends(get_db),
):
    """载入内置示例题目（幂等；无需 API Key 即可体验组卷与导出）。"""
    try:
        result = import_sample_questions(
            db,
            get_active_version_code(),
            extra_content_normalizer=lambda text: _strip_leading_question_number(
                normalize_fillin_macro(text)
            ),
            force=str(force).lower() in ("true", "1", "yes"),
        )
    except Exception as exc:  # noqa: BLE001 - 文件缺失等异常需回传可读原因
        return {"status": "error", "message": f"示例题目载入失败：{exc}"}
    return result


@app.get("/api/version/check-update")
def check_version_update():
    """Check for latest release on GitHub."""
    from mathbank import __version__, GITHUB_REPO
    from mathbank.ai_http import robust_request_get

    is_git_repo = (PROJECT_ROOT / ".git").exists()
    current_ver = __version__

    result = {
        "status": "success",
        "current_version": current_ver,
        "latest_version": current_ver,
        "has_update": False,
        "release_title": "",
        "release_body": "",
        "release_url": f"https://github.com/{GITHUB_REPO}/releases/latest",
        "published_at": "",
        "assets": {},
        "is_git_repo": is_git_repo
    }

    # 防御：若 GITHUB_REPO 被设回 `localfork/...` 占位，则不向上游查询更新。
    # 防止 UI 推荐上游覆盖升级清空本地所有定制，并避免在 GitHub API 留下无效查询日志。
    if GITHUB_REPO.startswith("localfork/"):
        result["status"] = "info"
        result["message"] = "当前为本地定制派生，更新检查已禁用。"
        return result

    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MathBank-Question-Bank-App"
        }
        resp = robust_request_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            latest_tag = data.get("tag_name", "").strip()
            latest_ver = latest_tag.lstrip("vV")
            
            # Compare versions
            current_tuple = parse_version_tuple(current_ver)
            latest_tuple = parse_version_tuple(latest_ver)
            
            has_update = latest_tuple > current_tuple
            
            # 2.2.1 起 macOS 按芯片分包。这里必须显式选本机架构对应的包：
            # 若只按名字里的 "macOS" 匹配，后匹配的会覆盖先匹配的，会取错架构。
            host_arch = local_macos_architecture()
            assets_map = {}
            macos_candidates = []
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                # 校验和文件的名字里也含 "macOS"/"Windows"，必须先排除，
                # 否则一键升级会下载到 .sha256 而不是安装包。
                if not name or name.lower().endswith((".sha256", ".sha256sum", ".txt", ".json", ".sig")):
                    continue
                download_url = asset.get("browser_download_url", "")
                size_mb = round(asset.get("size", 0) / (1024 * 1024), 1)
                download_count = asset.get("download_count", 0)
                entry = {
                    "name": name,
                    "url": download_url,
                    "size_mb": size_mb,
                    "downloads": download_count,
                }
                lowered_name = name.lower()
                if "macos" in lowered_name or "mac" in lowered_name or "darwin" in lowered_name:
                    entry["arch"] = macos_asset_architecture(name)
                    macos_candidates.append(entry)
                elif "windows" in lowered_name or "win" in lowered_name:
                    assets_map.setdefault("Windows", entry)

            if macos_candidates:
                matched = next(
                    (item for item in macos_candidates if item["arch"] == host_arch),
                    macos_candidates[0],
                )
                matched["host_arch"] = host_arch
                assets_map["macOS"] = matched
            
            result.update({
                "latest_version": latest_tag,
                "has_update": has_update,
                "release_title": data.get("name", "") or latest_tag,
                "release_body": data.get("body", ""),
                "release_url": data.get("html_url", result["release_url"]),
                "published_at": data.get("published_at", ""),
                "assets": assets_map
            })
        else:
            result["status"] = "warning"
            result["message"] = f"GitHub API 返回状态码: {resp.status_code}"
    except Exception as e:
        result["status"] = "warning"
        result["message"] = f"检查更新超时或失败: {str(e)}"
        
    return result

# ----------------- TikZ Render & AI Correction API -----------------

def compile_tikz_to_png(tikz_code: str) -> str:
    """
    编译 TikZ 代码为 PNG 并存放在静态资源目录中。
    如果编译成功，返回相对路径（如 /static/uploads/tikz_xxx.png）。
    如果编译失败，抛出 Exception 详细说明原因。
    """
    import shutil
    import uuid
    import subprocess
    import os
    import platform

    # 1. 检查 xelatex
    # macOS 特有处理：如果系统是 macOS 且标准 MacTeX 路径存在，确保其在 PATH 中，防止 GUI/后台进程环境变量丢失
    if platform.system() == "Darwin":
        mactex_bin = "/Library/TeX/texbin"
        if os.path.exists(mactex_bin) and mactex_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.path.pathsep + mactex_bin
    elif platform.system() == "Windows":
        # Windows 上 TeX Live 默认装在 C:\texlive\<年>\bin\windows，安装器未必加入 PATH，自动探测并补齐
        texlive_root = r"C:\texlive"
        if os.path.isdir(texlive_root):
            for _year in sorted(os.listdir(texlive_root), reverse=True):
                _bin = os.path.join(texlive_root, _year, "bin", "windows")
                if os.path.isdir(_bin) and _bin not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = os.environ.get("PATH", "") + os.path.pathsep + _bin

    if not shutil.which("xelatex"):
        raise RuntimeError("系统未检测到 'xelatex' 编译器。请确保您的系统已安装 MacTeX/TeX Live 并将其加入 PATH。")

    # 2. 检查 PyMuPDF (fitz)
    try:
        import fitz
    except ImportError:
        raise RuntimeError("Python 环境中未安装 'pymupdf'，无法将 PDF 转换为图像，请运行 'pip install pymupdf' 安装。")

    # 3. 创建临时文件夹
    temp_dir = os.path.join(UPLOAD_DIR, ".tikz_temp")
    os.makedirs(temp_dir, exist_ok=True)

    unique_id = uuid.uuid4().hex
    tex_path = os.path.join(temp_dir, f"{unique_id}.tex")
    pdf_path = os.path.join(temp_dir, f"{unique_id}.pdf")
    png_path = os.path.join(temp_dir, f"{unique_id}.png")
    aux_path = os.path.join(temp_dir, f"{unique_id}.aux")
    log_path = os.path.join(temp_dir, f"{unique_id}.log")

    # 拼装完整的 TeX 模板
    tex_content = f"""\\documentclass[tikz, border=2mm]{{standalone}}
\\usepackage{{ctex}}
\\usepackage{{amsmath}}
\\usepackage{{amssymb}}
\\usepackage{{tikz}}
\\usepackage{{pgfplots}}
\\pgfplotsset{{compat=1.16}}
\\usetikzlibrary{{patterns}}
\\usetikzlibrary{{calc,positioning,intersections,arrows}}
\\usetikzlibrary{{shapes.geometric,through,decorations.pathmorphing,arrows.meta,quotes,mindmap,shapes.symbols,shapes.arrows,automata,angles,3d,trees,shadows,shapes.callouts,decorations.pathreplacing,decorations.markings}}
\\begin{{document}}
{tikz_code}
\\end{{document}}"""

    try:
        # 写入临时 tex 文件
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        # 调用 xelatex 编译
        result = subprocess.run(
            [
                "xelatex",
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "-output-directory=.",
                os.path.basename(tex_path),
            ],
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env=build_restricted_tex_environment(temp_dir),
        )

        if result.returncode != 0:
            # 尝试提取编译错误原因
            log_content = ""
            if os.path.exists(log_path):
                try:
                    with open(log_path, "rb") as lf:
                        raw_log = lf.read()
                        try:
                            log_text = raw_log.decode("utf-8")
                        except UnicodeDecodeError:
                            log_text = raw_log.decode("gbk", errors="replace")
                        lines = log_text.splitlines()
                        # 找到包含 ! 的报错行
                        error_lines = [line.strip() for line in lines if line.startswith("!")]
                        if error_lines:
                            log_content = "\n".join(error_lines[:3])
                except Exception:
                    pass
            error_msg = log_content if log_content else "LaTeX 语法错误，编译失败。"
            raise RuntimeError(f"编译错误: {error_msg}")

        if not os.path.exists(pdf_path):
            raise RuntimeError("编译未生成 PDF 文件。")

        # 使用 fitz 将 PDF 转换成 PNG
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            raise RuntimeError("生成的 PDF 文件为空。")
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=250)
        pix.save(png_path)
        doc.close()

        if not os.path.exists(png_path):
            raise RuntimeError("PDF 转换 PNG 失败。")

        # 将最终生成的图片拷贝到 uploads 目录下
        final_filename = f"tikz_{unique_id}.png"
        final_dest = os.path.join(UPLOAD_DIR, final_filename)
        shutil.copy2(png_path, final_dest)

        # 返回相对路径
        return f"/{UPLOAD_DIR_REL}/{final_filename}"

    except subprocess.TimeoutExpired:
        raise RuntimeError("编译超时 (15秒)，可能是您的 TikZ 绘图循环出现了死循环。")
    except Exception as e:
        raise RuntimeError(str(e))
    finally:
        # 清理临时文件
        for temp_file in [tex_path, pdf_path, png_path, aux_path, log_path]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

@app.post("/api/render_tikz")
def render_tikz_endpoint(tikz_code: str = Form(...)):
    """接收 TikZ 代码并编译成静态 PNG，返回其相对路径"""
    try:
        image_path = compile_tikz_to_png(tikz_code)
        return {"status": "success", "image_path": image_path}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/correct_tikz")
def correct_tikz_endpoint(
    tikz_code: str = Form(...),
    original_image_path: str = Form(...),
    user_prompt: str = Form(None)
):
    """利用用户指定的高级绘图模型进行 TikZ 纠错，支持人工指导意见注入"""
    import base64

    # 动态读取高级绘图模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
    draw_provider = resolve_draw_provider(prefer_draw)
    if not draw_provider.api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                f"未配置 {draw_provider.credential_label}！"
                "请在设置面板中配置后重试。"
            ),
        )
    model_name = draw_provider.model_name
    print(
        f"[TikZ Correction] 启用 {draw_provider.provider_label} 高级模型进行纠错: "
        f"{model_name}, Base URL: {draw_provider.chat_completions_url}"
    )

    # 对原始截图进行 Base64 编码
    try:
        clean_original_path = resolve_upload_asset(
            original_image_path,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    except AssetSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with open(clean_original_path, "rb") as f:
            encoded_original = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"读取原始图片失败: {str(e)}")

    # 尝试编译当前的 TikZ 代码
    rendered_image_path = None
    compile_error_log = None
    try:
        rendered_image_path = compile_tikz_to_png(tikz_code)
    except Exception as e:
        compile_error_log = str(e)

    # 视觉比对模式（编译成功，获取到两张图）
    if rendered_image_path:
        try:
            clean_rendered_path = resolve_upload_asset(
                rendered_image_path,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
        except AssetSecurityError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            with open(clean_rendered_path, "rb") as f:
                encoded_rendered = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"读取渲染出的 TikZ 图片失败: {str(e)}")

        prompt = build_tikz_correction_prompt(
            tikz_code,
            user_guidance=user_prompt,
            rendered_comparison=True,
        )

        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_original}"
                }
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_rendered}"
                }
            }
        ]
        
        # 临时创建的渲染图在使用后也可以删除，以节省磁盘
        try:
            clean_rendered_path.unlink()
        except Exception:
            pass

    # 报错自愈模式（编译失败，只有原始图 + 报错日志）
    else:
        prompt = build_tikz_correction_prompt(
            tikz_code,
            user_guidance=user_prompt,
            compile_error_log=compile_error_log,
            rendered_comparison=False,
        )

        content_payload = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encoded_original}"
                }
            }
        ]

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": content_payload
            }
        ],
        "stream": False
    }
    payload = inject_reasoning_effort(payload, draw_provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=draw_provider.provider_code,
        model_name=draw_provider.model_name,
        task="draw",
    )

    try:
        response = post_chat_completion(
            draw_provider,
            payload,
            timeout=90,
            check_status=False,
        )
        if response.status_code != 200:
            raise RuntimeError(f"大模型接口返回错误 HTTP {response.status_code}")
        
        res_json = response.json()
        choices = res_json.get("choices", [])
        if not choices:
            raise RuntimeError("大模型返回结果为空 choices")
            
        ai_message = choices[0].get("message", {}).get("content", "")
        
        # 使用正则从大模型的回答中抓取 ```latex ... ``` 里面的内容
        match = re.search(r"```(?:latex)?(.*?)```", ai_message, re.DOTALL | re.IGNORECASE)
        corrected_code = match.group(1).strip() if match else ai_message.strip()
        
        return {
            "status": "success",
            "corrected_code": corrected_code,
            "mode": "visual_diff" if rendered_image_path else "error_recovery"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI 纠错请求失败: {str(e)}")

@app.post("/api/ai/draw_tikz_from_image")
def draw_tikz_from_image_endpoint(
    image_path: str = Form(...),
    latex_content: str = Form(None),
    x_local_token: str = Header(None, alias="X-Local-Token")
):
    """根据指定的题目图片，调用高级多模态模型生成对应的 LaTeX TikZ 代码"""
    # Middleware already enforces this header for HTTP calls.  Keep the direct
    # function guard tied to the same single token source for test/internal use.
    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        physical_path = resolve_upload_asset(
            image_path,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
    except AssetSecurityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
        
    # 动态读取绘图高级模型配置
    prefer_draw = os.getenv("PREFER_DRAW_MODEL") or os.getenv("PREFER_PARSE_MODEL") or "Qwen/Qwen3-VL-32B-Instruct"
    
    try:
        print(f"[API Draw TikZ] 正在调用高级模型 {prefer_draw} 对插图 {image_path} 进行多模态 TikZ 绘图分析...")
        tikz_code = draw_tikz_via_high_model(
            physical_path,
            prefer_draw,
            latex_content=latex_content
        )
        
        if not tikz_code:
            raise RuntimeError(f"多模态高级模型 {prefer_draw} 未能生成有效的 TikZ 代码")
            
        return {
            "status": "success",
            "tikz_code": tikz_code
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"AI 识图绘图失败: {str(e)}")

# ----------------- Questions Management API -----------------

@app.get("/api/questions")
def list_questions(
    q: str = None,
    search: str = None,
    compulsory: str = None,
    category_compulsory: str = None,
    chapter: str = None,
    category_chapter: str = None,
    knowledge: str = None,
    category_knowledge: str = None,
    knowledge_list: str = None,
    solve_method: str = None,
    qtype: str = None,
    question_type: str = None,
    difficulty: str = None,
    source: str = None,
    subject: str = None,
    origin: str = None,
    page: Optional[int] = None,
    page_size: int = 20,
    sort: str = "desc",
    db: Session = Depends(get_db)
):
    search_q = q or search
    comp_val = compulsory or category_compulsory
    chap_val = chapter or category_chapter
    know_val = knowledge or category_knowledge
    type_val = qtype or question_type

    query = db.query(Question)
    
    # Check if searching for a specific display sequence number
    target_id_by_seq = None
    if search_q:
        clean_q = search_q.strip()
        if clean_q.startswith("#"):
            clean_q = clean_q[1:]
        if clean_q.isdigit():
            seq_val = int(clean_q)
            if seq_val >= 1:
                row = (
                    db.query(Question.id)
                    .order_by(Question.id.asc())
                    .offset(seq_val - 1)
                    .limit(1)
                    .first()
                )
                if row:
                    target_id_by_seq = row[0]

    if search_q:
        if target_id_by_seq is not None:
            query = query.filter(
                (Question.id == target_id_by_seq) |
                (Question.content.like(f"%{search_q}%")) | 
                (Question.source.like(f"%{search_q}%")) |
                (Question.answer_markdown.like(f"%{search_q}%")) |
                (Question.review.like(f"%{search_q}%")) |
                (Question.tags.like(f"%{search_q}%"))
            )
        else:
            query = query.filter(
                (Question.content.like(f"%{search_q}%")) | 
                (Question.source.like(f"%{search_q}%")) |
                (Question.answer_markdown.like(f"%{search_q}%")) |
                (Question.review.like(f"%{search_q}%")) |
                (Question.tags.like(f"%{search_q}%"))
            )
    if comp_val:
        query = query.filter(Question.category_compulsory == comp_val)
    if chap_val:
        # 主分类命中，或关联章节(related_curriculums JSON)中含有该章节，融合题也能被检索到。
        # 使用 JSON1 函数精确匹配章节，避免 LIKE 通配符/转义位置带来的脆弱性。
        query = query.filter(
            or_(
                Question.category_chapter == chap_val,
                text(
                    "EXISTS (SELECT 1 FROM json_each(COALESCE(related_curriculums, '[]')) "
                    "WHERE json_extract(value, '$.chapter') = :chap)"
                ).bindparams(chap=chap_val),
            )
        )
    if know_val:
        query = query.filter(Question.category_knowledge == know_val)
    if knowledge_list:
        # 多选标签 OR 匹配：逗号分隔取值列表，任一标签精确命中即返回
        # 用 LIKE 边界匹配避免子串误命中 (如 "函数" 不应匹配 "函数单调性")
        know_values = [v.strip() for v in knowledge_list.split(",") if v.strip()]
        if know_values:
            kl_filters = []
            for v in know_values:
                esc = v.replace("%", "\\%").replace("_", "\\_")
                kl_filters.append(Question.knowledge_list.like(f"{esc},%"))
                kl_filters.append(Question.knowledge_list.like(f"%,{esc}"))
                kl_filters.append(Question.knowledge_list == esc)
            query = query.filter(or_(*kl_filters))
    if solve_method:
        method_values = [v.strip() for v in solve_method.split(",") if v.strip()]
        if method_values:
            sm_filters = []
            for v in method_values:
                esc = v.replace("%", "\\%").replace("_", "\\_")
                sm_filters.append(Question.solve_method.like(f"{esc},%"))
                sm_filters.append(Question.solve_method.like(f"%,{esc}"))
                sm_filters.append(Question.solve_method == esc)
            query = query.filter(or_(*sm_filters))
    if type_val:
        query = query.filter(Question.question_type == type_val)
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)
    if source:
        query = query.filter(Question.source.like(f"%{source}%"))
    # 学科是题库的一等维度：三个学科 Tab 各自请求，互不混科。组卷页允许同时勾多科
    # （一套卷子可以跨学科），所以这里额外接受逗号分隔的多值；单值路径保持原语义，
    # 连「未知值回落数学」的历史行为都不动。
    if subject:
        raw_subjects = [entry.strip() for entry in str(subject).split(",") if entry.strip()]
        if len(raw_subjects) == 1:
            query = query.filter(Question.subject == normalize_subject(raw_subjects[0]))
        elif raw_subjects:
            picked_subjects: list = []
            for entry in raw_subjects:
                code = normalize_subject(entry)
                if code not in picked_subjects:
                    picked_subjects.append(code)
            query = query.filter(Question.subject.in_(picked_subjects))
    # 渠道（bank / mistake）用于「只看错题」这类筛选。
    if origin:
        query = query.filter(Question.origin == str(origin).strip())
        
    order_columns = (
        (Question.created_at.asc(), Question.id.asc())
        if str(sort).lower() == "asc"
        else (Question.created_at.desc(), Question.id.desc())
    )
    if page is not None:
        safe_page_size = max(1, min(int(page_size), 100))
        total = query.count()
        total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
        safe_page = max(1, min(int(page), total_pages))
        questions = (
            query.order_by(*order_columns)
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
            .all()
        )
        seq_map = get_seq_mapping(db, [item.id for item in questions])
        return {
            "items": [
                {**item.to_summary_dict(), "seq_num": seq_map.get(item.id)}
                for item in questions
            ],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
            "total_pages": total_pages,
        }

    questions = query.order_by(*order_columns).all()
    seq_map = get_seq_mapping(db, [item.id for item in questions])
    return [{**item.to_summary_dict(), "seq_num": seq_map.get(item.id)} for item in questions]


@app.get("/api/knowledge-stats")
def knowledge_stats(
    compulsory: Optional[str] = None,
    chapter: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """按「必修 + 章节」过滤，返回 ``{category_knowledge: count}`` 字典。

    服务端 ``GROUP BY category_knowledge`` 直接聚合，前端不再拉整章题目的完整
    LaTeX 题干/图片路径再客户端 forEach 统计（实测最大章节 228.8 KB vs 聚合
    后 0.68 KB，约 339× 传输浪费）。

    章节过滤与 ``list_questions`` 一致：主分类或关联章节(related_curriculums
    JSON)命中即统计，融合题不漏算。
    """
    from sqlalchemy import func

    query = db.query(
        Question.category_knowledge.label("knowledge"),
        func.count(Question.id).label("count"),
    )
    if compulsory:
        query = query.filter(Question.category_compulsory == compulsory)
    if chapter:
        query = query.filter(
            or_(
                Question.category_chapter == chapter,
                text(
                    "EXISTS (SELECT 1 FROM json_each(COALESCE(related_curriculums, '[]')) "
                    "WHERE json_extract(value, '$.chapter') = :chap)"
                ).bindparams(chap=chapter),
            )
        )
    rows = query.group_by(Question.category_knowledge).all()
    # 库内 category_knowledge 默认 ""，与 list_questions 行为对齐：空值统一显示为「未细分知识点」
    result: dict = {}
    for knowledge, count in rows:
        key = knowledge if knowledge else "未细分知识点"
        result[key] = result.get(key, 0) + count
    return result


@app.get("/api/questions/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    seq_map = get_seq_mapping(db, [q.id])
    q_dict = q.to_dict()
    q_dict["seq_num"] = seq_map.get(q.id)
    return q_dict


@app.get("/api/tag-options")
def tag_options(db: Session = Depends(get_db)):
    """返回题库中去重后的知识点与解题方法标签列表，供前端多选筛选使用。"""
    knowledge_set: set[str] = set()
    method_set: set[str] = set()
    rows = db.query(Question.knowledge_list, Question.solve_method).all()
    for kl, sm in rows:
        if kl:
            for t in re.split(r"[,，;；\n]+", kl):
                if t.strip():
                    knowledge_set.add(t.strip())
        if sm:
            for t in re.split(r"[,，;；\n]+", sm):
                if t.strip():
                    method_set.add(t.strip())
    return {
        "status": "success",
        "knowledge_list": sorted(knowledge_set),
        "solve_method": sorted(method_set),
    }

def normalize_fillin_macro(text: str) -> str:
    """将题干中的任何下划线格式（\\underline{...}、\\fillin[...]、连续划线 ___）一律统一规范化为最纯粹的 \\fillin 宏"""
    if not text or not isinstance(text, str):
        return text or ""
    # 1. 替换连续下划线 ___ (3个及以上) 为 \fillin
    text = re.sub(r'_{3,}', r'\\fillin', text)
    # 2. 替换任何带参数的 \fillin[...] 为纯净的 \fillin
    text = re.sub(r'\\fillin\s*\[[^\]]*?\](?:\[[^\]]*?\])?', r'\\fillin', text)
    # 3. 替换任何形式的 \underline{...} 为纯净的 \fillin
    text = re.sub(r'\\underline\s*\{[^}]*?\}', r'\\fillin', text)
    # 4. 清理可能残留的额外右花括号 }
    text = re.sub(r'\\fillin\}', r'\\fillin', text)
    return text


_FIGURE_PLACEHOLDER_MARK = "[插图待补: 图1]"

#: 模型自绘的 TikZ 段落 / 单边 token。产品口径是「图由人工截图补入」，
#: 所以这里只做换算与擦除，**不做任何编译**。
_TIKZ_PICTURE_RE = re.compile(
    r"\\begin\s*\{\s*tikzpicture\s*\}.*?\\end\s*\{\s*tikzpicture\s*\}",
    re.DOTALL | re.IGNORECASE,
)
_TIKZ_BEGIN_RE = re.compile(r"\\begin\s*\{\s*tikzpicture\s*\}", re.IGNORECASE)
_TIKZ_END_RE = re.compile(r"\\end\s*\{\s*tikzpicture\s*\}", re.IGNORECASE)
_TIKZ_TOKEN_RE = re.compile(r"tikzpicture", re.IGNORECASE)
_FIGURE_PLACEHOLDER_RE = re.compile(r"\[插图待补\s*[:：]\s*[^\]]*\]")

#: 模型伪造图形位的其它写法。渲染端只支持 KaTeX 的数学环境，下面这些一个都不认，
#: 留在正文里就是一行谁也读不懂的纯文本。实测 Qwen3-VL 被禁止画 TikZ 之后就改用
#: `\begin{center}\includegraphics{image-placeholder}\end{center}` 这一套。
_FAKE_INCLUDE_RE = re.compile(
    r"\\includegraphics\s*\*?\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", re.IGNORECASE
)
_FAKE_ENV_TAGS_RE = re.compile(
    r"\\(?:begin|end)\s*\{\s*"
    r"(?:center|figure|flushleft|flushright|minipage|wrapfigure|asy|pspicture)"
    r"\s*\}(?:\s*\[[^\]]*\])?(?:\s*\{[^}]*\})?",
    re.IGNORECASE,
)
_FAKE_CAPTION_RE = re.compile(r"\\caption\s*\*?\s*\{[^{}]*\}", re.IGNORECASE)
_TEXT_WRAPPED_PLACEHOLDER_RE = re.compile(
    r"\\text\s*\{\s*(\[插图待补\s*[:：]\s*[^\]]*\])\s*\}", re.IGNORECASE
)
#: 占位符那一行上的排版残渣：模型常把它跟 \quad / \qquad 之类捆在一起，
#: 那些命令在数学环境外不渲染，留着就是从占位符旁边多出一串反斜杠。
_ORPHAN_SPACING_RE = re.compile(r"\\(?:quad|qquad)\b")

#: 触发兜底的指纹。命中任意一个才动手，否则内容一个字节都不改 —— 人工写好的
#: 编号、人工粘的图片 markdown 都不该被这条链路碰。
_FIGURE_SCAFFOLDING_HINTS = ("tikzpicture", "includegraphics", "\\caption", "\\begin{center}",
                             "\\begin{figure}", "\\begin{minipage}", "\\begin{flushleft}",
                             "\\begin{flushright}")


def replace_drawn_figures_with_placeholders(text: str) -> str:
    """把模型「画」出来的图形换算成 ``[插图待补: 图N]`` 占位符（零 token 兜底）。

    产品口径：**错题流程不做 TikZ 编译**，题干/解析里的图一律由使用者在原卷上框选
    补入（前端 TikZ 入口 2026-08-26 就已隐藏），正文里不该出现任何图形代码。提示词
    写明了「留位不画」，但模型守不守是概率问题，实测同一批 4 道题里 2 道自行画了整段
    ``tikzpicture``；提示词收紧之后它不画 TikZ 了，却改用
    ``\\begin{center}\\includegraphics{image-placeholder}\\end{center}`` 来占位 ——
    同一类缺陷换了个写法。渲染端只认 KaTeX，这两种写法在审校页都只是一行纯文本乱码。

    所以在写回这一步做零 token 兜底，按顺序处理：

    1. TikZ 段落（成对）+ 落单的 ``\\begin`` / ``\\end`` token；
    2. ``\\includegraphics`` / ``\\caption`` / 纯排版环境壳子（center、figure、
       minipage…）—— 只脱壳，里面的内容照留；
    3. ``\\text{[插图待补: 图N]}`` 这类把占位符裹起来的写法就地拆开；
    4. 占位符那一行上的 ``\\quad`` 之类排版残渣清掉，连续空行压成一个。

    最后按出现顺序统一重排编号：前端是**按占位符出现顺序**配对 ``figure_images``
    的，编号与顺序错位会让「图 2」配上第一张图。

    没有任何图形脚手架的指纹时原样返回 —— 人工写的内容不该被这条链路碰。
    """

    raw = str(text or "")
    lowered = raw.lower()
    if not any(hint in lowered for hint in _FIGURE_SCAFFOLDING_HINTS):
        return raw

    cleaned = _TIKZ_PICTURE_RE.sub(_FIGURE_PLACEHOLDER_MARK, raw)
    if "tikzpicture" in cleaned.lower():
        cleaned = _TIKZ_BEGIN_RE.sub(_FIGURE_PLACEHOLDER_MARK, cleaned)
        cleaned = _TIKZ_END_RE.sub("", cleaned)
        cleaned = _TIKZ_TOKEN_RE.sub("", cleaned)
    cleaned = _TEXT_WRAPPED_PLACEHOLDER_RE.sub(r"\1", cleaned)
    cleaned = _FAKE_INCLUDE_RE.sub("", cleaned)
    cleaned = _FAKE_CAPTION_RE.sub("", cleaned)
    cleaned = _FAKE_ENV_TAGS_RE.sub("", cleaned)

    lines = []
    for line in cleaned.split("\n"):
        if _FIGURE_PLACEHOLDER_RE.search(line):
            line = _ORPHAN_SPACING_RE.sub(" ", line)
            line = re.sub(r"[ \t]{2,}", " ", line).strip()
        lines.append(line.rstrip())
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))

    counter = {"seq": 0}

    def _renumber(_match):
        counter["seq"] += 1
        return f"[插图待补: 图{counter['seq']}]"

    return _FIGURE_PLACEHOLDER_RE.sub(_renumber, cleaned)


def _strip_leading_question_number(content: str) -> str:
    """剥离套卷导入时残留的原卷题号（仅去除题干最开头的顺序编号，避免组卷时与系统编号叠加）。

    处理范围：阿拉伯数字 / 中文数字开头的 "16." "（16）" "16、" "16、"、"(1)"、
    "第1题"、全角句点 "16．" 等纯序号前缀。

    刻意不处理：题干内部的小问序号（如 "(1) 求..." 出现在句中时）、
    以及 "3.14""1.5万" 这类小数开头的实质内容（用 (?!\\d) 保护）。
    """
    if not content or not isinstance(content, str):
        return content
    import re as _re
    # 仅当题号处于字符串最开头时才剥离；只命中无歧义的大题编号，
    # 不命中题干自然开头的小问序号（如 "(1) 求..."）或"12 名学生"这类内容。
    # (?!\d) 保护小数：避免 "3.14 的值" 被误删成 "14 的值"。
    cleaned = _re.sub(
        r"^\s*"
        r"(?:"
        r"第\s*\d{1,3}\s*题|"                       # 第1题 / 第 12 题
        r"\(?\d{1,3}[\)）]?[\.、．\)）](?!\d)|"      # 16. 16) 16、 16．（16）
        r"[一二三四五六七八九十百]{1,3}[\.、．](?!\d)|"  # 一. 二、 三．
        r"[\(（][一二三四五六七八九十]+[\)）]|"        # （一）
        r"[①②③④⑤⑥⑦⑧⑨⑩]+\s?"                   # ①②③
        r")\s*",
        "",
        content,
        count=1,
    )
    return cleaned.strip() if cleaned else content


# PDF / DOCX 拆题后的答案区识别 + 题号回填。
# ──────────────────────────────────────────────────────────────────────────────
# 历史问题：原卷在题号区之后往往另起一节集中放出参考答案（如 `7. D\n解析：…`，
# 按题号递增排），但 LLM 按 8 题一段切片时，跨段引用容易断，客观题答案就被静默清空。
# 这里在拆题后用零 token 的正则扫描全部 OCR 页面文本，按题号建索引，再回填
# `_orig_seq_int` 与 `answer_markdown` —— 与 AI 拆出来的 content 完全解耦。
# 答案区起点必须出现以下之一：
#   ① 浓郁眉批式 header（「一数 高考数学核心方法」、「【参考答案】」）；
#   ② 页面首次出现「题号. 答案 \n 解析：」全格式密集排列；
# 进入答案区后，按题号切段，每段第一行除题号外为答案，其余为解析。
_PDF_ANSWER_HEADER_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"[【\[（(]\s*(?P<header>[^】\]）)\n]{2,40}?)\s*[】\]）)]"
    r"|"
    r"(?P<header2>[一数]?\s*高考数学核心方法[^\n]*?答案\s*[区页]?)"
    r")",
    re.MULTILINE,
)
# 答案行的「题号. 答案」开头 pattern（用于定位题号起点与答案起始处）。
# 必须从行首开头，ans 至少一字符（避免惰性匹配全部吃掉答案），且只接受
# 中文 / 字母 / 数字 / LaTeX 符号起点 —— 否则 OCR 误把含 `9. 解：…` 这种
# 段落里的点识别为题号。
_PDF_ANSWER_HEAD_RE = re.compile(
    r"(?m)^(?P<num>[0-9]{1,3})\s*[.、．)）]\s*"
    r"(?P<ans>\$?[A-D一-鿿\$0-9])[^\n]*?$"   # ans 起点必须为非空标识符
)
# 答案段的「解析：<文本>」拆分点
_PDF_ANSWER_EXPL_RE = re.compile(r"\n\s*解析\s*[：:]\s*")


def _extract_pdf_answer_zone(ocr_results):
    """扫描全部 OCR 页面文本，提取按题号排列的原卷答案。

    返回 ``dict[int, dict]``：``题号 -> {"answer": ..., "explanation": ..., "page_index": ...}``。
    """
    answer_zone: dict[int, dict] = {}
    if not ocr_results:
        return answer_zone

    # 1. 串联全文，同时维护「绝对偏移 -> page_index」映射，便于回查答案所在物理页
    text_offsets: list[tuple[int, int]] = []
    full_text_parts: list[str] = []
    cursor = 0
    for page_index, page_text in enumerate(ocr_results):
        body = (page_text or "").strip()
        if not body:
            continue
        cleaned = re.sub(r"<!--\s*MATHBANK_PDF_PAGE:\d+\s*-->", "", body)
        text_offsets.append((page_index, cursor))
        full_text_parts.append(cleaned)
        cursor += len(cleaned) + 1
    full_text = "\n".join(full_text_parts)
    if not full_text:
        return answer_zone

    # 2. 找答案区起点
    zone_start = -1
    for header_match in _PDF_ANSWER_HEADER_RE.finditer(full_text):
        probe_window = full_text[header_match.end(): header_match.end() + 200]
        if re.search(
            r"(?m)^\s*[0-9]{1,3}\s*[.、．)）]\s*[A-D一-鿿][^\n]*\s*\n\s*解析",
            probe_window,
        ):
            zone_start = header_match.end()
            break
    if zone_start < 0:
        # 退化路径：找第一处「题号. 答案 \n 解析：」密集出现的起点。
        # ans 首字符容忍：选项字母 A-D / LaTeX 符号 $ / 中文 / 数字 / 数学符号，
        # 否则像 `1. $\dfrac{3}{2}$ \n 解析` 这种纯 LaTeX 答案也会被错过。
        first_item = re.search(
            r"(?m)^(?P<num>[0-9]{1,3})\s*[.、．)）]\s*"
            r"(?:[A-D]|\$|\$?[一-鿿]|\\[a-z]{2,}|\(|=-|\.|[0-9])[^\n]*\s*\n\s*解析",
            full_text,
        )
        if first_item:
            zone_start = first_item.start()
        else:
            return answer_zone

    zone_text = full_text[zone_start:]
    # 退出条件：遇到反思 / 章节标题 / 总结 → 答案区结束
    end_match = re.search(
        r"\n\s*(?:【反思】|反思\s|考点\s|学科\s【|第\s*[一二三四五六七八九十]+\s*章)",
        zone_text,
    )
    if end_match:
        zone_text = zone_text[: end_match.start()]

    # 3. 找所有「题号. 答案」开头。每个题号边界即为上一题的结束。
    heads: list[tuple[int, int, int]] = []  # (题号, 答案起始处 start, 答案文本 group("ans") 的 absolute start)
    for m in _PDF_ANSWER_HEAD_RE.finditer(zone_text):
        try:
            n = int(m.group("num"))
        except (TypeError, ValueError):
            continue
        # 答案必须仅占一行 + 后面紧跟 \n 解析：, 否则视为题干跳号（防止把「4. (2025)」误识别为答案）
        tail = zone_text[m.end(): m.end() + 200]
        if not re.search(r"\n\s*解析", tail):
            continue
        heads.append((n, m.start(), m.start("ans")))
    if not heads:
        return answer_zone

    # 4. 按切片解析 答案+解析
    for i, (n, start, ans_start) in enumerate(heads):
        end = heads[i + 1][1] if i + 1 < len(heads) else len(zone_text)
        block = zone_text[start:end]
        # 答案文本 = ans_start 到 block 内第一个换行
        ans_text_in_block = block[ans_start - start:].split("\n", 1)[0].strip()
        # 解析文本 = block 第一个「解析：」之后到末尾
        expl_split = _PDF_ANSWER_EXPL_RE.search(block)
        explanation = ""
        if expl_split:
            explanation = block[expl_split.end():].strip()
        page_index = 0
        abs_start = zone_start + start
        for page_idx, offset in text_offsets:
            if offset <= abs_start:
                page_index = page_idx
        answer_zone[n] = {
            "answer": ans_text_in_block,
            "explanation": explanation,
            "page_index": page_index,
        }
    return answer_zone


def _apply_pdf_answer_zone(parsed_questions, answer_zone):
    """把答区按题号回填到每题 ``answer_markdown``，仅在 AI 漏填时注入。

    设计上保持「AI 写出来的不覆盖，AI 未填才补」的原则，避免把单题多问互救到的
    干干字答案抹掉。补的内容使用一致的「答案 → 解析」开头格式，保留 LLM 后来替
    补后的连贯。
    """
    if not answer_zone:
        return 0
    filled = 0
    for q in parsed_questions:
        orig_seq = q.pop("_orig_seq_int", None)
        if not orig_seq:
            continue
        hit = answer_zone.get(orig_seq)
        if not hit:
            continue
        existing = (q.get("answer_markdown") or "").strip()
        # 本题在拆题阶段 AI 没出来有意义的答案时才填空
        if existing and len(existing) >= 2:
            continue
        ans_text = hit["answer"]
        explanation = hit["explanation"]
        body_parts = ["[EXTRACTED_ORIGINAL]", ans_text]
        if explanation:
            body_parts.append(explanation)
        q["answer_markdown"] = "\n\n".join(body_parts).strip()
        # 复查字段后仅在 source 还未能从 answer 内容推导出时同步补
        if "page_index" in hit:
            q.setdefault("_answer_page_index", hit["page_index"])
        filled += 1
    return filled



def committed_question_response(
    db: Session,
    db_question: Question,
    question_id: int,
    *,
    operation: str,
) -> dict:
    """Serialize a committed write without ever misreporting it as failed."""

    try:
        db.refresh(db_question)
        seq_map = get_seq_mapping(db, [question_id])
        question = db_question.to_dict()
        question["seq_num"] = seq_map.get(question_id)
        return {"status": "success", "question": question}
    except Exception as exc:
        # The durable transaction is already complete.  End any failed read
        # transaction and return enough identity for the client to continue;
        # a later list/detail refresh can obtain the full representation.
        try:
            db.rollback()
        except Exception:
            pass
        print(
            f"[Question Write] Post-commit {operation} response degraded "
            f"(type={type(exc).__name__})."
        )
        return {"status": "success", "question": {"id": question_id}}


@app.get("/api/documents/imported")
def check_document_imported(name: str = "", db: Session = Depends(get_db)):
    """按来源文件名精确判断该题是否已导入题库，供前端「已导入文档免重复拆解」使用。

    入库时解析题的 source 默认填为来源文件名（见 import.js appendParsedQuestions），
    因此 source == name 即代表该文档的题已进入题库。
    """
    if not name:
        return {"imported": False, "count": 0}
    # 入库时 source 经 normalize_source 归一后落库，故比较前同样归一，
    # 否则经 CANONICAL_MAP / Tier2 改写的文件名永远匹配不到，去重守卫静默失效。
    count = db.query(Question).filter(Question.source == normalize_source(name)).count()
    return {"imported": count > 0, "count": count}


def parse_related_curriculums(raw):
    """Validate and normalize the related_curriculums payload.

    Accepts a JSON string or a list. Returns a compact JSON string for storage.
    Each entry must be a dict with at least a non-empty ``chapter``. Entries are
    deduplicated by (compulsory, chapter, knowledge).
    """
    if raw is None:
        return "[]"
    if isinstance(raw, str):
        if not raw.strip():
            return "[]"
        try:
            data = json.loads(raw)
        except Exception:
            return "[]"
    else:
        data = raw
    if not isinstance(data, list):
        return "[]"
    seen = set()
    out = []
    for item in data:
        if isinstance(item, str):
            # AI 拆卷可能产出 "学段 / 章节 / 小节" 字符串，按 "/" 拆分归一化。
            # 仅当含分隔符 "/" 才视为有效关联章节描述，避免把无意义的纯文本误当成章节。
            if '/' not in item:
                continue
            parts = [p.strip() for p in item.split("/") if p.strip()]
            if not parts:
                continue
            item = {
                "compulsory": parts[0] if len(parts) > 1 else "",
                "chapter": parts[1] if len(parts) > 1 else parts[0],
                "knowledge": parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else parts[0]),
            }
        if not isinstance(item, dict):
            continue
        chapter = (item.get("chapter") or "").strip()
        if not chapter:
            continue
        compulsory = (item.get("compulsory") or "").strip()
        knowledge = (item.get("knowledge") or "").strip()
        key = (compulsory, chapter, knowledge)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "compulsory": compulsory,
            "chapter": chapter,
            "knowledge": knowledge,
        })
    return json.dumps(out, ensure_ascii=False)


@app.post("/api/questions")
def create_question(
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    subject: str = Form(""),  # 学科（math/physics/chemistry）；老请求不带时由 normalize_subject 归入 math
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    difficulty: str = Form(...),
    source: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    figure_align: str = Form("right"),
    tags: str = Form(""),
    knowledge_list: str = Form(""),  # 知识点多标签 (逗号分隔)
    solve_method: str = Form(""),  # 解题方法多标签 (逗号分隔)
    related_curriculums: str = Form("[]"),  # 关联章节(JSON数组: [{compulsory,chapter,knowledge}])
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),  # JSON array string
    force: str = Form("false"),  # 查重命中时是否强制入库
    db: Session = Depends(get_db)
):
    asset_promotions: list[tuple[Path, Path]] = []
    try:
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)
        # 选择题选项统一为 choices 网格环境（双保险：AI 即便输出内联 A./B./C./D. 也归一化）
        content = normalize_choice_options_to_latex(content)

        # 来源自动归一（与一次性批量归一、拆卷导入共用同一映射表）
        source = normalize_source(source)

        # 学科归一：老请求不带 subject 时退回 math，语义与历史一致。
        # 录入单题对三科开放（这是物化的手动录入口子）；导入试卷仍只对数学开放。
        subject_value = normalize_subject(subject)

        # Validate json array format
        parsed_img_paths = json.loads(image_paths) if image_paths else []
        
        # 自动晋升临时图片
        content, answer_markdown, parsed_img_paths = promote_question_temp_assets(
            content,
            answer_markdown,
            parsed_img_paths,
            promotion_log=asset_promotions,
        )
        parsed_img_paths = normalize_upload_asset_references(
            parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        
        # 1. Fallback if third level is empty, default to chapter
        if not category_knowledge and category_chapter:
            category_knowledge = category_chapter

        # 规范化多标签：受控词表映射 + 逗号分隔、去空白、去重、保序
        norm_knowledge_list = normalize_tag_list(knowledge_list, field="knowledge_list")
        # 防御性剥离：任何入库路径（含手动录入）都移除题干开头残留的原卷顺序题号
        content = _strip_leading_question_number(content)
        norm_solve_method = normalize_tag_list(solve_method, field="solve_method")
        # 若知识点多标签为空，但单值知识点存在，则回填到多标签
        if not norm_knowledge_list and category_knowledge:
            norm_knowledge_list = normalize_tag_list(category_knowledge, field="knowledge_list")

        db_question = Question(
            subject=subject_value,
            origin="bank",
            content=content,
            content_fingerprint=_normalize_question_content(content),
            question_type=question_type,
            category_compulsory=category_compulsory,
            category_chapter=category_chapter,
            category_knowledge=category_knowledge,
            difficulty=difficulty,
            source=source,
            answer_markdown=answer_markdown,
            review=review,
            tikz_code=tikz_code,
            figure_align=figure_align if figure_align in ["right", "center", "bottom_right"] else "right",
            tags=tags,
            knowledge_list=norm_knowledge_list,
            solve_method=norm_solve_method,
            related_curriculums=parse_related_curriculums(related_curriculums),
        )
        db_question.image_paths = parsed_img_paths
        
        # Handle related question association (transitive relation)
        related_id_int = int(related_question_id) if related_question_id and related_question_id.strip() else None
        if related_id_int:
            q_related = db.query(Question).filter(Question.id == related_id_int).first()
            if q_related:
                g2 = q_related.association_group_id
                if not g2:
                    new_grp = str(uuid.uuid4())
                    q_related.association_group_id = new_grp
                    db_question.association_group_id = new_grp
                else:
                    db_question.association_group_id = g2

        # ---- 入库前查重：规则见 mathbank.duplicate_check（与错题入库共用同一实现）----
        # 抽成公共函数的原因：错题工作台的「数学错题批量入库」需要完全相同的判重
        # 行为，内联两份迟早会出现「手动入库判重复、错题入库却放行」的不一致。
        force_bool = force.lower() in ("true", "1", "yes")
        if not force_bool:
            dup_id, dup_sim = find_duplicate_question(
                db, content, question_type, subject_value
            )
            if dup_id is not None:
                return duplicate_warning_payload(db, dup_id, dup_sim)

        db.add(db_question)
        db.flush()

        # Save the question and its active curriculum mirror atomically.
        # 版本码按学科取：数学走活动大纲，物化各自锁定一套。
        active_version = get_subject_version_code(db_question.subject)
        curriculum_map = QuestionCurriculum(
            question_id=db_question.id,
            version_code=active_version,
            compulsory=category_compulsory,
            chapter=category_chapter,
            knowledge=category_knowledge
        )
        db.add(curriculum_map)
        committed_question_id = db_question.id
        db.commit()
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"保存题目失败: {str(e)}")

    # Everything below is compensating or response work after the durable
    # success boundary; none of it may turn the write into a misleading 400.
    schedule_database_export(background_tasks, operation="create_question")
    return committed_question_response(
        db,
        db_question,
        committed_question_id,
        operation="create_question",
    )

@app.put("/api/questions/{question_id}")
def update_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    question_type: str = Form(...),
    subject: str = Form("math"),
    category_compulsory: str = Form(""),
    category_chapter: str = Form(""),
    category_knowledge: str = Form(""),
    difficulty: str = Form(...),
    source: str = Form(""),
    answer_markdown: str = Form(""),
    review: str = Form(""),
    tikz_code: str = Form(""),
    figure_align: str = Form("right"),
    tags: str = Form(""),
    knowledge_list: str = Form(""),
    solve_method: str = Form(""),
    related_curriculums: str = Form("[]"),  # 关联章节(JSON数组)
    related_question_id: str = Form(""),
    image_paths: str = Form("[]"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    asset_promotions: list[tuple[Path, Path]] = []
    old_images = list(db_question.image_paths)
    try:
        # 规范化填空题下划线为 \fillin 宏
        content = normalize_fillin_macro(content)
        # 选择题选项统一为 choices 网格环境（双保险：AI 即便输出内联 A./B./C./D. 也归一化）
        content = normalize_choice_options_to_latex(content)

        # 来源自动归一（与一次性批量归一、拆卷导入共用同一映射表）
        source = normalize_source(source)

        parsed_img_paths = json.loads(image_paths) if image_paths else []
        
        # 自动晋升临时图片
        content, answer_markdown, parsed_img_paths = promote_question_temp_assets(
            content,
            answer_markdown,
            parsed_img_paths,
            promotion_log=asset_promotions,
        )
        parsed_img_paths = normalize_upload_asset_references(
            parsed_img_paths,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        
        # 1. Fallback if third level is empty, default to chapter
        if not category_knowledge and category_chapter:
            category_knowledge = category_chapter

        norm_knowledge_list = normalize_tag_list(knowledge_list, field="knowledge_list")
        if not norm_knowledge_list and category_knowledge:
            norm_knowledge_list = normalize_tag_list(category_knowledge, field="knowledge_list")
        norm_solve_method = normalize_tag_list(solve_method, field="solve_method")

        db_question.content = content
        db_question.content_fingerprint = _normalize_question_content(content)
        db_question.question_type = question_type
        db_question.category_compulsory = category_compulsory
        db_question.category_chapter = category_chapter
        db_question.category_knowledge = category_knowledge
        db_question.difficulty = difficulty
        db_question.source = source
        db_question.answer_markdown = answer_markdown
        db_question.review = review
        db_question.tikz_code = tikz_code
        db_question.knowledge_list = norm_knowledge_list
        db_question.solve_method = norm_solve_method
        db_question.related_curriculums = parse_related_curriculums(related_curriculums)
        if figure_align in ["right", "center", "bottom_right"]:
            db_question.figure_align = figure_align
        db_question.tags = tags
        # Physical cleanup happens only after the database commit succeeds.
        removed_images = set(old_images) - set(parsed_img_paths)

        db_question.image_paths = parsed_img_paths
        
        # Handle related question association updates (transitive relation)
        related_id_int = int(related_question_id) if related_question_id and related_question_id.strip() else None
        if related_id_int:
            q_related = db.query(Question).filter(Question.id == related_id_int).first()
            if q_related and q_related.id != db_question.id:
                g1 = db_question.association_group_id
                g2 = q_related.association_group_id
                
                if not g1 and not g2:
                    new_grp = str(uuid.uuid4())
                    db_question.association_group_id = new_grp
                    q_related.association_group_id = new_grp
                elif g1 and not g2:
                    q_related.association_group_id = g1
                elif not g1 and g2:
                    db_question.association_group_id = g2
                else:
                    if g1 != g2:
                        db.query(Question).filter(Question.association_group_id == g1).update(
                            {Question.association_group_id: g2}, synchronize_session=False
                        )
                        db_question.association_group_id = g2
        
        # Update or create active QuestionCurriculum mapping
        active_version = get_active_version_code()
        curriculum_map = db.query(QuestionCurriculum).filter(
            QuestionCurriculum.question_id == db_question.id,
            QuestionCurriculum.version_code == active_version
        ).first()
        if not curriculum_map:
            curriculum_map = QuestionCurriculum(
                question_id=db_question.id,
                version_code=active_version
            )
            db.add(curriculum_map)
        curriculum_map.compulsory = category_compulsory
        curriculum_map.chapter = category_chapter
        curriculum_map.knowledge = category_knowledge
        
        db.commit()
    except Exception as e:
        db.rollback()
        rollback_question_asset_promotions(asset_promotions)
        raise HTTPException(status_code=400, detail=f"更新题目失败: {str(e)}")

    # The question is already durably updated at this point.  Best-effort
    # cleanup and response assembly must not turn success into a false failure.
    try:
        delete_unreferenced_question_assets(db, removed_images)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit update cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )
    schedule_database_export(background_tasks, operation="update_question")
    return committed_question_response(
        db,
        db_question,
        question_id,
        operation="update_question",
    )

@app.post("/api/questions/{question_id}/figure_align")
def update_question_figure_align(
    question_id: int,
    figure_align: str = Form("right"),
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
    if figure_align not in ["right", "center", "bottom_right"]:
        figure_align = "right"
    db_question.figure_align = figure_align
    db.commit()
    db.refresh(db_question)
    return {"status": "success", "question_id": question_id, "figure_align": figure_align}

@app.get("/api/questions/{question_id}/associated")
def get_associated_questions(question_id: int, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到题目")
        
    grp = q.association_group_id
    if not grp or grp.strip() == "":
        return []
        
    associated = db.query(Question).filter(
        Question.association_group_id == grp,
        Question.id != question_id
    ).all()
    
    seq_map = get_seq_mapping(db, [item.id for item in associated])
    return [{**item.to_dict(), "seq_num": seq_map.get(item.id)} for item in associated]

@app.post("/api/questions/{question_id}/associate")
def associate_questions_endpoint(
    background_tasks: BackgroundTasks,
    question_id: int,
    target_id: int = Form(...),
    db: Session = Depends(get_db)
):
    q1 = db.query(Question).filter(Question.id == question_id).first()
    q2 = db.query(Question).filter(Question.id == target_id).first()
    if not q1 or not q2:
        raise HTTPException(status_code=404, detail="未找到对应题目")
        
    if q1.id == q2.id:
        raise HTTPException(status_code=400, detail="不能自己和自己关联")
        
    g1 = q1.association_group_id
    g2 = q2.association_group_id
    
    try:
        if not g1 and not g2:
            new_grp = str(uuid.uuid4())
            q1.association_group_id = new_grp
            q2.association_group_id = new_grp
        elif g1 and not g2:
            q2.association_group_id = g1
        elif not g1 and g2:
            q1.association_group_id = g2
        else:
            if g1 != g2:
                db.query(Question).filter(Question.association_group_id == g1).update(
                    {Question.association_group_id: g2}, synchronize_session=False
                )
                q1.association_group_id = g2
                
        db.commit()
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        schedule_database_export(background_tasks, operation="associate_questions")
        
        return {"status": "success", "message": "关联成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"关联失败: {str(e)}")

@app.delete("/api/questions/{question_id}/associated")
def remove_association(
    question_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Remove a question from its association group (bidirectional)."""
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="未找到题目")

    grp = q.association_group_id
    if not grp or grp.strip() == "":
        return {"status": "success", "message": "该题目无关联关系"}

    try:
        # Clear this question's group ID
        q.association_group_id = ""

        # If only one other question remains in the group, clear its group too (no point in a group of one)
        remaining = db.query(Question).filter(
            Question.association_group_id == grp,
            Question.id != question_id
        ).all()

        if len(remaining) == 1:
            remaining[0].association_group_id = ""

        db.commit()
        
        # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
        schedule_database_export(background_tasks, operation="remove_association")
        
        return {"status": "success", "message": "已成功解除所有关联"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"解除关联失败: {str(e)}")

@app.delete("/api/questions/{question_id}")
def delete_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    db_question = db.query(Question).filter(Question.id == question_id).first()
    if not db_question:
        raise HTTPException(status_code=404, detail="未找到对应的题目")
        
    image_paths_to_check = list(db_question.image_paths)
    try:
        from sqlalchemy import func

        affected_paper_ids = [
            paper_id
            for (paper_id,) in db.query(PaperQuestion.paper_id)
            .filter(PaperQuestion.question_id == question_id)
            .distinct()
            .all()
        ]
        if affected_paper_ids:
            remaining_scores = dict(
                db.query(
                    PaperQuestion.paper_id,
                    func.coalesce(func.sum(PaperQuestion.score), 0),
                )
                .filter(
                    PaperQuestion.paper_id.in_(affected_paper_ids),
                    PaperQuestion.question_id != question_id,
                )
                .group_by(PaperQuestion.paper_id)
                .all()
            )
            for paper in db.query(Paper).filter(
                Paper.id.in_(affected_paper_ids)
            ):
                paper.total_score = int(remaining_scores.get(paper.id, 0))
        db.delete(db_question)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"删除题目失败: {str(e)}")

    # The database delete is complete.  Image cleanup is intentionally
    # best-effort so a locked/missing file cannot make the client believe the
    # question still exists and submit a duplicate delete.
    try:
        delete_unreferenced_question_assets(db, image_paths_to_check)
    except Exception as cleanup_exc:
        print(
            "[Storage Cleanup] Post-commit delete cleanup failed "
            f"(type={type(cleanup_exc).__name__}); it will be retried by "
            "the startup orphan cleanup."
        )

    # Auto export database to files for Git synchronization and AI referencing (Async Background Task)
    schedule_database_export(background_tasks, operation="delete_question")

    return {"status": "success", "message": "题目删除成功"}

# ----------------- Category Hierarchy Autocomplete API -----------------

# Backward-compatible names; authoritative data lives in JSON resources.
RENJIAO_A_CURRICULUM = load_curriculum("A")
RENJIAO_B_CURRICULUM = load_curriculum("B")
SUJIAO_CURRICULUM = load_curriculum("S")
HUJIAO_CURRICULUM = load_curriculum("H")

METADATA_FILE = str(DATA_BACKUP_DIR / ("custom_metadata_test.json" if IS_TESTING else "custom_metadata.json"))
METADATA_CACHE = {}

def get_current_curriculum():
    return METADATA_CACHE.get("curriculum", RENJIAO_A_CURRICULUM)

def load_or_init_metadata():
    global METADATA_CACHE
    default_metadata = build_default_metadata("A")
    
    # Ensure backup directory exists
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Verify schema
                if isinstance(loaded, dict) and "question_types" in loaded and "difficulties" in loaded and "curriculum" in loaded:
                    # Self-heal metadata file (e.g. add 常规题, update simplified book names)
                    modified = False
                    has_normal = any(d.get("value") == "normal" for d in loaded.get("difficulties", []))
                    if not has_normal:
                        loaded["difficulties"].insert(1, {"value": "normal", "label": "常规题", "color": "text-blue-600 bg-blue-50 border-blue-200"})
                        modified = True
                        
                    curriculum = loaded.get("curriculum", {})
                    mappings = {
                        "选择性必修一": "选修一",
                        "选择性必修二": "选修二",
                        "选择性必修三": "选修三",
                        "必修第一册": "必修一",
                        "必修第二册": "必修二",
                        "必修第三册": "必修三",
                        "必修第四册": "必修四",
                    }
                    new_curriculum = {}
                    for comp, chapters in curriculum.items():
                        mapped_comp = mappings.get(comp, comp)
                        if mapped_comp != comp:
                            modified = True
                        new_curriculum[mapped_comp] = chapters
                    if modified:
                        loaded["curriculum"] = new_curriculum
                        try:
                            write_private_text_atomic(
                                METADATA_FILE,
                                json.dumps(loaded, ensure_ascii=False, indent=2),
                            )
                            print(f"[Metadata Self-Heal] Upgraded {METADATA_FILE} with simplified book names and normal difficulty.")
                        except Exception as e:
                            print(f"[Metadata Self-Heal Error] Failed to write updated metadata: {e}")
                    
                    METADATA_CACHE = loaded
                    print(f"[Metadata] Loaded custom metadata from {METADATA_FILE}")
                    heal_database_curriculum_names()
                    return
        except Exception as e:
            print(f"[Metadata Warning] Error loading {METADATA_FILE}: {e}. Overwriting with default.")
            
    # Self-heal / initialize
    try:
        write_private_text_atomic(
            METADATA_FILE,
            json.dumps(default_metadata, ensure_ascii=False, indent=2),
        )
        print(f"[Metadata] Initialized default metadata at {METADATA_FILE}")
    except Exception as e:
        print(f"[Metadata Error] Could not write default metadata: {e}")
        
    METADATA_CACHE = default_metadata
    heal_database_curriculum_names()

# Load metadata on startup
load_or_init_metadata()

def get_active_version_code() -> str:
    curriculum = METADATA_CACHE.get("curriculum", {})
    combined_chapters = ""
    for book_content in curriculum.values():
        if isinstance(book_content, dict):
            combined_chapters += " ".join(book_content.keys())
    if "第一章" in combined_chapters:
        return "B"
    if "第 1 章 集合与逻辑" in combined_chapters or "数学建模活动案例" in combined_chapters or "第 2 章 等式与不等式" in combined_chapters or "第 3 章 幂、指数与对数" in combined_chapters:
        return "H"
    if "第1章" in combined_chapters:
        return "S"
    return "A"


def get_subject_version_code(subject: str | None) -> str:
    """学科对应的教材版本码，写进 ``question_curriculums.version_code``。

    数学保持原行为（由「设置 - 大纲」里的活动大纲反查 A/B/S/H）；物理恒为教科版
    ``PJK``，化学恒为人教版 ``CRJ``。所有入库路径都必须经过这里取值 —— 否则物化题
    会被挂到数学大纲下，章节筛选与知识统计就再也对不上。
    """

    subject_value = normalize_subject(subject)
    if subject_value == "math":
        return get_active_version_code()
    return default_version_for_subject(subject_value)


def get_subject_curriculum_tree(subject: str | None) -> dict:
    """学科对应的教材目录树。

    数学返回「设置 - 大纲」里的活动大纲；物理返回教科版树、化学返回人教版树。
    凡是「拿树去比对 / 让 AI 按树分类」的地方都必须走这里 —— 用数学树去处理物化，
    会让 AI 把物理题打上数学章节。
    """

    subject_value = normalize_subject(subject)
    if subject_value == "math":
        return get_current_curriculum()
    return load_curriculum(default_version_for_subject(subject_value))

@app.get("/api/config/metadata")
def get_metadata_config():
    """题库元数据。

    在原有 ``question_types`` / ``difficulties`` / ``curriculum``（数学的「活动大纲」，
    仍可在设置里编辑）之外，新增两份只读数据：

    - ``subjects``：三科 Tab 定义（value / label / 可用版本 / 默认版本）；
    - ``subject_curriculum``：``{学科: 目录树}``。物理挂教科版、化学挂人教版，
      数学直接用设置里的活动大纲。前端必须按当前学科取树 —— 三科共用一套树必然错位。
    """

    payload = dict(METADATA_CACHE)
    payload["subjects"] = [
        {
            "value": subject,
            "label": SUBJECT_LABELS[subject],
            "versions": [
                {"code": code, "name": CURRICULUM_NAMES[code]}
                for code in versions_for_subject(subject)
            ],
            "default_version": default_version_for_subject(subject),
        }
        for subject in SUBJECT_ORDER
    ]
    subject_curriculum = {"math": METADATA_CACHE.get("curriculum", {})}
    for subject in SUBJECT_ORDER:
        if subject == "math":
            continue
        subject_curriculum[subject] = load_curriculum(
            default_version_for_subject(subject)
        )
    payload["subject_curriculum"] = subject_curriculum
    return payload

@app.get("/api/config/curriculum-presets/{version}")
def get_curriculum_preset_config(version: str):
    try:
        return get_curriculum_preset(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

def route_chapter(comp: str, chap: str, know: str, target: str) -> tuple[str, str, str]:
    """跨大纲版本智能章节与小节路由翻译算法，返回 (new_compulsory, new_chapter, new_knowledge)"""
    combined = f"{comp} {chap} {know}"
    new_comp, new_chap = "", ""
    if target == "A":
        if "集合" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "2. 一元二次函数、方程和不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "4. 指数函数与对数函数"
        elif "三角函数" in combined or "三角恒等" in combined: new_comp, new_chap = "必修一", "5. 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "3. 函数的概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "6. 平面向量及其应用"
        elif "复数" in combined: new_comp, new_chap = "必修二", "7. 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "8. 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "1. 空间向量与立体几何"
        elif "直线" in combined or "圆的方程" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "3. 圆锥曲线的方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "2. 直线和圆的方程"
        elif "数列" in combined: new_comp, new_chap = "选修二", "4. 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "5. 一元函数的导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修三", "6. 计数原理"
        elif "概率" in combined or "随机变量" in combined or "分布" in combined: new_comp, new_chap = "选修三", "7. 随机变量及其分布"
        elif "统计" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修三", "8. 成对数据的统计分析"
        else: new_comp, new_chap = "必修一", "1. 集合与常用逻辑用语"
    elif target == "B":
        if "集合" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第二章 等式与不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修二", "第四章 指数函数、对数函数与幂函数"
        elif "三角函数" in combined: new_comp, new_chap = "必修三", "第七章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第三章 函数"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修四", "第九章 解三角形"
        elif "数量积" in combined or "三角恒等" in combined: new_comp, new_chap = "必修三", "第八章 向量的数量积与三角恒等变换"
        elif "平面向量" in combined: new_comp, new_chap = "必修二", "第六章 平面向量初步"
        elif "复数" in combined: new_comp, new_chap = "必修四", "第十章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修四", "第十一章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第一章 空间向量与立体几何"
        elif "直线" in combined or "圆" in combined or "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第二章 平面解析几何"
        elif "数列" in combined: new_comp, new_chap = "选修三", "第五章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修三", "第六章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第三章 排列、组合与二项式定理"
        elif "随机变量" in combined or "条件概率" in combined or "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第四章 概率与统计"
        elif "统计" in combined or "概率" in combined: new_comp, new_chap = "必修二", "第五章 统计与概率"
        else: new_comp, new_chap = "必修一", "第一章 集合与常用逻辑用语"
    elif target == "S":
        if "集合" in combined: new_comp, new_chap = "必修一", "第1章 集合"
        elif "逻辑" in combined: new_comp, new_chap = "必修一", "第2章 常用逻辑用语"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第3章 不等式"
        elif "指数" in combined or "对数" in combined: new_comp, new_chap = "必修一", "第4章 指数与对数"
        elif "三角函数" in combined: new_comp, new_chap = "必修一", "第7章 三角函数"
        elif "函数" in combined: new_comp, new_chap = "必修一", "第5章 函数概念与性质"
        elif "解三角形" in combined or "正弦" in combined or "余弦" in combined: new_comp, new_chap = "必修二", "第11章 解三角形"
        elif "数量积" in combined or "平面向量" in combined: new_comp, new_chap = "必修二", "第9章 平面向量"
        elif "三角恒等" in combined: new_comp, new_chap = "必修二", "第10章 三角恒等变换"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第12章 复数"
        elif "立体几何" in combined and "空间向量" not in combined: new_comp, new_chap = "必修二", "第13章 立体几何初步"
        elif "空间向量" in combined: new_comp, new_chap = "选修二", "第6章 空间向量与立体几何"
        elif "直线" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "圆" in combined and "圆锥曲线" not in combined: new_comp, new_chap = "选修一", "第2章 圆与方程"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined: new_comp, new_chap = "选修一", "第3章 圆锥曲线与方程"
        elif "解析几何" in combined: new_comp, new_chap = "选修一", "第1章 直线与方程"
        elif "数列" in combined: new_comp, new_chap = "选修一", "第4章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修一", "第5章 导数及其应用"
        elif "计数" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第7章 计数原理"
        elif "随机变量" in combined or "条件概率" in combined: new_comp, new_chap = "选修二", "第8章 概率"
        elif "回归" in combined or "独立性" in combined or "成对" in combined: new_comp, new_chap = "选修二", "第9章 统计"
        elif "统计" in combined: new_comp, new_chap = "必修二", "第14章 统计"
        elif "概率" in combined: new_comp, new_chap = "必修二", "第15章 概率"
        else: new_comp, new_chap = "必修一", "第1章 集合"
    elif target == "H":
        if "集合与逻辑" in combined or ("集合" in combined and "选修" not in comp): new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"
        elif "等式" in combined or "不等式" in combined: new_comp, new_chap = "必修一", "第 2 章 等式与不等式"
        elif "幂、指数" in combined or "指数与对数" in combined or ("指数" in combined and "函数" not in combined) or ("对数" in combined and "函数" not in combined): new_comp, new_chap = "必修一", "第 3 章 幂、指数与对数"
        elif "幂函数" in combined or "指数函数" in combined or "对数函数" in combined: new_comp, new_chap = "必修一", "第 4 章 幂函数、指数函数与对数函数"
        elif "反函数" in combined or "函数的概念" in combined or ("函数" in combined and "三角" not in combined and "导数" not in combined and "选修" not in comp and "必修二" not in comp and "必修三" not in comp): new_comp, new_chap = "必修一", "第 5 章 函数的概念、性质及应用"
        elif "解三角形" in combined or "正弦定理" in combined or "余弦定理" in combined or "常用三角公式" in combined or ("三角" in combined and "函数" not in combined): new_comp, new_chap = "必修二", "第 6 章 三角"
        elif "三角函数" in combined: new_comp, new_chap = "必修二", "第 7 章 三角函数"
        elif "平面向量" in combined or ("向量" in combined and "空间" not in combined): new_comp, new_chap = "必修二", "第 8 章 平面向量"
        elif "复数" in combined: new_comp, new_chap = "必修二", "第 9 章 复数"
        elif "空间直线" in combined or "空间点" in combined or ("立体几何" in combined and "空间向量" not in combined and "简单几何体" not in combined and "球" not in combined and "柱体" not in combined and "锥体" not in combined): new_comp, new_chap = "必修三", "第 10 章 空间直线与平面"
        elif "简单几何体" in combined or "柱体" in combined or "锥体" in combined or "多面体" in combined or "球" in combined: new_comp, new_chap = "必修三", "第 11 章 简单几何体"
        elif "古典概" in combined or "随机现象" in combined or ("概率" in combined and "条件概率" not in combined and "随机变量" not in combined and "分布" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 12 章 概率初步"
        elif "总体与样本" in combined or "抽样" in combined or "统计图表" in combined or ("统计" in combined and "成对" not in combined and "回归" not in combined and "列联表" not in combined and "选修" not in comp): new_comp, new_chap = "必修三", "第 13 章 统计"
        elif "红绿灯" in combined or "优惠券" in combined or "车辆转弯" in combined or "雨中行" in combined or "出租车" in combined or "家具" in combined or "登山" in combined or "包装彩带" in combined or "削菠萝" in combined or "高度测量" in combined or "外卖" in combined or "必修四" in comp: new_comp, new_chap = "必修四", "第 1 部分 数学建模活动案例"
        elif "平面直角坐标系中的直线" in combined or "直线与方程" in combined or ("直线" in combined and "空间" not in combined and "圆锥曲线" not in combined): new_comp, new_chap = "选修一", "第 1 章 平面直角坐标系中的直线"
        elif "圆锥曲线" in combined or "椭圆" in combined or "双曲线" in combined or "抛物线" in combined or ("圆" in combined and "圆锥曲线" in combined): new_comp, new_chap = "选修一", "第 2 章 圆锥曲线"
        elif "空间向量" in combined: new_comp, new_chap = "选修一", "第 3 章 空间向量及其应用"
        elif "数列" in combined or "等差数列" in combined or "等比数列" in combined or "数学归纳法" in combined: new_comp, new_chap = "选修一", "第 4 章 数列"
        elif "导数" in combined: new_comp, new_chap = "选修二", "第 5 章 导数及其应用"
        elif "计数原理" in combined or "排列" in combined or "组合" in combined or "二项式" in combined: new_comp, new_chap = "选修二", "第 6 章 计数原理"
        elif "条件概率" in combined or "随机变量" in combined or "常用分布" in combined or "二项分布" in combined or "正态分布" in combined: new_comp, new_chap = "选修二", "第 7 章 概率初步（续）"
        elif "成对数据" in combined or "线性回归" in combined or "列联表" in combined or "独立性检验" in combined or "回归" in combined: new_comp, new_chap = "选修二", "第 8 章 成对数据的统计分析"
        elif "刹车距离" in combined or "易拉罐" in combined or "珠穆朗玛峰" in combined or "水葫芦" in combined or "铅球" in combined or "电梯调度" in combined or "存款计划" in combined or "民生巨变" in combined or "教室里的照明" in combined or "选修三" in comp: new_comp, new_chap = "选修三", "第 1 部分 数学建模活动案例"
        else: new_comp, new_chap = "必修一", "第 1 章 集合与逻辑"

    active_v = get_active_version_code()
    if target == active_v:
        c_tree = METADATA_CACHE.get("curriculum", {})
    else:
        try:
            c_tree = load_curriculum(target)
        except ValueError:
            c_tree = {}
    
    valid_knows = c_tree.get(new_comp, {}).get(new_chap, [])
    new_know = know if know in valid_knows else ""
    return new_comp, new_chap, new_know

@app.post("/api/config/metadata")
def save_metadata_config(
    payload: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    global METADATA_CACHE
    # Validation
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求 Payload 格式错误")
        
    for field in ["question_types", "difficulties", "curriculum"]:
        if field not in payload:
            raise HTTPException(status_code=400, detail=f"元数据配置缺少核心字段: '{field}'")
            
    # Simple validate question_types and difficulties lists
    if not isinstance(payload["question_types"], list) or not isinstance(payload["difficulties"], list):
        raise HTTPException(status_code=400, detail="question_types 或 difficulties 必须是数组列表")
        
    if not isinstance(payload["curriculum"], dict):
        raise HTTPException(status_code=400, detail="curriculum 必须是字典对象")
        
    old_metadata = METADATA_CACHE
    metadata_path = Path(METADATA_FILE)
    old_file_contents = (
        metadata_path.read_text(encoding="utf-8") if metadata_path.exists() else None
    )
    file_replaced = False
    transaction_committed = False

    # Update the curriculum mirror and metadata as one compensated operation.
    try:
        source_version = get_active_version_code()
        # Detect target version
        curriculum = payload.get("curriculum", {})
        combined_chapters = ""
        for book_content in curriculum.values():
            if isinstance(book_content, dict):
                combined_chapters += " ".join(book_content.keys())
        if "第一章" in combined_chapters:
            target_version = "B"
        elif "第 1 章 集合与逻辑" in combined_chapters or "数学建模活动案例" in combined_chapters or "第 2 章 等式与不等式" in combined_chapters or "第 3 章 幂、指数与对数" in combined_chapters:
            target_version = "H"
        elif "第1章" in combined_chapters:
            target_version = "S"
        else:
            target_version = "A"

        # Incremental migration if curriculum version shifts
        if source_version != target_version:
            # Check and run incremental migration for all questions that do not have classifications for target_version
            all_questions = db.query(Question).all()
            for q in all_questions:
                target_map = db.query(QuestionCurriculum).filter(
                    QuestionCurriculum.question_id == q.id,
                    QuestionCurriculum.version_code == target_version
                ).first()
                if not target_map or not target_map.compulsory:
                    source_map = db.query(QuestionCurriculum).filter(
                        QuestionCurriculum.question_id == q.id,
                        QuestionCurriculum.version_code == source_version
                    ).first()
                    if source_map and source_map.compulsory:
                        new_comp, new_chap, new_know = route_chapter(
                            source_map.compulsory, source_map.chapter, source_map.knowledge, target_version
                        )
                        if not target_map:
                            target_map = QuestionCurriculum(
                                question_id=q.id,
                                version_code=target_version
                            )
                            db.add(target_map)
                        target_map.compulsory = new_comp
                        target_map.chapter = new_chap
                        target_map.knowledge = new_know
        # Batch update main questions table categories with target version values
        from sqlalchemy import text
        db.flush()
        db.execute(text("""
            UPDATE questions 
            SET category_compulsory = COALESCE((SELECT compulsory FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_chapter = COALESCE((SELECT chapter FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), ''),
                category_knowledge = COALESCE((SELECT knowledge FROM question_curriculums WHERE question_id = questions.id AND version_code = :v), '')
        """), {"v": target_version})

        write_private_text_atomic(
            metadata_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        file_replaced = True
        db.commit()
        transaction_committed = True
    except Exception as e:
        db.rollback()
        if not transaction_committed:
            METADATA_CACHE = old_metadata
        if file_replaced and not transaction_committed:
            try:
                if old_file_contents is None:
                    metadata_path.unlink(missing_ok=True)
                else:
                    write_private_text_atomic(metadata_path, old_file_contents)
            except OSError as restore_error:
                print(
                    "[Metadata] Failed to restore metadata after DB rollback "
                    f"(type={type(restore_error).__name__})."
                )
        raise HTTPException(status_code=500, detail=f"保存元数据失败: {str(e)}")

    # Everything below is post-commit and must not change the successful save
    # into an error response or compensate already-durable database changes.
    METADATA_CACHE = payload
    print(
        f"[Metadata] Saved new custom metadata to {METADATA_FILE} "
        f"(Detected version: {target_version})"
    )
    schedule_database_export(background_tasks, operation="save_metadata")
    return {"status": "success", "message": "元数据配置保存成功！"}

# ----------------- DB Statistics API -----------------

@app.get("/api/stats")
def get_db_stats(db: Session = Depends(get_db)):
    try:
        total = db.query(Question).count()
        normal = db.query(Question).filter(Question.difficulty == "normal").count()
        easy_error = db.query(Question).filter(Question.difficulty == "easy_error").count()
        challenge = db.query(Question).filter(Question.difficulty == "challenge").count()
        qiangji = db.query(Question).filter(Question.difficulty == "qiangji").count()
        
        # Cascaded Stage & Chapter Counts
        rows = db.query(
            Question.category_compulsory,
            Question.category_chapter
        ).all()
        
        comp_chap_stats = {}
        for comp, chap in rows:
            comp_val = comp or "未分类"
            chap_val = chap or "未分章节"
            if comp_val not in comp_chap_stats:
                comp_chap_stats[comp_val] = {}
            if chap_val not in comp_chap_stats[comp_val]:
                comp_chap_stats[comp_val][chap_val] = 0
            comp_chap_stats[comp_val][chap_val] += 1
            
        def compulsory_sort_key(comp_name: str):
            if not comp_name or comp_name == "未分类":
                return (99, 99, comp_name or "")
            num_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6}
            is_comp = 0 if ("必修" in comp_name and "选" not in comp_name) else 1
            num = 99
            for k, v in num_map.items():
                if k in comp_name:
                    num = min(num, v)
            return (is_comp, num, comp_name)

        sorted_comp_chap_stats = {
            k: comp_chap_stats[k]
            for k in sorted(comp_chap_stats.keys(), key=compulsory_sort_key)
        }
            
        # Daily additions in local time (UTC+8)
        date_rows = db.query(Question.created_at).all()
        daily_adds = {}
        for (created_at,) in date_rows:
            if created_at:
                # Convert UTC to UTC+8 local time
                local_time = created_at + datetime.timedelta(hours=8)
                date_str = local_time.strftime("%Y-%m-%d")
                daily_adds[date_str] = daily_adds.get(date_str, 0) + 1

        return {
            "status": "success",
            "total_count": total,
            "normal_count": normal,
            "easy_error_count": easy_error,
            "challenge_count": challenge,
            "qiangji_count": qiangji,
            "compulsory_chapter_counts": sorted_comp_chap_stats,
            "daily_adds": daily_adds
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"获取统计数据失败: {str(e)}"},
            status_code=500
        )

@app.get("/api/categories")
def list_categories(subject: str = "math", db: Session = Depends(get_db)):
    """学段 → 章节 → 小节 三级级联数据（题库筛选栏与录入表单共用）。

    基线目录树按学科取：数学用「设置 - 大纲」里的活动大纲，物理挂教科版、
    化学挂人教版。三科共用一套数学树的时代结束了 —— 那会让物理题的学段下拉框
    里全是数学章节，选出来的分类写进库后与教材完全对不上。

    数据库里的自定义分类仍会合并进来，但只合并**当前学科**的题：跨学科合并会
    把数学的章节名塞进物理下拉框。
    """

    subject_value = normalize_subject(subject)
    base_tree = get_subject_curriculum_tree(subject_value)

    hierarchy = {}
    for comp, chapters in base_tree.items():
        hierarchy[comp] = {}
        for chap, sections in chapters.items():
            hierarchy[comp][chap] = list(sections)
            
    # Also fetch any custom entries from DB
    results = (
        db.query(
            Question.category_compulsory,
            Question.category_chapter,
            Question.category_knowledge,
        )
        .filter(Question.subject == subject_value)
        .distinct()
        .all()
    )
    
    for comp, chap, know in results:
        if not comp:
            continue
        if comp not in hierarchy:
            hierarchy[comp] = {}
        if not chap:
            continue
        if chap not in hierarchy[comp]:
            hierarchy[comp][chap] = []
        if know and know not in hierarchy[comp][chap]:
            hierarchy[comp][chap].append(know)
            
    return hierarchy

# ----------------- AI Auto-Classification API -----------------

@app.post("/api/ai/classify")
def ai_classify(
    content: str = Form(...),
    use_free_model: str = Form("false"),
    subject: str = Form("math"),
):
    use_free = use_free_model.lower() in ("true", "1", "yes")
    classify_decision = decide_classify_model(use_free)
    classify_model = classify_decision["raw_model"]
    provider = classify_decision["provider"]
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.credential_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider_name})，无法自动智能分类！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        # 分类提示词必须吃当前学科的目录树：给物理题喂数学大纲，AI 只会
        # 硬套出「必修一 / 1. 集合与常用逻辑用语」这种对不上的结果。
        system_instructions = build_classification_system_prompt(
            get_subject_curriculum_tree(subject)
        )
        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": f"题目内容:\n{content}"}
            ],
            "response_format": {
                "type": "json_object"
            },
            "temperature": 0.2,
            "max_tokens": 512
        }
        
        # Only add thinking if using a DeepSeek model or DeepSeek base URL, excluding legacy models that don't support it
        is_deepseek = ("deepseek" in model_name.lower() or "deepseek" in api_base.lower()) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
        if is_deepseek and provider.reasoning_effort in {None, "default"}:
            data["thinking"] = {
                "type": "disabled"
            }
        data = inject_reasoning_effort(data, provider.reasoning_effort)
        data = apply_bailian_thinking_policy(
            data,
            provider_code=provider.provider_code,
            model_name=model_name,
            task="classify",
        )
        
        response = post_chat_completion(
            provider,
            data,
            timeout=30,
            provider_name=provider_name,
        )
            
        res_json = response.json()
        ai_message = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        
        # Strip potential markdown formatting if returned
        if ai_message.startswith("```"):
            lines = ai_message.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            ai_message = "\n".join(lines).strip()
            
        result = json.loads(ai_message)
        curr = get_current_curriculum()

        # 题型（细粒度）：优先用 AI 的 question_type，否则回退到结构规则识别
        VALID_TYPES = {"single_choice", "multi_choice", "fill_in_blank", "detailed_answer"}
        raw_type = result.get("question_type", "")
        if raw_type in VALID_TYPES:
            question_type = raw_type
        else:
            structured_form = detect_structured_question_form(content)
            question_type = structured_form or normalize_ai_question_form(raw_type)
            if question_type == "choice":
                question_type = "single_choice"
            if question_type not in VALID_TYPES:
                question_type = "single_choice"

        # 难度（唯一事实来源：mathbank.curriculums.DIFFICULTY_VALUES）
        difficulty = normalize_difficulty(result.get("difficulty", ""))

        # 来源：自动归一（与一次性批量归一、手动保存共用同一映射表）
        # allow_ai：本就在 AI 链路上，规则无力规整的全新来源交给 LLM 按规约加工一次
        source = normalize_source(result.get("source"), allow_ai=True)

        # 学段 / 章节：校验必须存在于 curriculum，否则回退到第一个可用学段/章节
        # 兼容模型可能输出的旧字段名 category_compulsory / category_chapter
        compulsory = result.get("compulsory") or result.get("category_compulsory") or ""
        chapter = result.get("chapter") or result.get("category_chapter") or ""
        is_fallback = False
        raw_recommendation = ""
        if not (compulsory in curr and chapter in curr.get(compulsory, {})):
            raw_recommendation = f"{compulsory} -> {chapter}"
            is_fallback = True
            if curr:
                if compulsory in curr:
                    chapter = next(iter(curr[compulsory]), "")
                else:
                    compulsory = next(iter(curr), "必修一")
                    chapter = next(iter(curr[compulsory]), "")
            else:
                compulsory, chapter = "必修一", "1. 集合与常用逻辑用语"

        # 小节：必须存在于 curriculum[compulsory][chapter]
        category_knowledge = result.get("category_knowledge", "")
        if compulsory in curr and chapter in curr.get(compulsory, {}):
            valid_sections = curr[compulsory][chapter]
            if category_knowledge not in valid_sections:
                category_knowledge = chapter  # 默认小节=章节
        else:
            category_knowledge = chapter

        # 知识点 / 解题方法（支持数组或字符串）
        knowledge_list = result.get("knowledge_list", []) or []
        solve_method = result.get("solve_method", []) or []
        if isinstance(knowledge_list, str):
            knowledge_list = [x.strip() for x in re.split(r"[,，;；\n]+", knowledge_list) if x.strip()]
        if isinstance(solve_method, str):
            solve_method = [x.strip() for x in re.split(r"[,，;；\n]+", solve_method) if x.strip()]

        # 关联章节（融合题）：主分类之外的额外章节，需校验存在性后保留
        related_chapters = result.get("related_chapters", []) or []
        if isinstance(related_chapters, str):
            try:
                related_chapters = json.loads(related_chapters)
            except Exception:
                related_chapters = []
        if not isinstance(related_chapters, list):
            related_chapters = []
        valid_related = []
        seen_rel = set()
        for item in related_chapters:
            if not isinstance(item, dict):
                continue
            rc = (item.get("compulsory") or "").strip()
            rch = (item.get("chapter") or "").strip()
            rk = (item.get("knowledge") or "").strip()
            if not rch:
                continue
            # 章节必须存在于当前教材目录，否则丢弃（避免脏数据）
            if rc in curr and rch in curr.get(rc, {}):
                if not rk or rk not in curr[rc][rch]:
                    rk = rch
            else:
                continue
            key = (rc, rch, rk)
            if key in seen_rel:
                continue
            seen_rel.add(key)
            valid_related.append({"compulsory": rc, "chapter": rch, "knowledge": rk})

        return {
            "status": "success",
            "question_type": question_type,
            "difficulty": difficulty,
            "source": source,
            "compulsory": compulsory,
            "chapter": chapter,
            "category_knowledge": category_knowledge,
            "knowledge_list": knowledge_list,
            "solve_method": solve_method,
            "related_chapters": valid_related,
            "is_fallback": is_fallback,
            "raw_recommendation": raw_recommendation,
        }
            
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"AI 智能分类失败: {str(e)}"},
            status_code=500
        )

# ----------------- LaTeX Batch Paper Import APIs -----------------

@app.post("/api/upload/tex-source")
def upload_tex_source(file: UploadFile = File(...)):
    """Decode and inspect a single TeX source file without executing it."""
    filename = file.filename or ""
    if not filename.lower().endswith(".tex"):
        return JSONResponse(
            content={"status": "error", "message": "上传文件格式不正确，必须为 .tex 格式！"},
            status_code=400,
        )
    try:
        content = read_stream_limited(file.file, MAX_TEX_BYTES)
        result = decode_and_prepare_tex(content)
        return {
            "status": "success",
            "source": result["source"],
            "title": result["title"],
            "diagnostics": result["diagnostics"],
        }
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "TeX 文件过大，请上传 5MB 以内的单文件试卷源码！"},
            status_code=413,
        )
    except ValueError as exc:
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=400)


@app.post("/api/upload/batch")
def upload_batch_images(files: List[UploadFile] = File(...)):
    try:
        if not files or len(files) > 20:
            return JSONResponse(
                content={"status": "error", "message": "配图数量必须为 1 至 20 张。"},
                status_code=400,
            )
        validated = []
        total_bytes = 0
        seen_names: set[str] = set()
        for file in files:
            original_name = tex_asset_basename(file.filename or "image") or "image"
            normalized_name = original_name.casefold()
            if normalized_name in seen_names:
                raise ValueError(f"存在重名配图 {original_name}，请保留一张或先重命名。")
            seen_names.add(normalized_name)
            try:
                raw = read_stream_limited(file.file, MAX_SINGLE_IMAGE_BYTES)
            except UploadTooLargeError as exc:
                raise ValueError(f"图片 {original_name} 超过 10MB。") from exc
            total_bytes += len(raw)
            if total_bytes > 50 * 1024 * 1024:
                raise ValueError("配图总大小不能超过 50MB。")
            try:
                normalized = normalize_raster_image(raw)
            except InvalidImageError as exc:
                raise ValueError(f"图片 {original_name} 不是安全的栅格图片。") from exc
            validated.append((original_name, normalized))

        mapping = {}
        for original_name, normalized in validated:
            filename = f"{uuid.uuid4().hex}{normalized.extension}"
            filepath = os.path.join(UPLOAD_DIR, filename)
            with open(filepath, "wb") as f:
                f.write(normalized.data)
            relative_path = f"/{UPLOAD_DIR_REL}/{filename}"
            mapping[original_name] = relative_path
            
        return {
            "status": "success",
            "mapping": mapping
        }
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        return JSONResponse(
            content={"status": "error", "message": f"批量图片上传失败: {str(e)}"},
            status_code=400
        )


def _build_parse_payload(provider, model_name, system_instructions, user_content, max_output_tokens):
    """按给定 provider/model 构造拆题请求体（含 DeepSeek thinking / 推理强度 / 百炼策略）。"""
    data = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": max_output_tokens,
    }
    is_deepseek = (
        "deepseek" in model_name.lower() or "deepseek" in (provider.api_base or "").lower()
    ) and "deepseek-chat" not in model_name.lower() and "deepseek-reasoner" not in model_name.lower()
    if is_deepseek and provider.reasoning_effort in {None, "default"}:
        data["thinking"] = {"type": "disabled"}
    data = inject_reasoning_effort(data, provider.reasoning_effort)
    data = apply_bailian_thinking_policy(
        data,
        provider_code=provider.provider_code,
        model_name=model_name,
        task="parse",
    )
    return data


def _extract_questions_list(parsed_data):
    """从 AI 返回的 JSON 中提取题目列表（兼容 {"questions":[...]} / {"data":[...]} / 裸数组 / 单对象）。"""
    if isinstance(parsed_data, dict):
        if "questions" in parsed_data and isinstance(parsed_data["questions"], list):
            return parsed_data["questions"]
        elif "data" in parsed_data and isinstance(parsed_data["data"], list):
            return parsed_data["data"]
        else:
            parsed_questions = None
            for key, val in parsed_data.items():
                if isinstance(val, list):
                    parsed_questions = val
                    break
            if parsed_questions is None:
                parsed_questions = [parsed_data]
            return parsed_questions
    elif isinstance(parsed_data, list):
        return parsed_data
    else:
        raise Exception("AI 返回的 JSON 格式不正确，期望是一个数组或包含 questions 列表的对象。")


def _parse_single_chunk(user_content, decision, system_instructions, max_output_tokens, timeout, paid_timeout=300, chunk_markdown=None):
    """解析单个文本块；含免费模型超时/连接失败自动回退付费（回退会改写 decision 供后续块复用）。"""
    provider = decision["provider"]
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label
    if not api_key:
        raise ValueError(f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。")

    payload = _build_parse_payload(provider, model_name, system_instructions, user_content, max_output_tokens)
    # 主调用超时：免费模型用短超时（FREE_PARSE_TIMEOUT）以便及时回退；
    # 纯付费场景（无免费模型）则直接给足付费超时（PAID_PARSE_TIMEOUT）。
    primary_timeout = paid_timeout if not decision.get("used_free") else timeout
    try:
        response = post_chat_completion(provider, payload, timeout=primary_timeout, provider_name=provider_name)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
        paid_provider = decision.get("paid_provider")
        if decision.get("used_free") and paid_provider is not None:
            print(
                f"[Parse Router] 免费模型 {provider_name} 请求失败"
                f"（{type(net_err).__name__}），自动回退到付费模型重试..."
            )
            provider = paid_provider
            decision["provider"] = paid_provider
            api_key = provider.api_key
            api_base = provider.api_base
            model_name = provider.model_name
            provider_name = provider.provider_label
            payload = _build_parse_payload(provider, model_name, system_instructions, user_content, max_output_tokens)
            response = post_chat_completion(provider, payload, timeout=paid_timeout, provider_name=provider_name)
            decision["used_free"] = False
            decision["reason"] = (decision.get("reason") or "") + "；免费模型超时已自动回退付费"
        else:
            raise

    res_json = response.json()
    # L2 截断感知：把 finish_reason 和 usage 透出到上游，由 _parse_chunks_to_questions 决定是否续拆。
    finish_reason = ""
    try:
        finish_reason = (res_json.get("choices") or [{}])[0].get("finish_reason", "") or ""
    except Exception:
        finish_reason = ""
    completion_tokens = None
    try:
        usage = res_json.get("usage") or {}
        if isinstance(usage, dict):
            completion_tokens = usage.get("completion_tokens")
    except Exception:
        completion_tokens = None
    raw_ai_text = res_json["choices"][0]["message"]["content"].strip()
    # 抓 last_q_no + was_truncated 两条关键信号（即使 parse_ai_json 兜底成功也要记录）
    was_truncated, last_q_no = detect_truncation_signals(raw_ai_text)
    # finish_reason=length 强信号覆盖：raw_decode 成功也按截断处理（输出顶到 max_tokens）
    if finish_reason == "length" and not was_truncated:
        was_truncated = True
    parsed_data = parse_ai_json(raw_ai_text, raw_markdown=chunk_markdown if chunk_markdown is not None else user_content)
    questions = _extract_questions_list(parsed_data)
    return ChunkParseResult(
        questions=questions,
        was_truncated=was_truncated,
        last_q_no=last_q_no,
        finish_reason=finish_reason,
        completion_tokens=completion_tokens,
        raw_text=raw_ai_text,
    )


# 多块拆题时附带给下一段的「上一段末尾」字符数。用于让模型判断本段开头是否是
# 上一道题的延续（跨页/跨块题目兜底）。过大浪费 token，过小看不出上下文。
CHUNK_CONTEXT_CHARS = 800


# ---------------------------------------------------------------------------
# 拆解质量契约：L2/L3 防御所需的结构化返回值
# ---------------------------------------------------------------------------
@dataclass
class ChunkParseResult:
    """单块解析结果：题目列表 + 截断感知元数据。

    - ``questions``：本次解析出的题目列表（按模型输出顺序，可能含已截断回收的）。
    - ``was_truncated``：本次输出是否被检测到截断（JSON 不闭合 / finish_reason=length）。
    - ``last_q_no``：从已回收题目中识别的最大题号，供续拆定位；无信号时为 None。
    - ``finish_reason``：模型原始 finish_reason（"length"/"stop"/其他）。
    - ``completion_tokens``：模型声明的实际输出 token 数，用于排查"假截断"等问题。
    - ``raw_text``：原始 AI 输出文本（仅诊断用，不返回前端）。
    """
    questions: List[Any]
    was_truncated: bool = False
    last_q_no: Optional[int] = None
    finish_reason: Optional[str] = None
    completion_tokens: Optional[int] = None
    raw_text: str = ""


@dataclass
class DocParseResult:
    """整卷拆解结果：题目列表 + 拆解质量（透传到 DOCUMENT_TASKS，供前端徽章展示）。

    - ``expected_count``：基于题目区题号识别估算的应有题数（0 表示无法可靠估算）。
    - ``parsed_count``：本次实际拆出的题目数（dedupe 前）。
    - ``truncated_blocks``：本次触发自动续拆的块数。
    - ``missing_ranges``：疑似漏掉的题号区间列表 ``[(from, to), ...]``。
    - ``retries``：触发的续拆总次数。
    """
    questions: List[Any]
    expected_count: int = 0
    parsed_count: int = 0
    truncated_blocks: int = 0
    missing_ranges: List[Tuple[int, int]] = field(default_factory=list)
    retries: int = 0


def _build_condensed_parse_system_prompt(extra_system_note: str = "", formula_lock: bool = True, generate_answers_bool: bool = False) -> str:
    """多块拆题时，除首块外后续块使用的精简系统提示（Plan C）。

    完整系统提示约 3.7K 字，多块文档每块重复发送浪费明显。后续块改用本精简版，
    仅保留输出 JSON 结构、字段枚举与「公式必须原样保留、不得展开」等关键约束，
    整体下降约 75% 系统提示 token，同时不影响首块质量。
    formula_lock 必须与首块一致：DOCX/TeX 锁定路径用 True（保留 [[Mn]] 协议），
    PDF 未锁定路径用 False（要求用 $...$ 主动包裹，禁止 [[Mn]]）。
    generate_answers_bool 必须与首块一致：精简提示若不带答案规则，后续块的模型会
    按默认教研专家人设把解析全部写出来，落库前又被「未勾选生成 → 清空无
    [EXTRACTED_ORIGINAL] 标记的解析」整段抹掉，白烧输出 token 且易撞截断漏题。
    """
    base = (
        "你是资深高中数学教研专家。正在解析一份多段试卷的其中一段（首段已给出完整规则，"
        "此处仅给精简提醒）。必须且只能输出严格合法 JSON，字段为 questions 数组，每条题目含：\n"
        "- content：纯净题干（去掉原卷大题号，保留 LaTeX 与图片占位符 [插图待补: 图N]"
        + ("、公式占位符 [[Mn]]" if formula_lock else "")
        + "）\n"
        "- answer_markdown：答案与解析\n"
        "- question_type：single_choice / multi_choice / fill_in_blank / detailed_answer\n"
        "- compulsory、chapter：学段 / 章节名称\n"
        "- difficulty：easy_error / normal / challenge / qiangji\n"
        "- source：出处信息或 null\n"
        "- knowledge_list：字符串数组；solve_method：单个字符串；related_chapters：字符串数组；tags：字符串数组\n"
        "- referenced_images：字符串数组（把 [插图待补: 图N] 列入）\n"
        "- answer_belongs_to：仅分离模式需要，否则 null\n"
        "【关键保留规则】\n"
    )
    if formula_lock:
        formula_section = (
            "1. 公式：输入中形如 `<mathbank-math id=\"M1\">$...$</mathbank-math>` 的公式，输出时必须原样替换为且仅一次 `[[M1]]`，绝不许输出公式本身的 LaTeX、绝不许展开 `[[M1]]`。\n"
        )
    else:
        formula_section = (
            "1. 公式：本卷公式未做锁定标记，你必须在输出时把每一个数学公式/符号/表达式用 `$...$`（行内）或 `$$...$$`（独立）完整包裹，原样保留公式本体与 `$` 定界符，绝不许丢弃 `$`，绝不许使用 `[[Mn]]` 占位符。\n"
        )
    base = base + formula_section + (
        "2. 图片：输入中的 `[插图待补: 图N]` 必须原样保留，并列入 referenced_images，不得展开为 URL 或改写。\n"
        "3. 选择题选项统一 `\\begin{choices}\\item...\\end{choices}` 且每项独立成行；填空题用 `\\fillin`；加粗用 `\\textbf{}`（禁双星号）；严禁用单个 `*` 把几何顶点/变量做成 Markdown 斜体（如 `*X*`），一律用 `$...$` 包裹（如 `$X$`）。\n"
        "4. 完整保留 tabular/array/matrix/cases/aligned 等数学与表格结构。\n"
        "5. 字符串内换行用 `\\n`，LaTeX 反斜杠写成 `\\\\`；不要包裹 ```json 代码块。只解析本段内的题目。\n"
        "6. 输入中若含 `<上文片段>...</上文片段>`，它只是上一段的末尾，唯一用途是帮你判断本段开头"
        "是否是上一道题的延续（例如上一段末尾停在「（1）」、本段开头是「（2）」，说明它们同属一道大题）。"
        "严禁把上文片段中的任何内容输出成题目，也严禁据此凭空补全；只有 `<本段正文>` 内的内容才是你要解析的题目。\n"
    )
    # 答案规则必须与首块（完整提示）共用同一措辞，否则后续块会「生成了又被清空」。
    base = base + build_answer_rule(generate_answers_bool) + "\n"
    if extra_system_note:
        base = base + extra_system_note
    return base


# ---------------------------------------------------------------------------
# 落库前净化：把模型用 Markdown 单星号斜体包裹的变量（*X*、*ABC*、*x₀* 等）
# 还原为 LaTeX 数学模式 $...$，并修正 PDF 提取产生的 cp1252 误码字符。
# 提示词已加护栏禁止单星号斜体（治本），本函数作为兜底（兜住历史与偶发）。
# 注意：¥（日元）在中文数学应用题里可能合法出现，故实时净化不含 ¥→∞，
#       仅一次性存量清洗脚本对确认的脏题单独处理。
# ---------------------------------------------------------------------------
_GARBLE_MAP_LIVE = {
    "\u00a3": "\u2264",   # £ → ≤
    "\u00b4": "\u00d7",   # ´ → ×
    "\u00ce": "\u2208",   # Î → ∈
    "\u00a2": "\u2032",   # ¢ → ′ (prime)
    "\u00ae": "\u2192",   # ® → →
}

_MATHY_INNER = (
    r"[A-Za-z0-9"
    r"α-ωΑ-Ω"                                   # 希腊字母
    r"₀₁₂₃₄₅₆₇₈₉"                              # 下标数字
    r"⁰¹²³⁴⁵⁶⁷⁸⁹"                              # 上标数字
    r"¼½¾"                                      # 分数
    r"±×÷·′″°∞≤≥≠≈≡∈∉∪∩⊂⊆∑∏√∂∫"               # 数学符号
    r"\s\^\_\+\-\*\/\(\)\[\]\.\,\;\:\!\?\=]"    # 运算符/标点/空白
)
_MATHY_PATTERN = re.compile(r"^" + _MATHY_INNER + r"*$")
_STAR_RE = re.compile(r"(?<!\*)\*([^*$\\\n]+)\*(?!\*)")


def _clean_star_emphasis_in_text(text, garble_map):
    """把文本中的 *变量* 斜体还原为 $变量$，并修正误码字符（仅处理 $...$ 数学模式之外的部分）。"""
    if not text:
        return text
    for bad, good in garble_map.items():
        if bad in text:
            text = text.replace(bad, good)
    if "*" not in text:
        return text
    # 仅在「非数学模式」（$...$ 之外）处理单星号斜体，避免破坏已有公式。
    parts = text.split("$")
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
            continue

        def _repl(m):
            inner = m.group(1).strip()
            if inner and _MATHY_PATTERN.match(inner):
                return "$" + inner + "$"
            return m.group(0)

        out.append(_STAR_RE.sub(_repl, part))
    return "$".join(out)


def sanitize_question_markdown(question, garble_map=_GARBLE_MAP_LIVE):
    """落库前净化单道题的 content / answer_markdown：修正误码 + 单星号斜体转数学模式。"""
    for field in ("content", "answer_markdown"):
        val = question.get(field)
        if isinstance(val, str) and ("*" in val or any(b in val for b in garble_map)):
            new_val = _clean_star_emphasis_in_text(val, garble_map)
            if new_val != val:
                question[field] = new_val
    return question


def _report_parse_progress(progress_callback, done, total):
    """向解析任务回报分块进度。回调本身出错不得影响解析主流程。"""

    if not progress_callback:
        return
    try:
        progress_callback(done, total)
    except Exception:
        pass


def _parse_chunks_to_questions(document_text, decision, system_instructions, generate_answers_bool, separated_mode=False, progress_callback=None, extra_system_note: str = "", formula_lock: bool = True):
    """将（可能超长的）试卷文本按题号边界分块，逐块调用 LLM 解析，合并去重。

    四层防御的核心调度器（L1 + L2 + L3）：

    - **L1 事前**：先按字符切（split_markdown_into_question_chunks），再按题数
      和字符硬上限再分批（split_chunks_by_question_count），让单次输出可控。
    - **L2 事中**：调用 _parse_single_chunk 拿到 ChunkParseResult，检测 was_truncated。
      若截断，从「last_q_no+1」起在原 chunk 切子段续拆，最多 3 次。
    - **L3 事后**：合并后预估题目区题数，对比实际拆得题数，记录 missing_ranges。

    单块失败不会拖垮整卷，会跳过并继续；所有块都失败才抛错。

    返回 :class:`DocParseResult`：题目列表 + 拆解质量统计，供前端徽章展示。

    progress_callback: 可选回调 (done, total)，每完成/跳过一段即调用一次。
    """
    max_output_tokens = 65536
    FREE_PARSE_TIMEOUT = 90   # 免费模型（SiliconFlow Qwen3-VL-8B-Instruct）对复杂数学题易 ReadTimeout；
                             # 正常返回通常 <60s，90s 足够其完成，超时即视为卡死立即回退付费
    PAID_PARSE_TIMEOUT = 300 # 付费回退（deepseek-flash）给足 5 分钟，避免复杂大题二次超时丢块
    MAX_TRUNCATION_RETRIES = 3  # 单块截断续拆上限（治本 L2）

    chunks = split_markdown_into_question_chunks(document_text)
    # 治本：字符未超阈值时，若题目区题数过多，单次输出仍会顶到模型 max_tokens
    # 上限被硬截断（实测 19 题卷只回收前 10 题）。按题数+字符硬上限再分批，让每次
    # LLM 调用的输出体量可控、不截断。题号识别不到的块由字符切分兜底。
    chunks = split_chunks_by_question_count(chunks, max_questions=DEFAULT_MAX_QUESTIONS)
    total = len(chunks)

    # L2 续拆辅助：从「last_q_no」起在原 chunk 中切子段，供 _parse_single_chunk 复用。
    def _slice_tail_after_q_no(chunk_text: str, after_q_no: int) -> Optional[str]:
        """在 chunk 中找到「题号 > after_q_no」的第一个题号位置，取到末尾。"""
        seq = _question_number_positions(chunk_text)
        for num, pos in seq:
            if num > after_q_no:
                return chunk_text[pos:]
        return None

    all_questions: List[Any] = []
    total_retries = 0
    truncated_blocks = 0

    def _process_chunk(chunk_text: str, idx: int, total_chunks: int) -> List[Any]:
        """处理单个块：构造 user_content + 处理续拆。返回本块（含续拆）的所有题。"""
        nonlocal total_retries, truncated_blocks
        # 跨页/跨块兜底：把上一段末尾作为「上文片段」附上，让模型能判断本段开头
        # 是否是上一道题的延续（如上一块末尾停在「（1）」、本块开头是「（2）」），
        # 避免把半道题当成完整题输出。chunk_markdown 仍传原始 chunk，保持诊断一致。
        context_block = ""
        if idx > 0:
            prev = chunks[idx - 1]
            tail = prev[-CHUNK_CONTEXT_CHARS:] if len(prev) > CHUNK_CONTEXT_CHARS else prev
            if tail.strip():
                context_block = (
                    "<上文片段 仅用于判断题目的开头是否完整，严禁把其中的内容作为题目输出>\n"
                    f"{tail}\n"
                    "</上文片段>\n\n"
                )
        user_content = (
            f"[这是试卷的第 {idx + 1}/{total_chunks} 段，请只解析本段内的题目，"
            f"忽略其它段落；返回格式仍为 {{\"questions\": [...]}}。]\n\n"
            f"{context_block}"
            f"<本段正文 这才是你要解析的内容>\n{chunk_text}\n</本段正文>"
        )
        chunk_system = system_instructions if idx == 0 else _build_condensed_parse_system_prompt(
            extra_system_note, formula_lock, generate_answers_bool
        )
        # 首次解析
        result: ChunkParseResult = _parse_single_chunk(
            user_content, decision, chunk_system, max_output_tokens,
            FREE_PARSE_TIMEOUT, paid_timeout=PAID_PARSE_TIMEOUT, chunk_markdown=chunk_text,
        )
        collected = list(result.questions)
        # L2 截断自动续拆：若首次返回被截断，从 last_q_no+1 起切子段重试
        retries = 0
        current_chunk = chunk_text
        last_q = result.last_q_no
        while result.was_truncated and retries < MAX_TRUNCATION_RETRIES and last_q is not None:
            tail = _slice_tail_after_q_no(current_chunk, last_q)
            if not tail or not tail.strip():
                # 子段为空（题号已切走），不再续拆
                break
            retries += 1
            total_retries += 1
            truncated_blocks += 1
            sub_user = (
                f"[这是试卷的第 {idx + 1}/{total_chunks} 段续拆 {retries}/{MAX_TRUNCATION_RETRIES}，"
                f"刚才输出被截断在第 {last_q} 题，本次只解析第 {last_q + 1} 题起的剩余题目，"
                f"全部完整输出不要省略；返回格式仍为 {{\"questions\": [...]}}。]\n\n"
                f"<本段子段 这才是你要解析的内容>\n{tail}\n</本段子段>"
            )
            print(
                f"[Parse Chunking] 第 {idx + 1}/{total_chunks} 段检测到截断（last_q={last_q}），"
                f"自动续拆 {retries}/{MAX_TRUNCATION_RETRIES}（子段 {len(tail)} 字符）...",
                flush=True,
            )
            try:
                result = _parse_single_chunk(
                    sub_user, decision, chunk_system, max_output_tokens,
                    FREE_PARSE_TIMEOUT, paid_timeout=PAID_PARSE_TIMEOUT, chunk_markdown=tail,
                )
                collected.extend(result.questions)
                last_q = result.last_q_no
            except Exception as exc:
                print(
                    f"[Parse Chunking] 第 {idx + 1}/{total_chunks} 段续拆 {retries}/{MAX_TRUNCATION_RETRIES} 失败："
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                break
        return collected

    if total <= 1:
        try:
            qs = _process_chunk(chunks[0] if chunks else document_text, 0, 1)
        except Exception as exc:
            print(f"[Parse Chunking] 段解析失败: {type(exc).__name__}: {exc}", flush=True)
            raise
        for q in qs:
            sanitize_question_markdown(q)
        all_questions.extend(qs)
        _report_parse_progress(progress_callback, 1, 1)
        final = dedupe_questions(all_questions)
        expected = _estimate_question_count(document_text)
        missing = _compute_missing_ranges(expected, len(final))
        if missing:
            print(
                f"[Parse Chunking] 预估题目区约 {expected} 题，实际拆得 {len(final)} 题，"
                f"疑似漏题区间: {missing}（已触发截断续拆 {truncated_blocks} 次/总重试 {total_retries} 次）",
                flush=True,
            )
        return DocParseResult(
            questions=final,
            expected_count=expected,
            parsed_count=len(final),
            truncated_blocks=truncated_blocks,
            missing_ranges=missing,
            retries=total_retries,
        )

    print(f"[Parse Chunking] 文档较长，已切分为 {total} 段逐段解析。")
    for idx, chunk in enumerate(chunks):
        try:
            qs = _process_chunk(chunk, idx, total)
            for q in qs:
                sanitize_question_markdown(q)
            all_questions.extend(qs)
            print(f"[Parse Chunking] 第 {idx + 1}/{total} 段解析完成，本段 {len(qs)} 题。")
        except Exception as exc:
            print(f"[Parse Chunking] 第 {idx + 1}/{total} 段解析失败，跳过该段：{type(exc).__name__}: {exc}")
            continue
        finally:
            # 放在 finally 中：即使该段走 continue 跳过，进度也照常回报。
            _report_parse_progress(progress_callback, idx + 1, total)

    if not all_questions:
        raise Exception("所有分块均解析失败，无法拆解试卷。")
    final = dedupe_questions(all_questions)
    # L3 校验兜底：分批+续拆后仍比对「题目区预估题数」与「实际拆得题数」，
    # 差异计入 missing_ranges，供前端徽章展示。
    expected = _estimate_question_count(document_text)
    missing = _compute_missing_ranges(expected, len(final))
    if missing:
        print(
            f"[Parse Chunking] 预估题目区约 {expected} 题，实际拆得 {len(final)} 题，"
            f"疑似漏题区间: {missing}（已触发截断续拆 {truncated_blocks} 次/总重试 {total_retries} 次）",
            flush=True,
        )
    return DocParseResult(
        questions=final,
        expected_count=expected,
        parsed_count=len(final),
        truncated_blocks=truncated_blocks,
        missing_ranges=missing,
        retries=total_retries,
    )


def _compute_missing_ranges(expected: int, parsed: int) -> List[Tuple[int, int]]:
    """根据 expected/parsed 估算 missing 题号区间（粗略，连续段合并）。

    仅返回 ``[(from, to), ...]``，不去重已拆出的题号（dedupe 前已合并）。
    当前简化版：若 expected > parsed，标记 [parsed+1, expected] 为单个区间。
    零 token 成本，仅用于 L3 用户可见告警。

    实际逻辑委托给 :func:`mathbank.paper_chunking.compute_missing_ranges`，
    保证 main.py 顶层 OOM 时该函数仍可被独立测试。
    """
    from mathbank.paper_chunking import compute_missing_ranges as _impl
    return _impl(expected, parsed)


def _estimate_question_count(document_text: str) -> int:
    """在「答案区锚点」之前估算题目区题数，供拆题后校验是否漏题。

    - 分离式文档（题目在前、解析在后）：截到首个答案锚点，只统计题干区题号；
    - 纯题目文档（无锚点）：统计全文题号。
    用 :func:`_question_number_positions` 的「递增题号」识别，规避表格/选项干扰。
    返回 0 表示无法可靠估算（此时不做漏题告警）。
    """
    if not document_text:
        return 0
    t = document_text
    ans = _ANSWER_ZONE_ANCHORS.search(t)
    if ans:
        t = t[: ans.start()]
    return len(_question_number_positions(t))


# ---------------------------------------------------------------------------
# 分离式文档自动识别（零 token 成本）
# 旧版靠前端「题目与解析分离」开关手动指定；现改为后端在拆题前对提取文本跑一次
# 本地启发式，自动判断是否「题干区在前、解析区在后」结构，从而去掉该按钮。
# ---------------------------------------------------------------------------
# 裸锚点「解析」必须排除数学专有名词，否则「解析式 / 解析几何 / 解析法」会被
# 当成答案区标题。这类词在函数、圆锥曲线题的题干里极常见（实测成都七中高一期中
# 卷题干出现 2 次「解析式」，直接把 head_hits 从 2 顶到 3 导致漏判）。
_ANSWER_ZONE_ANCHORS = re.compile(
    r"(参考答案|答案与解析|答案解析|答案与评分标准|参考答案与评分细则|"
    r"试题解析|详解与答案|参考答案及解析|答案部分|解答部分|"
    r"【答案】|【解析】|参\s*考\s*答\s*案|解\s*析(?!式|几何|法))",
    re.MULTILINE,
)
_INLINE_ANSWER = re.compile(r"(【答案】|【解析】|解\s*[：:]|答案\s*[：:]|证明\s*[：:])")


def detect_separated_mode(text: str) -> bool:
    """本地启发式判断文档是否为「题干在前、解析在后」的分离结构。

    零 token 成本：仅在拆题前对后端已提取出的文本跑一次正则。

    判定思路：**答案区起点**定位 + 起点之前的内联解析覆盖率。
    命中条件：首个答案区锚点落在 15%~90%（前面有实质题干区、后面不是只剩页脚），
    且该起点之前「每题内联答案占比」< 1.0（题干区本身不带解析）。

    ---- 为什么用「首个锚点」而不是「最后一个锚点」----

    旧实现用 ``last_pos >= 0.6``，隐含假设「答案区位于文档后 40%」。这条假设在
    菁优网 / 学科网式试卷上系统性失效：这类卷子的详解区往往**比题干区还长**
    （实测成都七中高一期中卷：题干区 29.4%、答案区 70.6%，含 19 个【解答】块）。

    更糟的是旧实现还被页脚噪声「救活」——该卷唯一的靠后锚点是页脚
    「声明：试题解析著作权属菁优网所有」(98%)。一旦换一份没有这行版权声明的
    卷子，``last_pos`` 掉到 0.30，判定立刻翻车。所以边界必须取**答案区起点**。

    ---- 为什么 ``inline`` 也要限定在边界之前 ----

    ``inline_ratio`` 的语义是「题干区里有多少题自带解析」，分子分母必须是同一
    范围。旧实现 ``inline`` 全文统计（把答案区的【解答】解：全部算进分子），
    ``q_count`` 却只算边界之前 —— 分子分母范围不一致，实测把该卷 ratio 从
    0.10 抬到 1.20，直接越过 1.0 阈值。

    阈值 1.0：``inline_ratio < 1.0`` 才判分离。即「边界前每题都内联解析」
    （覆盖率 = 100%）就视为讲义而非分离卷。
    """
    t = text or ""
    n = max(1, len(t))
    hits = [(m.start() / n, m.group(0)) for m in _ANSWER_ZONE_ANCHORS.finditer(t)]
    if not hits:
        return False
    # 答案区起点 = 首个锚点位置（而非最后一个，理由见 docstring）
    answer_start = min(p for p, _ in hits)
    boundary = max(1, int(answer_start * n))
    head = t[:boundary]
    # 分子分母同范围：都只统计答案区起点之前
    inline = len(_INLINE_ANSWER.findall(head))
    q_count = len(
        re.findall(
            r"(?m)^\s*(?:\\item\s+)?(?:\d{1,3}|[一二三四五六七八九十]+)[．.、]",
            head,
        )
    )
    inline_ratio = (inline / q_count) if q_count else 0.0
    # 下界 0.15：前面必须有成规模的题干区，排除「答案区在开头」的误判
    # 上界 0.90：锚点不能只是文末页脚，后面得留得下真正的解析内容
    return 0.15 <= answer_start <= 0.90 and inline_ratio < 1.0


def parse_paper_text_internal(
    latex_content: str,
    generate_answers_bool: bool,
    separated_mode: bool = False,
    progress_callback=None,
    extra_system_note: str = "",
    formula_lock: bool = True,
    quality_out=None,
    force_paid: bool = False,
    paper_title: str = "",
) -> list:
    """内部通用函数：调用选定的 LLM 接口，将 LaTeX 试卷内容解析拆分为结构化 JSON 卡片

    progress_callback: 可选回调 (done, total)，逐段回报分块解析进度，供调用方刷新任务进度。
    force_paid: True 时跳过免费/付费难度评估，直接使用付费拆解模型（Word 链路默认开）。
    paper_title: 试卷标题。传入后会写进提示词作为「本卷来源的唯一依据」——分批拆解
        每 8 题一段，各段独立调 LLM，若让每段自行猜测来源，同一份卷会裂成多个来源。
    """
    decision = decide_parse_model(latex_content, force_paid=force_paid)
    provider = decision["provider"]
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        raise ValueError(f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。")

    system_instructions = build_pdf_parse_system_prompt(
        get_current_curriculum(), generate_answers_bool, separated_mode=separated_mode,
        formula_lock=formula_lock, paper_title=paper_title,
    )
    if extra_system_note:
        system_instructions = system_instructions + extra_system_note

    max_output_tokens = 65536
    PARSE_TIMEOUT = 300  # 免费模型对大文档响应较慢，放宽到 5 分钟（保留以备调用方参考）

    parsed_questions = _parse_chunks_to_questions(
        latex_content, decision, system_instructions, generate_answers_bool, separated_mode,
        progress_callback=progress_callback, extra_system_note=extra_system_note,
        formula_lock=formula_lock,
    )

    # L3 任务级告警透传：把拆解质量写到调用方容器，供 DOCUMENT_TASKS 透传到前端。
    # ⚠️ 解包必须无条件进行：``quality_out`` 只是可选的透传出口，不能反过来决定
    # 返回值形态——否则调用方一旦不传 quality_out（如测试或新接入的链路），
    # 就会拿到不可迭代的 DocParseResult，在下面 for 循环处抛 TypeError。
    if isinstance(parsed_questions, DocParseResult):
        if quality_out is not None:
            quality_out[0] = {
                "expected_count": parsed_questions.expected_count,
                "parsed_count": parsed_questions.parsed_count,
                "truncated_blocks": parsed_questions.truncated_blocks,
                "missing_ranges": [list(r) for r in parsed_questions.missing_ranges],
                "retries": parsed_questions.retries,
            }
        parsed_questions = parsed_questions.questions

    # 选项回溯补回：小参数模型拆题时常保留题干却丢弃 A/B/C/D 选项，
    # 此处用原文做确定性兜底（零 token），只补不覆盖，匹配不足则静默跳过。
    try:
        recovered = recover_missing_choices(parsed_questions, latex_content)
        if recovered:
            print(
                f"[Choice Recovery] 已从原文回溯补回 {recovered} 道选择题的选项。",
                flush=True,
            )
    except Exception as recovery_error:
        # 兜底逻辑失败不得影响主流程
        print(f"[Choice Recovery] 跳过（{type(recovery_error).__name__}: {recovery_error}）")

    # 强制进行静默净化：若未勾选自动生成答案，则对于没有带有 [EXTRACTED_ORIGINAL] 的解析和解答，将其强行抹平为空。
    for q in parsed_questions:
        ans = q.get("answer_markdown", "")
        if not ans:
            q["answer_markdown"] = ""
            continue
        if not generate_answers_bool:
            if "[EXTRACTED_ORIGINAL]" in ans:
                q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
            else:
                q["answer_markdown"] = ""
        else:
            q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
        
    return parsed_questions


@app.post("/api/ai/parse-paper")
def ai_parse_paper(
    latex_content: str = Form(...),
    paper_title: str = Form(""),
    image_mapping_json: str = Form("{}"),
    generate_answers: str = Form("false")
):
    generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
    decision = decide_parse_model(latex_content)
    provider = decision["provider"]
    api_key = provider.api_key
    api_base = provider.api_base
    model_name = provider.model_name
    provider_name = provider.provider_label

    if not api_key:
        return JSONResponse(
            content={
                "status": "error", 
                "message": f"未配置对应的 API Key ({provider.credential_label})，无法智能拆解试卷！请在工作台右上角设置面板进行配置。"
            },
            status_code=400
        )
        
    try:
        image_mapping = json.loads(image_mapping_json)
        if not isinstance(image_mapping, dict):
            image_mapping = {}
    except Exception:
        image_mapping = {}

    try:
        tex_result = prepare_tex_source(latex_content)
        tex_diagnostics = tex_result["diagnostics"]
        model_source, math_locks = lock_visible_math(
            tex_result["model_source"],
            "TEX_" + uuid.uuid4().hex[:16],
        )
        tex_diagnostics["math_locks_created"] = len(math_locks)
        if not paper_title.strip() and tex_result["title"]:
            paper_title = tex_result["title"]

        # 自动识别「题目与解析分离」结构（零 token），与 PDF/DOCX 路径保持一致。
        detected_separated = detect_separated_mode(model_source)
        system_instructions = build_import_parse_system_prompt(
            get_current_curriculum(), generate_answers_bool, separated_mode=detected_separated
        )

        parsed = _parse_chunks_to_questions(
            model_source, decision, system_instructions, generate_answers_bool, detected_separated,
            formula_lock=True,
        )
        # L2 改造：_parse_chunks_to_questions 现在返回 DocParseResult，这里取 .questions。
        parsed_questions = parsed.questions if isinstance(parsed, DocParseResult) else parsed
        # 回退后更新实际使用的模型名（供 eval_decision 准确上报）
        model_name = decision["provider"].model_name
        provider_name = decision["provider"].provider_label

        if not parsed_questions or not all(isinstance(question, dict) for question in parsed_questions):
            raise ValueError("AI 未返回有效的题目对象列表。")
        for index, question in enumerate(parsed_questions, start=1):
            if not isinstance(question.get("content"), str) or not question["content"].strip():
                raise ValueError(f"AI 返回的第 {index} 题缺少有效题干，已停止导入以避免静默漏题。")
            if not isinstance(question.get("referenced_images"), list):
                question["referenced_images"] = []
            # 与 PDF 拆卷链路一致：题号由后处理剥离（提示词不再承担该职责，
            # 以免模型把 A./B./C./D. 选项也当成编号误删）。
            question["content"] = _strip_leading_question_number(
                normalize_choice_options_to_latex(question["content"])
            )

        lock_report = restore_visible_math(parsed_questions, math_locks, strict=False)
        tex_diagnostics.update(lock_report)
        if "warnings" in lock_report:
            tex_diagnostics.setdefault("warnings", []).extend(lock_report["warnings"])
        tex_diagnostics["question_count_actual"] = len(parsed_questions)
        estimated_count = tex_diagnostics.get("question_count_estimate", 0)
        if estimated_count and estimated_count != len(parsed_questions):
            tex_diagnostics.setdefault("warnings", []).append(
                f"源码约识别到 {estimated_count} 道题，但模型返回 {len(parsed_questions)} 道，请重点核对是否漏题或误拆。"
            )

        for graphic_ref in tex_diagnostics.get("referenced_graphics", []):
            graphic_ref = str(graphic_ref)
            candidates = []
            for question in parsed_questions:
                content_graphics = re.findall(
                    r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}",
                    question.get("content", ""),
                )
                question_refs = content_graphics + [
                    str(value) for value in question.get("referenced_images", [])
                ]
                if any(tex_asset_references_match(graphic_ref, value) for value in question_refs):
                    candidates.append(question)
            if len(candidates) == 1 and not any(
                tex_asset_references_match(graphic_ref, existing)
                for existing in candidates[0]["referenced_images"]
            ):
                candidates[0]["referenced_images"].append(graphic_ref)
            elif not candidates:
                tex_diagnostics.setdefault("unassigned_source_images", []).append(graphic_ref)
        
        # Translate referenced_images to server paths
        for q in parsed_questions:
            # 强制进行静默净化：若未勾选自动生成答案，则对于没有带有 [EXTRACTED_ORIGINAL] 的解析和解答，将其强行抹平为空。
            ans = q.get("answer_markdown", "")
            if not ans:
                q["answer_markdown"] = ""
            else:
                if not generate_answers_bool:
                    if "[EXTRACTED_ORIGINAL]" in ans:
                        q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()
                    else:
                        q["answer_markdown"] = ""
                else:
                    q["answer_markdown"] = ans.replace("[EXTRACTED_ORIGINAL]", "").strip()

            # 智能提取出处双重保险：AI 提取优先，若 AI 未提取则尝试正则从 content 中提取
            extracted_source = q.get("source")
            content_str = q.get("content", "")
            # 选择题选项统一为 choices 网格环境（双保险：即便 AI 输出内联 A./B. 也归一化）
            content_str = normalize_choice_options_to_latex(content_str)

            # 正则匹配题干开头形如 "10. (2019·全国·高考真题)已知..." 的出处
            # group(1): 题号前缀, group(2): 左括号, group(3): 出处内容, group(4): 右括号
            prefix_match = re.match(r'^(\s*(?:\d+[\.、\s]*)?)([\(（])([^\(（\)）\s]{4,})([\)）])', content_str)
            if prefix_match:
                if not extracted_source:
                    extracted_source = prefix_match.group(3).strip()
                # 剔除题干中的出处括号及前面的题号前缀，保持题干纯净
                to_remove = prefix_match.group(1) + prefix_match.group(2) + prefix_match.group(3) + prefix_match.group(4)
                content_str = content_str.replace(to_remove, "", 1).strip()
                # 移除可能残存的开头符号（如句点或顿号）
                content_str = re.sub(r'^[\s、\.．]+', '', content_str)
                q["content"] = content_str
                
            q["source"] = normalize_source(extracted_source or paper_title, fallback_title=paper_title, allow_ai=True)
            
            # Clean up double-escaped literal \n in fields
            for field in ["content", "answer_markdown"]:
                if field in q and isinstance(q[field], str):
                    text = q[field]
                    # Replace literal "\n" safely using negative lookahead (so it doesn't touch commands like \normalsize or \nabla)
                    text = re.sub(r'\\n(?![a-zA-Z])', '\n', text)
                    q[field] = text
            
            # Map images
            mapped_images = []
            ref_imgs = q.get("referenced_images", [])
            for ref_name in ref_imgs:
                ref_name = str(ref_name)
                # Direct match or fuzzy match
                found_path = None
                for orig_name, serv_path in image_mapping.items():
                    if tex_asset_references_match(ref_name, orig_name):
                        found_path = serv_path
                        break
                if found_path:
                    if found_path not in mapped_images:
                        mapped_images.append(found_path)
                    include_pattern = re.compile(
                        r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{\s*([^}]+?)\s*\}"
                    )
                    q["content"] = include_pattern.sub(
                        lambda match: (
                            f"![插图]({found_path})"
                            if tex_asset_references_match(ref_name, match.group(1))
                            else match.group(0)
                        ),
                        q["content"],
                    )
                else:
                    tex_diagnostics.setdefault("unmapped_images", []).append(str(ref_name))
                    
            q["image_paths"] = mapped_images
            
            # If AI didn't map it in content text but referenced it, append it to content
            for img_path in mapped_images:
                if img_path not in q["content"]:
                    q["content"] += f"\n\n![插图]({img_path})\n\n"

        unmapped_images = sorted(set(tex_diagnostics.get("unmapped_images", [])))
        unassigned_images = sorted(set(tex_diagnostics.get("unassigned_source_images", [])))
        tex_diagnostics["unmapped_images"] = unmapped_images
        tex_diagnostics["unassigned_source_images"] = unassigned_images
        if unmapped_images:
            tex_diagnostics.setdefault("warnings", []).append(
                "以下 TeX 配图未找到同名上传文件：" + "、".join(unmapped_images[:8])
            )
        if unassigned_images:
            tex_diagnostics.setdefault("warnings", []).append(
                "以下配图未能确定所属题目：" + "、".join(unassigned_images[:8])
            )
                    
        # 字段对齐：AI 拆解输出 compulsory/chapter，而前端审查卡片绑定 category_compulsory/category_chapter。
        # 这里做桥接（不额外消耗 token），使审查页能正确回填学段与章节。
        for q in parsed_questions:
            if not q.get("category_compulsory") and q.get("compulsory"):
                q["category_compulsory"] = q["compulsory"]
            if not q.get("category_chapter") and q.get("chapter"):
                q["category_chapter"] = q["chapter"]

        return {
            "status": "success",
            "questions": parsed_questions,
            "tex_diagnostics": tex_diagnostics,
            "eval_decision": {
                "difficulty": decision.get("difficulty"),
                "used_free": decision.get("used_free"),
                "reason": decision.get("reason"),
                "model": model_name,
                "provider_label": provider_name,
            },
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"试卷解析失败: {str(e)}"},
            status_code=500
        )

@app.get("/api/sources")
def get_sources(db: Session = Depends(get_db)):
    results = db.query(Question.source).distinct().all()
    sources = []
    for r in results:
        val = r[0]
        if val and val.strip():
            sources.append(val.strip())
            
    # Sort alphabetically (case-insensitive)
    sources.sort(key=str.lower)
    return sources

@app.get("/api/source-canonical-map")
def get_source_canonical_map():
    """吐出来源归一映射表，供前端 preview 精确对齐后端落库结果。

    单一事实源为 mathbank.source_normalize 的 CANONICAL_MAP（精确条目）与
    SCHOOL_ALIASES（学校别名）；前端初始化时拉取一次，在 normalizeSource 中
    分别作为 Tier1 精确查表与别名展开，保证预览值与存储值完全一致、且无重复定义。
    """
    return {"entries": CANONICAL_MAP, "aliases": SCHOOL_ALIASES}

@app.post("/api/shutdown")
def shutdown_server():
    import signal
    def stop_server():
        import time
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)

    import threading
    threading.Thread(target=stop_server).start()
    
    return {"status": "success", "message": "题库系统正在关闭中..."}


# ----------------- Storage Promotion Engine -----------------

def rollback_question_asset_promotions(promotions: list[tuple[Path, Path]]) -> None:
    """Best-effort compensation when a DB transaction rejects promoted files."""

    for source, destination in reversed(promotions):
        try:
            if destination.is_file() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        except OSError as exc:
            print(f"[Storage Rollback] Failed to restore a promoted asset: {type(exc).__name__}")


def _referenced_question_assets(db: Session) -> set[Path]:
    """Resolve every stored question image reference with one database query."""

    resolved_references: set[Path] = set()
    rows = db.query(
        Question._image_paths,
        Question.content,
        Question.answer_markdown,
    ).all()
    for raw_paths, content, answer_markdown in rows:
        references = []
        try:
            parsed = json.loads(raw_paths or "[]")
            if isinstance(parsed, list):
                references.extend(parsed)
        except (TypeError, json.JSONDecodeError):
            pass
        references.extend(
            re.findall(
                r'/static/(?:uploads|test_uploads)/[a-zA-Z0-9_./-]+',
                f"{content or ''}\n{answer_markdown or ''}",
            )
        )
        for reference in references:
            try:
                resolved = resolve_upload_asset(
                    reference,
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                    require_file=False,
                )
            except AssetSecurityError:
                continue
            resolved_references.add(resolved)
    return resolved_references


def delete_unreferenced_question_assets(db: Session, references) -> int:
    """Delete committed-away images only when no remaining question uses them."""

    candidates: set[Path] = set()
    for reference in set(references or []):
        try:
            candidates.add(
                resolve_upload_asset(
                    reference,
                    uploads_dir=UPLOAD_DIR,
                    url_prefix=UPLOAD_DIR_REL,
                    require_file=False,
                )
            )
        except AssetSecurityError:
            print("[Storage Cleanup] Skipped an invalid legacy image path.")

    if not candidates:
        return 0
    referenced = _referenced_question_assets(db)
    removed = 0
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate not in referenced:
                candidate.unlink()
                removed += 1
        except OSError:
            print("[Storage Cleanup] Skipped an unavailable legacy image path.")
    return removed


def promote_question_temp_assets(
    content: str,
    answer_markdown: str,
    image_paths_list: list,
    *,
    promotion_log: list[tuple[Path, Path]] | None = None,
) -> tuple:
    """物理移动临时图片到永久目录，并更新题干、解析和图片路径列表中的引用"""
    import shutil

    if not isinstance(image_paths_list, list):
        raise AssetSecurityError("image_paths 必须是插图路径数组。")

    embedded_paths = re.findall(
        r'/static/(?:uploads|test_uploads)/tmp/[a-zA-Z0-9_.-]+',
        f"{content}\n{answer_markdown}",
    )
    all_references = [value for value in image_paths_list if value] + embedded_paths

    # Validate the complete set before moving anything.  A bad second path must
    # not leave the first path half-promoted.
    canonical_by_input: dict[str, str] = {}
    resolved_by_canonical: dict[str, Path] = {}
    for reference in all_references:
        canonical = normalize_upload_asset_reference(
            reference,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
        )
        canonical_by_input[reference] = canonical
        resolved_by_canonical.setdefault(
            canonical,
            resolve_upload_asset(
                canonical,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            ),
        )

    upload_root = Path(UPLOAD_DIR).resolve()
    temp_root = Path(TMP_UPLOAD_DIR).resolve()
    promoted_by_canonical: dict[str, str] = {}
    for canonical, source in resolved_by_canonical.items():
        if source.parent == temp_root:
            destination_url = f"/{UPLOAD_DIR_REL}/{source.name}"
            destination = resolve_upload_asset(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
                require_file=False,
            )
            if destination.exists():
                raise AssetSecurityError("目标插图文件已存在，已停止覆盖。")
            shutil.move(str(source), str(destination))
            if promotion_log is not None:
                promotion_log.append((source, destination))
            promoted_by_canonical[canonical] = normalize_upload_asset_reference(
                destination_url,
                uploads_dir=upload_root,
                url_prefix=UPLOAD_DIR_REL,
            )
        elif source.is_relative_to(upload_root):
            promoted_by_canonical[canonical] = canonical
        else:  # Defensive; resolve_upload_asset should already make this impossible.
            raise AssetSecurityError("临时插图越出了上传目录。")

    replacements: dict[str, str] = {}
    for original, canonical in canonical_by_input.items():
        promoted = promoted_by_canonical[canonical]
        replacements[original] = promoted
        replacements[canonical] = promoted

    new_content = content
    new_answer = answer_markdown
    for old_path, new_path in replacements.items():
        new_content = new_content.replace(old_path, new_path)
        new_answer = new_answer.replace(old_path, new_path)

    updated_paths: list[str] = []
    for original in image_paths_list:
        if not original:
            continue
        promoted = promoted_by_canonical[canonical_by_input[original]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    for embedded in embedded_paths:
        promoted = promoted_by_canonical[canonical_by_input[embedded]]
        if promoted not in updated_paths:
            updated_paths.append(promoted)

    return new_content, new_answer, updated_paths


@app.post("/api/ai/manual-crop-pdf")
def manual_crop_pdf(payload: dict):
    """用户在前端手动拖拽框选后，后端根据坐标裁剪 PDF 页面的特定区域"""
    try:
        import math

        if not isinstance(payload, dict):
            raise ValueError("裁剪参数格式不正确。")
        try:
            task_id = str(uuid.UUID(str(payload.get("task_id", ""))))
        except (ValueError, AttributeError) as exc:
            raise ValueError("任务 ID 格式不正确。") from exc
        task = DOCUMENT_TASKS.snapshot(task_id)
        doc_type = (task or {}).get("document_type")
        if not task or doc_type not in ("pdf", "docx"):
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的 PDF/Word 任务！"},
                status_code=404,
            )
        if task.get("status") in {"cancelled", "error"}:
            raise ValueError("已取消或失败的文档任务不能再裁剪。")
        page_index = int(payload.get("page_index", 0))
        if page_index < 0 or page_index >= MAX_PDF_TASK_PAGES:
            raise ValueError("页码越界。")
        ymin = float(payload.get("ymin", 0))
        xmin = float(payload.get("xmin", 0))
        ymax = float(payload.get("ymax", 0))
        xmax = float(payload.get("xmax", 0))
        coordinates = (ymin, xmin, ymax, xmax)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("裁剪坐标必须是有限数值。")
        if not (
            0 <= ymin < ymax <= 100
            and 0 <= xmin < xmax <= 100
        ):
            raise ValueError("裁剪坐标必须位于 0–100，且框选区域不能为空。")

        # 页图文件名前缀随文档类型：PDF 直提为 pdf_page_，Word 转 PDF 后为 docx_page_
        # （_render_pdf_bytes_to_page_images 生成），此前只认 pdf_page_ 导致 Word 手动截图 404。
        img_prefix = "docx_page_" if doc_type == "docx" else "pdf_page_"
        img_filename = f"{img_prefix}{task_id}_{page_index}.png"
        img_filepath = Path(TMP_UPLOAD_DIR) / img_filename
        
        if not img_filepath.is_file() or img_filepath.is_symlink():
            return JSONResponse(
                content={"status": "error", "message": "未找到对应的页面图片！"},
                status_code=404
            )
            
        with Image.open(img_filepath) as img:
            img.load()
            w, h = img.size

            # Convert percentage to pixels.
            left = max(0, min((xmin / 100.0) * w, w - 1))
            top = max(0, min((ymin / 100.0) * h, h - 1))
            right = max(left + 1, min((xmax / 100.0) * w, w))
            bottom = max(top + 1, min((ymax / 100.0) * h, h))
            cropped = img.crop((left, top, right, bottom))

        crop_filename = f"pdf_crop_{task_id}_{uuid.uuid4().hex[:12]}.png"
        crop_filepath = Path(TMP_UPLOAD_DIR) / crop_filename
        cropped.save(crop_filepath, format="PNG")
        
        img_url = f"/{UPLOAD_DIR_REL}/tmp/{crop_filename}"
        if not DOCUMENT_TASKS.add_temp_asset(task_id, img_url):
            crop_filepath.unlink(missing_ok=True)
            raise ValueError("任务记录已过期，无法登记裁剪图片。")
        return {"status": "success", "image_path": img_url}
    except ValueError as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=400,
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"手动裁剪失败: {str(e)}"},
            status_code=500
        )


def extract_title_from_latex(latex: str) -> str:
    """从 LaTeX 源码中尝试自动提取试卷标题"""
    if not latex:
        return ""
    import re
    
    def clean_latex(txt: str) -> str:
        # 移除字体大小命令等
        txt = re.sub(r'\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt|heiti|kaishu|fangsong|songti)', '', txt)
        # 解包 textbf 等
        txt = re.sub(r'\\text(bf|it|sf|tt)?\s*\{([^}]+)\}', r'\2', txt)
        txt = txt.replace('{', '').replace('}', '').replace('\\\\', '\n').strip()
        lines = [line.strip() for line in txt.split('\n') if line.strip()]
        if lines:
            return lines[0][:60]
        return ""

    # 1. 尝试匹配 \title{...}
    match = re.search(r'\\title\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    # 2. 尝试匹配 \chead{...}
    match = re.search(r'\\chead\s*\{([^}]+)\}', latex)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned and "页" not in cleaned and "绝密" not in cleaned:
            return cleaned
            
    # 3. 尝试匹配 \begin{center} ... \end{center} 头部区域
    top_part = latex[:1500]
    match = re.search(r'\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}', top_part)
    if match:
        cleaned = clean_latex(match.group(1))
        if cleaned:
            return cleaned
            
    return ""


# ----------------- PDF Import & AI Parsing Backend Logic -----------------

def ocr_pdf_page_image(image_path: str) -> str:
    """自动选择已配置的 VLM 识别引擎进行单页识别，支持故障转移（Fallback）与兜底识别"""
    errors = []
    prefer_engine = os.getenv("OCR_PREFER_ENGINE", "siliconflow")
    providers_to_try = resolve_ocr_fallbacks(prefer_engine)

    if not providers_to_try:
        raise ValueError("未配置任何识图 Key，请在右上角「API设置」面板中配置 硅基流动、阿里百炼 或 中转站 API 密钥。")

    for ocr_provider in providers_to_try:
        label = ocr_provider.provider_label
        try:
            print(f"[PDF OCR Flow] 正在尝试调用识图引擎: {label}...")
            return ocr_via_provider(image_path, ocr_provider)
        except requests.exceptions.ReadTimeout as e_single:
            # The first provider may already have accepted the image.  Sending
            # it immediately to another provider can create a duplicate bill.
            raise RuntimeError(
                f"{label} 读取超时，请求是否已被处理尚不确定。"
                "为避免重复计费，本次未自动切换到下一家模型，请稍后手动重试。"
            ) from e_single
        except Exception as e_single:
            err_msg = f"{label} 出错: {str(e_single)}"
            print(f"[PDF OCR Flow Warning] {err_msg}")
            errors.append(err_msg)
            
    # 如果全部都失败了，抛出包含所有尝试错误细节的汇总异常
    raise RuntimeError("所有配置的识图引擎均尝试失败。详情:\n" + "\n".join(errors))


def process_ocr_illustrations(text: str) -> str:
    """(已关闭 AI 自动插图裁剪) 仅进行安全标签清洗，擦除任何潜在的视觉定位标签或 box 坐标标记，返回纯净 OCR 结果"""
    import re
    if not text:
        return text
    
    # 1. 擦除 Qwen 视觉定位标签: <|box_start|>(ymin,xmin,ymax,xmax)<|box_end|>
    cleaned = re.sub(r"(?i)<\|box_start\|>.*?<\|box_end\|>", "", text)
    
    # 2. 擦除 ILLUSTRATION_BOX 标签: [ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]
    cleaned = re.sub(r"(?i)\[ILLUSTRATION_BOX:.*?\]", "", cleaned)
    cleaned = re.sub(r"(?i)ILLUSTRATION_BOX\s*[:：\(（\[\s]*[^\]\)\n\r]+[\s\]\)]*", "", cleaned)
    
    return cleaned.strip()


def find_source_page_by_overlap(q_text: str, ocr_results: list) -> int:
    """利用 3-shingle（三字符切片）特征重合度，计算题目最可能所属的 PDF 原始物理页码（本地索引）。

    返回本地页索引（0-based，与 ocr_results / page_images 顺序一致）；无匹配时返回 -1。
    """
    if not q_text or not ocr_results:
        return -1
    
    import re
    def clean_for_compare(t: str) -> str:
        # 仅保留中文字符、英文字母和数字，过滤掉干扰公式渲染的标点符号
        return "".join(re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]', t))
        
    cleaned_q = clean_for_compare(q_text)
    if not cleaned_q:
        return -1
        
    best_page = -1
    max_overlap = 0
    
    for idx, page_text in enumerate(ocr_results):
        if not page_text:
            continue
        cleaned_page = clean_for_compare(page_text)
        
        # 构建 3-shingle 切片集合
        if len(cleaned_q) >= 3:
            shingles_q = set(cleaned_q[i:i+3] for i in range(len(cleaned_q)-2))
        else:
            shingles_q = {cleaned_q}
            
        if len(cleaned_page) >= 3:
            shingles_page = set(cleaned_page[i:i+3] for i in range(len(cleaned_page)-2))
        else:
            shingles_page = {cleaned_page}
            
        overlap = len(shingles_q.intersection(shingles_page))
        if overlap > max_overlap:
            max_overlap = overlap
            best_page = idx
            
    return best_page


def assign_source_pages_by_overlap(questions: list, page_texts: list) -> None:
    """为每道题写入 source_page（0-based 本地页索引），使前端“手动截图”能直接跳到该题所在页。

    基于 find_source_page_by_overlap 的 3-shingle 文本重合度匹配；page_texts 的顺序必须与
    前端展示的 page_images 严格一致（PDF = 目标页顺序；Word = 转 PDF 后的物理页顺序）。
    """
    if not page_texts:
        return
    for q in questions:
        if not isinstance(q, dict):
            continue
        sp = find_source_page_by_overlap(q.get("content", "") or "", page_texts)
        if sp >= 0:
            q["source_page"] = sp


@app.post("/api/paper/ai-select")
def ai_select_paper(payload: dict, db: Session = Depends(get_db)):
    """AI 智能选题：结合用户指定的 PREFER_SOLVE_MODEL 大模型与 math-teaching 教研引擎组卷"""
    try:
        prompt = payload.get("prompt", "").strip()
        question_type = payload.get("question_type", "")
        difficulty = payload.get("difficulty", "")
        compulsory = payload.get("compulsory", "")
        chapter = payload.get("chapter", "")
        knowledge = payload.get("knowledge", "")
        knowledge_list = payload.get("knowledge_list", "")
        solve_method = payload.get("solve_method", "")
        limit = max(1, min(int(payload.get("limit", 5)), 20))
        # 显式避重开关：fresh_priority=true 优先未用过(鲜活)，false 优先高频旧题，未传则按 prompt 关键词隐式判断
        fresh_priority_raw = payload.get("fresh_priority", None)

        # 0. 自然语言意图智能分析 (NL Intent Parser)
        extracted_topics = []
        is_review_intent = False
        if prompt:
            is_review_intent = any(k in prompt for k in ['做过', '考过', '已抽过', '已用过', '复习', '旧题', '重做', '错题', '以往', '历史'])
        # 显式开关覆盖隐式判断
        if fresh_priority_raw is not None:
            is_review_intent = not (str(fresh_priority_raw).lower() in ("true", "1", "yes"))
            num_match = re.search(r'([一二三四五六七八九十1-9]+)\s*道', prompt)
            cn_to_num = {'一':1, '两':2, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}
            if num_match:
                val = num_match.group(1)
                limit = cn_to_num.get(val, int(val) if val.isdigit() else limit)

            if not question_type:
                if '填空' in prompt: question_type = 'fill_in_blank'
                elif '单选' in prompt: question_type = 'single_choice'
                elif '多选' in prompt: question_type = 'multi_choice'
                elif '解答' in prompt: question_type = 'detailed_answer'

            known_topics = ['立体几何', '集合', '函数', '导数', '数列', '三角函数', '平面向量', '概率', '解析几何', '圆锥曲线', '复数', '不等式', '排列组合']
            extracted_topics = [t for t in known_topics if t in prompt]

        # 1. 结构化过滤基础题目池
        query = db.query(Question)
        if question_type:
            query = query.filter(Question.question_type == question_type)
        if difficulty:
            query = query.filter(Question.difficulty == difficulty)
        if compulsory:
            query = query.filter(Question.category_compulsory == compulsory)
        if chapter:
            # 主分类命中，或关联章节(JSON)中含有该章节，融合题也能被检索到
            query = query.filter(
                or_(
                    Question.category_chapter == chapter,
                    text(
                        "EXISTS (SELECT 1 FROM json_each(COALESCE(related_curriculums, '[]')) "
                        "WHERE json_extract(value, '$.chapter') = :chap)"
                    ).bindparams(chap=chapter),
                )
            )
        if knowledge:
            query = query.filter(Question.category_knowledge == knowledge)
        # 知识点多标签（knowledge_list 支持逗号分隔多值 OR，边界 LIKE）
        if knowledge_list:
            kl_values = [v.strip() for v in re.split(r"[,，;；\n]+", str(knowledge_list)) if v.strip()]
            if kl_values:
                kl_conds = []
                for v in kl_values:
                    like = f"%{v}%"
                    kl_conds.append(Question.knowledge_list.like(like))
                    kl_conds.append(Question.category_knowledge.like(like))
                query = query.filter(or_(*kl_conds))
        # 解题方法多标签（solve_method 支持逗号分隔多值 OR）
        if solve_method:
            sm_values = [v.strip() for v in re.split(r"[,，;；\n]+", str(solve_method)) if v.strip()]
            if sm_values:
                sm_conds = []
                for v in sm_values:
                    like = f"%{v}%"
                    sm_conds.append(Question.solve_method.like(like))
                query = query.filter(or_(*sm_conds))
            
        if is_review_intent:
            # 复习/旧题模式：优先提取已使用频次高的题目
            review_query = query.filter(Question.usage_count > 0).order_by(Question.usage_count.desc(), Question.id.desc())
            candidates = review_query.limit(35).all()
            if not candidates:
                candidates = query.order_by(Question.id.desc()).limit(35).all()
        else:
            # 默认鲜活模式：优先提取从未被使用过的冷门题目
            candidates = query.order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()
            if not candidates:
                candidates = db.query(Question).order_by(Question.usage_count.asc(), Question.id.desc()).limit(35).all()

        # 2. 解题、拆卷、分类和组卷共用同一供应商解析规则。
        # 不会因为某家 Key 缺失而静默改用另一家。
        target_model = (
            os.getenv("PREFER_SOLVE_MODEL")
            or os.getenv("PREFER_PARSE_MODEL")
            or "deepseek-chat"
        )
        provider = resolve_text_provider(target_model)
        api_key = provider.api_key
        api_base = provider.api_base
        model_name = provider.model_name
        provider_name = provider.provider_label

        api_error_detail = None
        if prompt and candidates:
            if not api_key or not api_base:
                api_error_detail = (
                    f"指定的 AI 解题模型 ({target_model}) 未配置有效的 "
                    f"API Key 或 Base URL（{provider.credential_label}）。"
                )
            else:
                candidate_items = []
                for q in candidates:
                    clean_stem = re.sub(r'[\r\n]+', ' ', q.content[:80])
                    candidate_items.append({
                        "id": q.id,
                        "question_type": q.question_type,
                        "difficulty": q.difficulty,
                        "usage_count": q.usage_count or 0,
                        "knowledge": q.category_knowledge or q.category_chapter or "通用知识点",
                        "knowledge_list": (q.knowledge_list or "").split(",") if q.knowledge_list else [],
                        "solve_method": (q.solve_method or "").split(",") if q.solve_method else [],
                        "tags": q.tags or "",
                        "stem_excerpt": clean_stem
                    })

                system_prompt, user_content = build_paper_selection_prompts(
                    teacher_prompt=prompt,
                    limit=limit,
                    candidates=candidate_items,
                    is_review_intent=is_review_intent,
                )

                try:
                    payload_data = {
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "temperature": 0.3
                    }
                    payload_data = inject_reasoning_effort(
                        payload_data, provider.reasoning_effort
                    )
                    payload_data = apply_bailian_thinking_policy(
                        payload_data,
                        provider_code=provider.provider_code,
                        model_name=model_name,
                        task="paper_selection",
                    )
                    response = post_chat_completion(
                        provider,
                        payload_data,
                        timeout=20,
                        provider_name=provider_name,
                    )
                    res_json = response.json()
                    raw_content = res_json.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if raw_content.startswith("```"):
                        raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content)
                        raw_content = re.sub(r"\s*```$", "", raw_content)

                    parsed = json.loads(raw_content)
                    raw_selected_ids = parsed.get("selected_ids", [])
                    ai_analysis = parsed.get("ai_analysis", "")

                    if raw_selected_ids and isinstance(raw_selected_ids, list):
                        # The model may only rank the candidate IDs that were
                        # actually supplied after local filters.  This prevents
                        # prompt output from bypassing chapter/type constraints
                        # or selecting arbitrary records from the database.
                        allowed_ids = {question.id for question in candidates}
                        selected_ids = []
                        seen_ids = set()
                        for raw_id in raw_selected_ids:
                            try:
                                selected_id = int(raw_id)
                            except (TypeError, ValueError):
                                continue
                            if (
                                selected_id in allowed_ids
                                and selected_id not in seen_ids
                            ):
                                selected_ids.append(selected_id)
                                seen_ids.add(selected_id)
                            if len(selected_ids) >= limit:
                                break
                        db_selected = db.query(Question).filter(Question.id.in_(selected_ids)).all()
                        id_map = {q.id: q for q in db_selected}
                        seq_map = get_seq_mapping(db, selected_ids)
                        final_questions = [{**id_map[qid].to_dict(), "seq_num": seq_map.get(qid)} for qid in selected_ids if qid in id_map]

                        if final_questions:
                            return {
                                "status": "success",
                                "data": final_questions,
                                "count": len(final_questions),
                                "ai_analysis": ai_analysis,
                                "model_used": f"{provider_name} ({model_name})",
                                "fallback": False
                            }
                except Exception as llm_err:
                    api_error_detail = f"{provider_name} API 请求失败: {str(llm_err)}"

        # 3. 降级本地算法（带明确错误反馈）
        fallback_questions = []
        if extracted_topics:
            for topic in extracted_topics:
                sub_query = db.query(Question)
                if question_type:
                    sub_query = sub_query.filter(Question.question_type == question_type)
                sub_query = sub_query.filter(
                    (Question.content.like(f"%{topic}%")) |
                    (Question.category_chapter.like(f"%{topic}%")) |
                    (Question.category_knowledge.like(f"%{topic}%")) |
                    (Question.tags.like(f"%{topic}%"))
                )
                order_clause = Question.usage_count.desc() if is_review_intent else Question.usage_count.asc()
                for q in sub_query.order_by(order_clause, Question.id.desc()).all():
                    if q not in fallback_questions:
                        fallback_questions.append(q)

        # 补足数量
        if len(fallback_questions) < limit:
            for q in candidates:
                if q not in fallback_questions:
                    fallback_questions.append(q)
                if len(fallback_questions) >= limit:
                    break

        selected_fallback = fallback_questions[:limit]
        seq_map = get_seq_mapping(db, [q.id for q in selected_fallback])
        result = [{**q.to_dict(), "seq_num": seq_map.get(q.id)} for q in selected_fallback]
        
        topic_str = "、".join(extracted_topics) if extracted_topics else "通用知识点"
        err_banner = f"⚠️ 【AI 解题模型调用未成功】: {api_error_detail}\n系统已为您自动启动本地教研算法，根据意图（{topic_str}）在本地题库中筛选并组合了 {len(result)} 道精选题目。" if api_error_detail else f"【本地智能筛选分析】已为您自动识别意图（{topic_str}），从题库中精准挑选并组合了鲜活试题。"
        
        return {
            "status": "success",
            "data": result,
            "count": len(result),
            "ai_analysis": err_banner,
            "model_used": f"⚠️ 模型调用失败 ({target_model}) ➔ 退回本地算法" if api_error_detail else "本地算法",
            "fallback": True
        }
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"AI 智能选题失败: {str(e)}"}, status_code=500)


@app.post("/api/paper/batch-select")
def batch_select_paper(payload: dict, db: Session = Depends(get_db)):
    """细目表组卷：按规格数组逐行选题（本地筛选为主，跨行去重），返回缺口报告。

    入参 payload:
    {
        "rows": [
            {"knowledge": "导数应用", "question_type": "detailed_answer",
             "difficulty": "challenge", "solve_method": "数形结合", "count": 3},
            ...
        ],
        "use_ai_refine": false   // 可选：是否对候选池做 AI 精排（需配置解析模型 Key）
    }
    每行 knowledge/solve_method 支持逗号分隔多值（OR 匹配，复用边界 LIKE）。
    """
    try:
        rows = payload.get("rows", [])
        if not isinstance(rows, list) or not rows:
            return JSONResponse(
                content={"status": "error", "message": "细目表不能为空。"},
                status_code=400,
            )
        if len(rows) > 50:
            return JSONResponse(
                content={"status": "error", "message": "细目表行数过多（上限 50 行）。"},
                status_code=400,
            )

        # 候选池默认优先「鲜活」题（usage_count 升序），避免反复抽到同一批旧题
        used_ids: set[int] = set()
        per_row_results = []
        all_selected_ids: list[int] = []

        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            knowledge = (row.get("knowledge") or "").strip()
            question_type = (row.get("question_type") or "").strip()
            difficulty = (row.get("difficulty") or "").strip()
            solve_method = (row.get("solve_method") or "").strip()
            try:
                need = max(0, min(int(row.get("count", 1)), 20))
            except (TypeError, ValueError):
                need = 1

            query = db.query(Question)
            if question_type:
                query = query.filter(Question.question_type == question_type)
            if difficulty:
                query = query.filter(Question.difficulty == difficulty)
            # 知识点多标签：逗号分隔多值 OR（边界 LIKE，避免子串误命中）
            if knowledge:
                know_values = [v.strip() for v in re.split(r"[,，;；\n]+", knowledge) if v.strip()]
                if know_values:
                    know_conds = []
                    for v in know_values:
                        like = f"%{v}%"
                        know_conds.append(Question.knowledge_list.like(like))
                        know_conds.append(Question.category_knowledge.like(like))
                    query = query.filter(or_(*know_conds))
            # 解题方法多标签 OR
            if solve_method:
                sm_values = [v.strip() for v in re.split(r"[,，;；\n]+", solve_method) if v.strip()]
                if sm_values:
                    sm_conds = []
                    for v in sm_values:
                        like = f"%{v}%"
                        sm_conds.append(Question.solve_method.like(like))
                    query = query.filter(or_(*sm_conds))

            # 排除已被前面行选走的题，避免跨行重复
            if used_ids:
                query = query.filter(~Question.id.in_(used_ids))

            candidates = query.order_by(Question.usage_count.asc(), Question.id.desc()).limit(60).all()

            picked = []
            for q in candidates:
                if len(picked) >= need:
                    break
                picked.append(q)

            row_selected_ids = [q.id for q in picked]
            for qid in row_selected_ids:
                used_ids.add(qid)
            all_selected_ids.extend(row_selected_ids)

            per_row_results.append({
                "row_index": idx,
                "spec": {
                    "knowledge": knowledge,
                    "question_type": question_type,
                    "difficulty": difficulty,
                    "solve_method": solve_method,
                    "count": need,
                },
                "selected_ids": row_selected_ids,
                "selected_count": len(row_selected_ids),
                "gap": max(0, need - len(row_selected_ids)),
            })

        # 组装返回题目数据（去重后的完整列表）
        db_selected = db.query(Question).filter(Question.id.in_(all_selected_ids)).all() if all_selected_ids else []
        id_map = {q.id: q for q in db_selected}
        seq_map = get_seq_mapping(db, all_selected_ids)
        questions_data = [
            {**id_map[qid].to_dict(), "seq_num": seq_map.get(qid)}
            for qid in all_selected_ids if qid in id_map
        ]

        total_gap = sum(r["gap"] for r in per_row_results)
        total_need = sum(r["spec"]["count"] for r in per_row_results)

        return {
            "status": "success",
            "questions": questions_data,
            "selected_count": len(all_selected_ids),
            "total_need": total_need,
            "total_gap": total_gap,
            "rows": per_row_results,
            "message": (f"已按细目表凑齐 {len(all_selected_ids)}/{total_need} 道，缺口 {total_gap} 道。"
                        + ("请在缺口行放宽条件（题型/难度/知识点）或补充题库。" if total_gap else "")),
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"细目表组卷失败: {str(e)}"},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# 拆题结果的学段/章节/小节受控归一（零 token 兜底）
# ---------------------------------------------------------------------------
# 背景：审查卡片的学段/章节/小节下拉是「严格相等匹配」受控教材目录
# （/api/categories = curriculum 全集 + 库内自定义条目）。LLM（尤其免费小
# 参数模型）输出的 compulsory/chapter 经常：①整字段留空；②写别名
# （「必修第一册」「选择性必修一」）；③照抄教辅专题名（「专题三 基本不等式」）。
# 任何偏差都会让三级下拉静默全空。与 source 的 normalize_source、选项的
# recover_missing_choices 同族问题，这里补上值级归一 + 知识点反推兜底。
#
# 匹配策略（保守优先，宁缺勿错）：
#   学段：精确 → 别名表 → 互含唯一命中；
#   章节：精确 → 归一化（去编号前缀/全角转半角/去空白）后相等 → 互含唯一；
#   小节：精确 → 互含唯一；空值时用 knowledge_list 逐个试；
#   反推：学段缺失或章节缺失时，用知识点标签在受控树里打分，唯一胜出才填。
_CATEGORY_COMP_ALIASES = {
    "必修第一册": "必修一", "必修第二册": "必修二", "必修第三册": "必修三",
    "必修第四册": "必修四",
    "选择性必修第一册": "选修一", "选择性必修第二册": "选修二", "选择性必修第三册": "选修三",
    "选择性必修一": "选修一", "选择性必修二": "选修二", "选择性必修三": "选修三",
    "选必一": "选修一", "选必二": "选修二", "选必三": "选修三",
    "必修1": "必修一", "必修2": "必修二", "必修3": "必修三", "必修4": "必修四",
    "选修1": "选修一", "选修2": "选修二", "选修3": "选修三",
}

_FULLWIDTH_TRANS = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）．：、，",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz().:,,",
)


def _normalize_category_key(s: str) -> str:
    """章节/小节名匹配前的归一：全角转半角、去空白、去编号类前缀（「第X章」「1.」「(3)」）。"""
    if not s:
        return ""
    t = str(s).translate(_FULLWIDTH_TRANS)
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"^第[一二三四五六七八九十百零]+[章节篇讲][\.、_\-]*", "", t)
    t = re.sub(r"^[\(\[\{](?:\d{1,2}|[\一二三四五六七八九十百零]{1,3})[\)\]\}][\.、_\-]*", "", t)
    t = re.sub(r"^\d+[\.\、_\-]+", "", t)
    return t.strip(" .、_-.：:,()（）")


def _match_compulsory(raw, curriculum) -> str | None:
    """学段（书名）受控归一。精确 → 别名 → 互含唯一命中；失败返回 None。"""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s in curriculum:
        return s
    alias = _CATEGORY_COMP_ALIASES.get(s)
    if alias and alias in curriculum:
        return alias
    hits = [c for c in curriculum if c and s and (c in s or s in c)]
    if len(hits) == 1:
        return hits[0]
    return None


def _match_chapter(raw, comp, curriculum) -> str | None:
    """章节受控归一（在给定书内）。精确 → 归一后相等 → 互含唯一命中。"""
    if not raw or not comp or comp not in curriculum:
        return None
    chaps = list(curriculum[comp].keys())
    s = str(raw).strip()
    if s in chaps:
        return s
    ns = _normalize_category_key(s)
    if not ns:
        return None
    norm_map: dict[str, list] = {}
    for ch in chaps:
        norm_map.setdefault(_normalize_category_key(ch), []).append(ch)
    hit = norm_map.get(ns)
    if hit and len(hit) == 1:
        return hit[0]
    hits = [ch for ch in chaps
            if ns in _normalize_category_key(ch) or _normalize_category_key(ch) in ns]
    if len(hits) == 1:
        return hits[0]
    return None


def _match_knowledge_in_section(raw, comp, chap, curriculum) -> str | None:
    """小节受控匹配（在给定书+章内）。精确 → 互含唯一命中。"""
    if not raw or not comp or not chap:
        return None
    knows = curriculum.get(comp, {}).get(chap) or []
    s = str(raw).strip()
    if s in knows:
        return s
    ns = _normalize_category_key(s)
    if not ns:
        return None
    hits = [k for k in knows if _normalize_category_key(k) and ns in _normalize_category_key(k)]
    if len(hits) == 1:
        return hits[0]
    return None


def _score_category_hits(tags, scoring_items: dict) -> dict:
    """知识点标签对候选（书,章）或小节打分：精确=3，互含（双方≥4字）=2。

    scoring_items: {候选键: 归一化文本}。返回 {候选键: 累计分}（只含有分者）。
    """
    scores: dict = {}
    for tag in tags:
        t = str(tag).strip()
        if len(t) < 2:
            continue
        nt = _normalize_category_key(t)
        if not nt:
            continue
        for key, ntext in scoring_items.items():
            if not ntext:
                continue
            if nt == ntext:
                scores[key] = scores.get(key, 0) + 3
            elif min(len(nt), len(ntext)) >= 4 and (nt in ntext or ntext in nt):
                scores[key] = scores.get(key, 0) + 2
    return scores


def _infer_comp_chap_from_knowledge(knowledge_list, curriculum):
    """知识点标签反推 (comp, chap)。章节名与小节名都参与打分（小节命中归所属章节）。

    仅当唯一 (comp, chap) 严格胜出时返回，否则 None。
    """
    if not knowledge_list:
        return None
    scoring: dict = {}
    for comp, chaps in curriculum.items():
        for chap, knows in chaps.items():
            scoring[(comp, chap)] = _normalize_category_key(chap)
            for k in knows or []:
                nk = _normalize_category_key(k)
                if nk:
                    scoring[(comp, chap)] = (scoring[(comp, chap)] + "|" + nk).strip("|")
    scores = _score_category_hits(knowledge_list, scoring)
    if not scores:
        return None
    best = max(scores.values())
    if best < 2:
        return None
    top = [k for k, v in scores.items() if v == best]
    if len(top) == 1:
        return top[0]
    return None


def _infer_chapter_from_knowledge(knowledge_list, comp, curriculum):
    """书已定时用知识点反推章节（章节名与小节名都参与打分）。唯一胜出才返回。"""
    if not knowledge_list or comp not in curriculum:
        return None
    scoring: dict = {}
    for chap, knows in curriculum[comp].items():
        scoring[chap] = _normalize_category_key(chap)
        for k in knows or []:
            nk = _normalize_category_key(k)
            if nk:
                scoring[chap] = (scoring[chap] + "|" + nk).strip("|")
    scores = _score_category_hits(knowledge_list, scoring)
    if not scores:
        return None
    best = max(scores.values())
    if best < 2:
        return None
    top = [k for k, v in scores.items() if v == best]
    if len(top) == 1:
        return top[0]
    return None


def normalize_category_fields(parsed_questions: list, curriculum: dict) -> dict:
    """三级受控归一 + 知识点反推兜底。就地改写 category_* 字段，返回审计统计。

    只在值能精确/唯一受控命中时才写入，失败留空（前端会归入「未分类」，
    由用户手选），绝不猜一个模糊值入库。
    """
    stats = {
        "total": len(parsed_questions),
        "comp": 0, "chap": 0, "know": 0,
        "comp_raw": {}, "chap_raw": {},
    }
    for q in parsed_questions:
        raw_comp = (q.get("category_compulsory") or q.get("compulsory") or "").strip()
        raw_chap = (q.get("category_chapter") or q.get("chapter") or "").strip()
        know_list = q.get("knowledge_list") if isinstance(q.get("knowledge_list"), list) else []

        comp = _match_compulsory(raw_comp, curriculum)
        chap = _match_chapter(raw_chap, comp, curriculum) if comp else None
        if comp and not chap:
            chap = _infer_chapter_from_knowledge(know_list, comp, curriculum)
        if not comp:
            inferred = _infer_comp_chap_from_knowledge(know_list, curriculum)
            if inferred:
                comp, chap = inferred

        if comp:
            q["category_compulsory"] = comp
            q["category_chapter"] = chap or ""
            stats["comp"] += 1
            if chap:
                stats["chap"] += 1
                if not (q.get("category_knowledge") or "").strip():
                    for tag in know_list:
                        know = _match_knowledge_in_section(tag, comp, chap, curriculum)
                        if know:
                            q["category_knowledge"] = know
                            stats["know"] += 1
                            break
        else:
            if raw_comp:
                stats["comp_raw"][raw_comp] = stats["comp_raw"].get(raw_comp, 0) + 1
            if raw_chap:
                key = f"{raw_comp or '?'} ▸ {raw_chap}"
                stats["chap_raw"][key] = stats["chap_raw"].get(key, 0) + 1
    return stats


def post_process_pdf_parsed_questions(parsed_questions: list, paper_title: str, task_id: str = None, ocr_results: list = None) -> list:
    """PDF 专属解析卡片后处理：正则搜寻 /tmp/ 下的图片，以及将未解析的图n占位符智能映射回真实的裁剪插图图片，
    最后将其灌入 image_paths 数组中，并在 content 中静默清除以配合布局展示。支持文本重合度兜底映射，防大模型删除路径！"""
    import re
    import os
    import glob

    # 0. 规范化所有拆解题目的填空下划线为 \fillin 宏，并剥离残留原卷题号
    #    先在剥离前抽两件关键信息：
    #    ① 题源括注（如 (2025·天津卷·★)），没有它的话 AI 输出 source 经常错位，
    #       而 PDF 路径不像 DOCX 走 lock_visible_math，AI 容易把题源直接写进 content 不进 source；
    #    ② 原卷题号，用于后段“答案区题号精确匹配”回填 answer_markdown。
    #    这两个信号剥离后即用即抛，不进数据库。
    for q in parsed_questions:
        if q.get("content"):
            q["content"] = normalize_fillin_macro(q.get("content", ""))
            raw_content = q["content"]
            # ② 题号：从开头 "1." "13)" "7."等跳到题干开始提取一次（只取第一个）。
            seq_match = re.match(
                r"^\s*(\d{1,3})(?=[\.、．\)）\s])",
                raw_content,
            )
            if seq_match:
                try:
                    q["_orig_seq_int"] = int(seq_match.group(1))
                except ValueError:
                    pass
            # ① 题源括注：紧跟题号的 (…/全角…)，与 DOCX 路径一致。
            # group(1) 题号前缀, group(2) 左括号, group(3) 出处, group(4) 右括号。
            src_match = re.match(
                r"^(\s*(?:\d{1,3}[\.、\s]*)?)([\(（])([^\(（\)）\s]{4,})([\)）])",
                raw_content,
            )
            if src_match and not q.get("source"):
                q["_pdf_source_extracted"] = src_match.group(3).strip()
                # 从 content 删去 该括注 + 前面可能的题号前缀，让题干纯净。
                to_remove = src_match.group(1) + src_match.group(2) + src_match.group(3) + src_match.group(4)
                q["content"] = raw_content.replace(to_remove, "", 1).strip()
            q["content"] = _strip_leading_question_number(q["content"])
            q["content"] = normalize_choice_options_to_latex(q["content"])

    # 0.5 学段/章节/小节三级受控归一（值级兜底）：AI 整卷拆解输出的
    #    compulsory/chapter 可能为空、写别名（「必修第一册」）或照抄教辅专题名，
    #    而审查卡片下拉严格相等匹配受控教材目录，任何偏差都静默全空。
    #    这里零 token 归一：精确 → 别名表 → 互含唯一 → 知识点标签反推（唯一胜出才填），
    #    失败留空交用户手选，绝不猜模糊值入库。同时输出审计日志便于事后定位。
    category_stats = normalize_category_fields(parsed_questions, get_current_curriculum())
    print(
        f"[Category Audit] {category_stats['total']} 题 | "
        f"学段受控 {category_stats['comp']}/{category_stats['total']}、"
        f"章节 {category_stats['chap']}、小节补填 {category_stats['know']}；"
        f"未匹配学段原值: {category_stats['comp_raw'] or '{}'}；"
        f"未匹配章节原值: {category_stats['chap_raw'] or '{}'}",
        flush=True,
    )

    # 1. 搜集该 PDF 任务在 tmp 文件夹中生成的所有物理裁剪图片，按生成时间（mtime）进行排序
    task_crop_urls = []
    # 1.5 抽答案区：按题号重新匹配原卷自带答案，回填到 answer_markdown（仅 AI 未填时）。
    #    OCR 文本前置牄潌后这里 receipt 在同一趟 reduce 中使用，
    #    避免后面推理时重复扫 OCR。
    pdf_answer_zone = _extract_pdf_answer_zone(ocr_results)
    if pdf_answer_zone:
        filled = _apply_pdf_answer_zone(parsed_questions, pdf_answer_zone)
        if filled:
            print(
                f"[PDF PostProcess] 从原卷答案区按题号回填了 {filled}/{len(parsed_questions)} 道题的 answer_markdown。",
                flush=True,
            )
    else:
        # 没识别出答案区时 _apply_pdf_answer_zone 不会运行，step 0 抽的
        # 临时题号必须在这里补清，避免 _orig_seq_int 泄漏进审查页/入库。
        for q in parsed_questions:
            q.pop("_orig_seq_int", None)
    if task_id:
        crop_pattern = os.path.join(TMP_UPLOAD_DIR, f"pdf_crop_{task_id}_*.png")
        crop_files = glob.glob(crop_pattern)
        crop_files.sort(key=lambda x: os.path.getmtime(x))
        task_crop_urls = [f"/{UPLOAD_DIR_REL}/tmp/{os.path.basename(f)}" for f in crop_files]
        print(f"[PDF PostProcess] 发现任务 {task_id} 的实际裁剪图片 {len(task_crop_urls)} 张: {task_crop_urls}")

    # 2. 顺序提取出所有题目中未成功解析的插图占位符（例如 图1.png, 图2.png, 图1, 图2 等，特征是不以 /static/ 开头的图片引用路径）
    placeholders_in_order = []
    placeholder_seen = set()
    
    # 匹配 Markdown 图片格式: ![alt](url)
    md_pattern = r'!\[.*?\]\(([^)]+)\)'
    # 匹配 LaTeX 图片格式: \includegraphics[...]{path}
    latex_pattern = r'\\includegraphics(?:\[.*?\])?\{([^}]+)\}'
    
    for q in parsed_questions:
        for field in ["content", "answer_markdown"]:
            text_val = q.get(field, "")
            if isinstance(text_val, str):
                # 提取 Markdown 图片占位符
                for m in re.finditer(md_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)
                # 提取 LaTeX 图片占位符
                for m in re.finditer(latex_pattern, text_val):
                    url = m.group(1).strip()
                    if url and not url.startswith("/static/") and url not in placeholder_seen:
                        placeholder_seen.add(url)
                        placeholders_in_order.append(url)

    # 3. 建立占位符与物理裁剪图片路径的 1-to-1 映射关系
    mapping = {}
    for idx, ph in enumerate(placeholders_in_order):
        if idx < len(task_crop_urls):
            mapping[ph] = task_crop_urls[idx]
    if mapping:
        print(f"[PDF PostProcess] 成功建立占位符修复映射: {mapping}")

    # 4. 对每个题目卡片进行字段修补、占位符替换与资源晋升准备
    # 4.0 题源兜底：AI 没填 source 时优先用正则在题干开头抽到的括注（_pdf_source_extracted）。
    #    仅当这个抽出的能过 normalize_source 才使用；避免把 (A) 这种选项括号误判。
    for q in parsed_questions:
        extracted = (q.pop("_pdf_source_extracted", None) or "").strip()
        ai_source = (q.get("source") or "").strip()
        chosen_source = ai_source
        if not chosen_source and extracted:
            chosen_source = extracted
        q["source"] = normalize_source(chosen_source or paper_title, fallback_title=paper_title, allow_ai=True)

        # 规范化 AI 自动打标的知识点 / 解题方法多标签（受控词表映射 + 去重）
        q["knowledge_list"] = normalize_tag_list(q.get("knowledge_list"), field="knowledge_list")
        q["solve_method"] = normalize_tag_list(q.get("solve_method"), field="solve_method")

        # 清理多余的双重转义 \n
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                text = q[field]
                text = re.sub(r'\\n(?![a-zA-Z])', '\n', text)
                q[field] = text

        # 智能替换 Markdown 和 LaTeX 字段中的图片占位符
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                # 替换已建立映射的非标准路径
                for ph, real_url in mapping.items():
                    if ph in q[field]:
                        q[field] = q[field].replace(ph, real_url)
                        # 如果是 LaTeX 的 \includegraphics 语法，顺带转换为 Markdown 图片语法以供前端预览渲染
                        latex_img_pattern = r'\\includegraphics(?:\[.*?\])?\{' + re.escape(real_url) + r'\}'
                        q[field] = re.sub(latex_img_pattern, f'![插图]({real_url})', q[field])

        # 寻找本题正文中夹带的所有临时图片 URL (注意：UUID 中含有 -，所以 regex 必须支持 [a-zA-Z0-9_-]+)
        found_crops = set()
        for field in ["content", "answer_markdown"]:
            if field in q and isinstance(q[field], str):
                for match in re.finditer(r'/static/(?:uploads|test_uploads)/tmp/[a-zA-Z0-9_.-]+', q[field]):
                    found_crops.add(match.group(0))
                    
        # 顺带检查 referenced_images 属性并应用修复映射
        ref_imgs = q.get("referenced_images", [])
        for ref in ref_imgs:
            mapped_ref = mapping.get(ref, ref)
            if "/tmp/" in mapped_ref:
                filename = os.path.basename(mapped_ref)
                found_crops.add(f"/{UPLOAD_DIR_REL}/tmp/{filename}")
                
        # 灌入 image_paths 作为独立配图卡片关联
        q["image_paths"] = list(found_crops)

    # 5. 极致兜底机制：如果大模型在拆题时完全删除了图片占位标记或路径，导致最终题目关联的图片为空，
    # 我们利用 3-shingle 文本重合度，将原始 PDF 物理页面产生的物理插图自动关联绑定回拆分出的题目！
    if ocr_results and task_id:
        page_crops = {}
        for p_idx, page_text in enumerate(ocr_results):
            # 获取当前页生成的所有 pdf_crop_ 临时文件 URL
            urls_on_page = re.findall(r'/static/uploads(?:_test|/test_uploads|/uploads)?/tmp/pdf_crop_[a-zA-Z0-9_-]+\.png', page_text or "")
            page_crops[p_idx] = list(set(urls_on_page))
            
        print(f"[PDF PostProcess Failsafe] 每页识别到的插图关系: {page_crops}")
        
        for q in parsed_questions:
            if not q.get("image_paths"):
                p_source = find_source_page_by_overlap(q.get("content", ""), ocr_results)
                if p_source >= 0:
                    crops = page_crops.get(p_source, [])
                    if crops:
                        q["image_paths"] = crops
                        print(f"[PDF PostProcess Failsafe] 成功通过重合度，将第 {p_source + 1} 页的插图 {crops} 兜底分配给题目: {q.get('content')[:40]}...")

    # 6. 从 content 题干中静默移除已经绑定至 image_paths 内部的占位图片语法，以避免重叠渲染
    for q in parsed_questions:
        found_crops = q.get("image_paths", [])
        if "content" in q and isinstance(q["content"], str):
            for crop_url in found_crops:
                q["content"] = re.sub(r'!\[.*?\]\(' + re.escape(crop_url) + r'\)', '', q["content"])
            q["content"] = q["content"].strip()
            
    return parsed_questions


def run_pdf_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
    page_range: str = None,
    pdf_strategy: str = "native_preferred",
):
    """PDF parsing with bounded OCR concurrency and cooperative cancellation."""

    import concurrent.futures

    temp_assets: list[str] = []
    current_step = "render_pages"
    tmp_pdf_path = Path(TMP_UPLOAD_DIR) / f"{task_id}.pdf"

    try:
        import fitz
    except ImportError:
        DOCUMENT_TASKS.fail(
            task_id,
            "本地 Python 环境未安装 PyMuPDF，请通过 pip install pymupdf 安装依赖！",
            document_type="pdf",
        )
        return

    try:
        DOCUMENT_TASKS.check_cancelled(task_id)
        tmp_pdf_path.write_bytes(file_bytes)
        DOCUMENT_TASKS.update(
            task_id,
            status="processing_images",
            progress=10,
            log="已接收文件，正在渲染 PDF 高清页面...",
            document_type="pdf",
            temp_assets=[],
        )
        DOCUMENT_TASKS.step_start(task_id, current_step)

        page_images: list[str] = []
        page_urls: list[str] = []
        with fitz.open(tmp_pdf_path) as document:
            total_pages = len(document)
            if total_pages == 0:
                raise ValueError("此 PDF 没有有效页面，或者格式已损坏！")
            target_page_indices = parse_page_range(page_range, total_pages)
            if len(target_page_indices) > MAX_PDF_TASK_PAGES:
                raise ValueError(
                    f"单次最多解析 {MAX_PDF_TASK_PAGES} 页，请填写较小的页码范围。"
                )

            for page_num in target_page_indices:
                DOCUMENT_TASKS.check_cancelled(task_id)
                page = document.load_page(page_num)
                estimated_pixels = int(
                    (page.rect.width / 72 * 250) * (page.rect.height / 72 * 250)
                )
                if estimated_pixels > 30_000_000:
                    raise ValueError(f"第 {page_num + 1} 页尺寸异常，已停止高清渲染。")
                pixmap = page.get_pixmap(dpi=250)
                image_filename = f"pdf_page_{task_id}_{page_num}.png"
                image_path = Path(TMP_UPLOAD_DIR) / image_filename
                pixmap.save(image_path)
                image_url = f"/{UPLOAD_DIR_REL}/tmp/{image_filename}"
                page_images.append(str(image_path))
                page_urls.append(image_url)
                temp_assets.append(image_url)
                DOCUMENT_TASKS.update(
                    task_id,
                    page_images=list(page_urls),
                    temp_assets=list(temp_assets),
                )

        tmp_pdf_path.unlink(missing_ok=True)
        DOCUMENT_TASKS.check_cancelled(task_id)
        total_target_pages = len(target_page_indices)

        current_step = "extract_text"
        DOCUMENT_TASKS.step_start(task_id, current_step)
        if pdf_strategy == "force_ocr":
            inspector_result = {"pages": [], "pdf_type": "scanned"}
            inspector_pages = {}
        else:
            inspector_result = inspect_and_extract_pdf(
                file_bytes,
                task_id,
                page_indices=target_page_indices,
            )
            inspector_pages = {
                int(page.get("page_index")): page
                for page in inspector_result.get("pages", [])
                if page.get("page_index") is not None
            }
        DOCUMENT_TASKS.check_cancelled(task_id)

        ocr_results = [None] * total_target_pages
        pages_requiring_ocr = []
        native_page_count = 0
        for local_idx, page_num in enumerate(target_page_indices):
            page_info = inspector_pages.get(page_num)
            native_text = str((page_info or {}).get("markdown") or "").strip()
            if page_info and not page_info.get("needs_ocr") and native_text:
                ocr_results[local_idx] = (
                    f"<!-- MATHBANK_PDF_PAGE:{page_num + 1} -->\n{native_text}"
                )
                native_page_count += 1
            else:
                pages_requiring_ocr.append(local_idx)

        if native_page_count:
            print(
                f"[PDF Inspector Flow] 原生直提 {native_page_count} 页，"
                f"视觉 OCR {len(pages_requiring_ocr)} 页 "
                f"(Type: {inspector_result.get('pdf_type')})",
                flush=True,
            )

        if not pages_requiring_ocr:
            DOCUMENT_TASKS.update(
                task_id,
                status="ai_splitting",
                progress=60,
                log=(
                    f"pdf-inspector 已按所选范围可靠提取 {native_page_count} 页原生文本，"
                    "正在连续拆题..."
                ),
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )
        else:
            DOCUMENT_TASKS.update(
                task_id,
                status="ocr_extraction",
                progress=30,
                log=(
                    f"所选 {total_target_pages} 页中，{native_page_count} 页已原生提取，"
                    f"仅对其余 {len(pages_requiring_ocr)} 页进行视觉转译..."
                ),
                page_images=list(page_urls),
                temp_assets=list(temp_assets),
            )

            def ocr_worker(local_idx, image_path):
                acquired = False
                try:
                    while not acquired:
                        DOCUMENT_TASKS.check_cancelled(task_id)
                        acquired = PDF_OCR_SEMAPHORE.acquire(timeout=0.25)
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    raw_text = ocr_pdf_page_image(image_path)
                    real_page_num = target_page_indices[local_idx] + 1
                    print(
                        f"[PDF OCR] 第 {real_page_num} 页识别完成 "
                        f"(characters={len(raw_text)})."
                    )
                    return local_idx, raw_text, None
                except TaskCancelled:
                    raise
                except Exception as ocr_error:
                    return local_idx, "", str(ocr_error)
                finally:
                    if acquired:
                        PDF_OCR_SEMAPHORE.release()

            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(pages_requiring_ocr), 4),
                thread_name_prefix="mathbank-pdf-ocr",
            )
            futures = []
            try:
                for local_idx in pages_requiring_ocr:
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    futures.append(
                        executor.submit(ocr_worker, local_idx, page_images[local_idx])
                    )

                completed = 0
                for future in concurrent.futures.as_completed(futures):
                    DOCUMENT_TASKS.check_cancelled(task_id)
                    local_idx, text, error = future.result()
                    if error:
                        real_page_num = target_page_indices[local_idx] + 1
                        raise RuntimeError(f"解析第 {real_page_num} 页出错: {error}")
                    processed_text = process_ocr_illustrations(text)
                    real_page_num = target_page_indices[local_idx] + 1
                    ocr_results[local_idx] = (
                        f"<!-- MATHBANK_PDF_PAGE:{real_page_num} -->\n"
                        f"{processed_text.strip()}"
                    )
                    completed += 1
                    progress = 30 + int(
                        (completed / len(pages_requiring_ocr)) * 40
                    )
                    DOCUMENT_TASKS.update(
                        task_id,
                        progress=progress,
                        log=(
                            f"视觉转译进度: {completed} / "
                            f"{len(pages_requiring_ocr)} 页已完成..."
                        ),
                    )
            finally:
                cancelled = DOCUMENT_TASKS.is_cancelled(task_id)
                if cancelled:
                    for future in futures:
                        future.cancel()
                executor.shutdown(wait=not cancelled, cancel_futures=True)

        DOCUMENT_TASKS.check_cancelled(task_id)
        full_latex_content = merge_pdf_page_texts(ocr_results)
        if not full_latex_content.strip():
            raise ValueError("所选 PDF 页面未能提取出可解析的文字内容。")

        current_step = "ai_split"
        DOCUMENT_TASKS.step_start(task_id, current_step)
        DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=80,
            log="文本与公式准备就绪！正在调用大模型拆解题目与标注属性...",
        )
        DOCUMENT_TASKS.check_cancelled(task_id)

        paper_title = os.path.splitext(filename)[0]
        auto_title = extract_title_from_latex(full_latex_content)
        if auto_title:
            paper_title = auto_title
        # 与 Word 链路一致：逐段回报进度，避免长卷拆解期间前端误判超时。
        def _report_pdf_split_progress(done, total):
            ratio = (done / total) if total else 1.0
            DOCUMENT_TASKS.update(
                task_id,
                status="ai_splitting",
                progress=80 + int(10 * ratio),
                log=f"正在调用大模型拆解题目（第 {done}/{total} 段）...",
            )

        # 自动识别「题目与解析分离」结构（零 token），替代原手动开关。
        detected_separated = detect_separated_mode(full_latex_content)
        DOCUMENT_TASKS.update(task_id, detected_separated=detected_separated)

        # L3 任务级告警：parse_quality 用容器透传到 DOCUMENT_TASKS，供前端徽章展示。
        _parse_quality_holder: List[Dict[str, Any]] = [{}]
        parsed_questions = parse_paper_text_internal(
            full_latex_content,
            generate_answers,
            separated_mode=detected_separated,
            progress_callback=_report_pdf_split_progress,
            formula_lock=False,
            quality_out=_parse_quality_holder,
            paper_title=paper_title,
        )
        parse_quality = _parse_quality_holder[0] or None
        DOCUMENT_TASKS.check_cancelled(task_id)
        current_step = "post_process"
        DOCUMENT_TASKS.step_start(task_id, current_step)
        DOCUMENT_TASKS.update(
            task_id,
            status="post_processing",
            progress=92,
            log="题目已拆分完成，正在整理卡片与插图映射...",
        )
        final_questions = post_process_pdf_parsed_questions(
            parsed_questions,
            paper_title,
            task_id,
            ocr_results,
        )
        # 方案B：按文本重合度把每题定位到原卷物理页，写 source_page 供手动截图直接跳转
        assign_source_pages_by_overlap(final_questions, ocr_results)
        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.step_complete_all(task_id)
        # 同 Word 链路：显式改写局部变量，避免收尾异常被误报为 post_process 失败。
        current_step = "finalize"
        DOCUMENT_TASKS.complete(
            task_id,
            log="完成！已为您提取并拆分全部题目卡片。",
            data=final_questions,
            page_images=list(page_urls),
            temp_assets=list(temp_assets),
            document_type="pdf",
            parse_quality=parse_quality,
        )
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        DOCUMENT_TASKS.step_error(
            task_id,
            current_step,
            f"PDF 智能拆解解析失败: {str(ex)}",
            document_type="pdf",
        )
    finally:
        tmp_pdf_path.unlink(missing_ok=True)


def parse_page_range(range_str: str, total_pages: int) -> list:
    """
    解析用户输入的页码范围字符串（1-indexed），转换为包含 0-indexed 页面索引的列表。
    支持格式如 "1-5", "1,3,5", "1-3,5,7-9"。
    """
    if total_pages <= 0:
        raise ValueError("PDF 没有有效页面。")
    if not range_str or not range_str.strip():
        return list(range(total_pages))

    pages = set()
    parts = str(range_str).replace(" ", "").split(",")
    for part in parts:
        if not part:
            raise ValueError("页码范围格式无效。")
        if "-" in part:
            sub_parts = part.split("-")
            if len(sub_parts) != 2:
                raise ValueError("页码范围格式无效。")
            try:
                start = int(sub_parts[0])
                end = int(sub_parts[1])
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if start < 1 or end < start or end > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.update(range(start - 1, end))
        else:
            try:
                page_number = int(part)
            except ValueError as exc:
                raise ValueError("页码范围必须使用数字。") from exc
            if page_number < 1 or page_number > total_pages:
                raise ValueError(f"页码范围必须位于 1 到 {total_pages}。")
            pages.add(page_number - 1)

    if not pages:
        raise ValueError("页码范围不能为空。")
    return sorted(pages)


# ----------------- 拆卷步骤可视化（进度条 / 错误定位） -----------------
# 结构化步骤计划：前端据此渲染竖向进度条，并在出错时高亮失败步骤。
PDF_DECOMPOSE_STEPS = [
    {"key": "render_pages", "label": "渲染 PDF 高清页面"},
    {"key": "extract_text", "label": "提取文本与公式"},
    {"key": "ai_split", "label": "大模型拆解题目"},
    {"key": "post_process", "label": "后处理与属性标注"},
    {"key": "done", "label": "完成导入"},
]
DOCX_DECOMPOSE_STEPS = [
    {"key": "extract_docx", "label": "安全提取 Word 内容"},
    {"key": "ai_split", "label": "大模型拆解题目"},
    {"key": "post_process", "label": "后处理与属性标注"},
    {"key": "done", "label": "完成导入"},
]

# ----------------- PDF Upload & Task Routing Endpoints -----------------

@app.post("/api/upload/pdf-task")
def upload_pdf_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
    page_range: Optional[str] = Form(None),
    pdf_strategy: str = Form("native_preferred"),
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .pdf 格式！"},
                status_code=400
            )
            
        # Incremental cap avoids loading an arbitrarily large multipart file.
        try:
            content = read_stream_limited(file.file, MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "PDF 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if not content.lstrip().startswith(b"%PDF-"):
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 PDF 文档！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="任务已排队，正在准备运行异步切片分析...",
            document_type="pdf",
            temp_assets=[],
        )
        DOCUMENT_TASKS.init_steps(task_id, PDF_DECOMPOSE_STEPS)
        try:
            DOCUMENT_TASKS.submit(
                task_id,
                run_pdf_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
                page_range,
                pdf_strategy,
            )
        except TaskQueueFull as exc:
            DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
            )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 PDF 解析任务失败: {str(e)}"},
            status_code=500
        )


def _delete_task_temp_assets(paths: list) -> int:
    """Delete only explicit files below this instance's upload tmp directory."""
    removed = 0
    tmp_root = Path(TMP_UPLOAD_DIR).resolve()
    for url in paths or []:
        try:
            full_path = resolve_upload_asset(
                str(url),
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
                require_file=False,
            )
        except AssetSecurityError:
            continue
        if full_path.parent != tmp_root:
            continue
        if full_path.is_file():
            try:
                full_path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# Start only after the cleanup callback above exists.  The one-minute sweep
# bounds a one-hour terminal TTL even when no later request creates a task.
if not IS_TESTING:
    DOCUMENT_TASKS.start_maintenance(interval_seconds=60.0)


def _render_pdf_bytes_to_page_images(pdf_bytes, task_id, temp_assets):
    """把 PDF 字节渲染成逐页 PNG（dpi=250），返回可访问 URL 列表，并追加到 temp_assets。

    250 DPI 与 PDF 拆解链路（``_render_pdf_page_images`` 附近的拆题渲染）保持一致：
    这里的页图同时是【手动截图】的裁剪源图，``/api/ai/manual-crop-pdf`` 收到的是
    百分比坐标、后端按图片真实像素换算，因此 DPI 直接决定裁出配图的清晰度，
    不要为了省本地渲染时间把这里调低。
    """
    import fitz
    page_urls = []
    tmp_pdf_path = Path(TMP_UPLOAD_DIR) / f"{task_id}_preview.pdf"
    tmp_pdf_path.write_bytes(pdf_bytes)
    try:
        with fitz.open(tmp_pdf_path) as document:
            total_pages = len(document)
            if total_pages == 0:
                return page_urls
            for page_num in range(total_pages):
                page = document.load_page(page_num)
                pixmap = page.get_pixmap(dpi=250)
                image_filename = f"docx_page_{task_id}_{page_num}.png"
                image_path = Path(TMP_UPLOAD_DIR) / image_filename
                pixmap.save(image_path)
                image_url = f"/{UPLOAD_DIR_REL}/tmp/{image_filename}"
                page_urls.append(image_url)
                temp_assets.append(image_url)
    finally:
        tmp_pdf_path.unlink(missing_ok=True)
    return page_urls


def run_docx_parsing_task(
    task_id: str,
    file_bytes: bytes,
    filename: str,
    generate_answers: bool = False,
):
    temp_assets = []
    current_step = "extract_docx"
    try:
        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.update(
            task_id,
            status="extracting_docx",
            progress=25,
            log="已接收 Word 试卷，正在安全提取 OMML 公式、文字与配图...",
            document_type="docx",
            temp_assets=[],
        )
        DOCUMENT_TASKS.step_start(task_id, current_step)

        # 2. 安全提取 Word Markdown；资产先放入 tmp，入库时再晋升。
        docx_res = extract_docx_markdown(
            file_bytes,
            output_dir=TMP_UPLOAD_DIR,
            url_prefix=f"/{UPLOAD_DIR_REL}/tmp",
            asset_prefix=f"word_{task_id}",
        )
        temp_assets = docx_res.get("image_paths", [])
        if not docx_res.get("success") or not docx_res.get("markdown"):
            raise ValueError(docx_res.get("error") or "未能从 Word 文档中提取出有效试题内容！")

        full_markdown_content = docx_res["markdown"]
        img_count = docx_res.get("image_count", 0)

        # Plan A：把插图长链接压成 [插图待补: 图N] 短占位符，拆题完成后再还原为真实链接，
        # 避免「模型看不到图却被强制保留长链接」造成的输入/输出双向 token 浪费。
        compressed_markdown_content, docx_img_map = compress_docx_image_links(full_markdown_content)
        diagnostics = docx_res.get("diagnostics", {})
        converted_count = diagnostics.get("omml_converted", 0) + diagnostics.get("mtef_converted", 0)
        review_count = diagnostics.get("review_required", 0)
        extraction_log = (
            f"Word 提取完成：{converted_count} 个公式已转换，{img_count} 张图片已保留"
            + (f"，{review_count} 处需人工核对。" if review_count else "，未发现需人工核对的内容。")
        )

        DOCUMENT_TASKS.check_cancelled(task_id)
        current_step = "ai_split"
        DOCUMENT_TASKS.step_start(task_id, current_step)
        DOCUMENT_TASKS.update(
            task_id,
            status="ai_splitting",
            progress=70,
            log=extraction_log + " 正在调用教研模型拆题...",
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
        )

        # 3. 智能提取标题与题目切片
        paper_title = os.path.splitext(filename)[0]
        auto_title = extract_title_from_latex(full_markdown_content)
        if auto_title:
            paper_title = auto_title

        # Keep every formula visible in-place for the model's mathematical
        # understanding, while assigning an immutable (compact) ID. The model
        # returns the ID and the server restores the exact Word-extracted source.
        locked_markdown_content, math_locks = lock_visible_math(
            compressed_markdown_content,
            task_id.replace("-", "")[:16],
        )
        diagnostics["math_locks_created"] = len(math_locks)

        # Plan A：仅当文档含插图时，把图片占位符协议注入系统提示（PDF/TEX 路径不含此说明）。
        docx_image_note = ""
        if docx_img_map:
            docx_image_note = (
                "\n【Word 文档图片协议】:\n"
                "本文 Word 试卷的图片在输入中以 [插图待补: 图1]、[插图待补: 图2]… 这样的占位符标注"
                "（已替代原始长链接以节省篇幅，原图并未丢失）。你必须原样保留这些占位符："
                "题干里图片出现的位置就写 [插图待补: 图N]，并且把对应的 [插图待补: 图N] 也列入该题目的 "
                "referenced_images 数组。系统会在拆题后自动把 [插图待补: 图N] 还原为真实图片链接，"
                "你切勿将其展开为 URL、也不要改写成其它形式。\n"
            )
        DOCUMENT_TASKS.check_cancelled(task_id)

        # 长卷（如含数千个 MathType 公式的教辅）拆解可能持续十分钟以上。
        # 逐段回报进度，避免前端在整个 ai_split 阶段看不到任何变化而误判超时。
        def _report_docx_split_progress(done, total):
            ratio = (done / total) if total else 1.0
            DOCUMENT_TASKS.update(
                task_id,
                status="ai_splitting",
                progress=70 + int(18 * ratio),
                log=f"正在调用教研模型拆题（第 {done}/{total} 段）...",
                document_type="docx",
            )

        # 自动识别「题目与解析分离」结构（零 token），替代原手动开关。
        detected_separated = detect_separated_mode(locked_markdown_content)
        DOCUMENT_TASKS.update(task_id, detected_separated=detected_separated)

        # L3 任务级告警：parse_quality 用容器透传到 DOCUMENT_TASKS，供前端徽章展示。
        _parse_quality_holder_docx: List[Dict[str, Any]] = [{}]
        # Word 链路默认走付费拆解模型：免费模型对「公式锁 [[Mn]] 协议」遵守率明显
        # 偏低（实测一份 19 题月考卷 math_locks_missing 达 116/255，且 answer_markdown
        # 全部为空），付费模型的质量收益远大于成本。想改回自动路由可在 .env 设
        # DOCX_FORCE_PAID_PARSE=false。
        _docx_force_paid = os.getenv("DOCX_FORCE_PAID_PARSE", "true").strip().lower() not in {
            "0", "false", "no", "off", ""
        }
        parsed_questions = parse_paper_text_internal(
            locked_markdown_content,
            generate_answers,
            separated_mode=detected_separated,
            progress_callback=_report_docx_split_progress,
            extra_system_note=docx_image_note,
            formula_lock=True,
            quality_out=_parse_quality_holder_docx,
            force_paid=_docx_force_paid,
            paper_title=paper_title,
        )
        parse_quality = _parse_quality_holder_docx[0] or None
        lock_report = restore_visible_math(parsed_questions, math_locks, strict=False)
        diagnostics.update(lock_report)
        if "warnings" in lock_report:
            diagnostics.setdefault("warnings", []).extend(lock_report["warnings"])

        # Plan A：把 [插图待补: 图N] 占位符还原为真实图片链接，并校验是否丢失。
        if docx_img_map:
            img_warnings = decompress_docx_image_links_in_questions(parsed_questions, docx_img_map)
            if img_warnings:
                diagnostics.setdefault("warnings", []).extend(img_warnings)

        DOCUMENT_TASKS.check_cancelled(task_id)
        current_step = "post_process"
        DOCUMENT_TASKS.step_start(task_id, current_step)
        DOCUMENT_TASKS.update(
            task_id,
            status="post_processing",
            progress=90,
            log="题目已拆分完成，正在整理卡片、图片与知识点标注...",
            document_type="docx",
        )
        final_questions = post_process_pdf_parsed_questions(parsed_questions, paper_title, task_id, [full_markdown_content])
        DOCUMENT_TASKS.check_cancelled(task_id)
        DOCUMENT_TASKS.step_complete_all(task_id)
        # step_complete_all 只清状态字典里的 current_step，不会改这里的局部变量。
        # 显式改写，避免后续收尾环节出错时被误报成「post_process 步骤失败」。
        current_step = "finalize"
        DOCUMENT_TASKS.update(
            task_id,
            progress=95,
            log="正在生成原卷预览图并做公式保真校验...",
            document_type="docx",
        )

        # 方案C：标记公式可能异常的单题（供前端卡片红色警告）
        from mathbank.docx_helper import detect_mathtype_garbage_residual
        for _q in final_questions:
            _c = _q.get("content", "") or ""
            _a = _q.get("answer_markdown", "") or ""
            if detect_mathtype_garbage_residual(_c) or detect_mathtype_garbage_residual(_a):
                _q["needs_review"] = True

        # 生成原卷预览页图（与 PDF 一致，供编辑页内嵌“查看原文件”对照核对）
        page_images: list[str] = []
        import subprocess
        try:
            soffice = _find_soffice()
            if soffice:
                DOCUMENT_TASKS.update(
                    task_id,
                    log="正在生成原卷预览图，便于对照核对公式...",
                    document_type="docx",
                )
                src_docx_path = Path(TMP_UPLOAD_DIR) / f"{task_id}.docx"
                # 方案A：把 docx 字体声明归一化为 Arial Unicode MS（macOS 上唯一
                # 同时覆盖中文+数学符号的字体，详见 mathbank/docx_font_normalizer.py
                # 顶部表），让 LO 转 PDF 时真正把它嵌入子集，不再退回到 Liberation Sans
                # 等无 CJK 的 fallback。失败时静默回退原始 docx（主解析流程不受影响，
                # 仅原卷预览图可能仍含方框）。
                try:
                    normalized_docx_bytes = normalize_docx_fonts(file_bytes)
                except Exception as norm_exc:
                    diagnostics.setdefault("warnings", []).append(
                        f"docx 字体归一化失败（已回退原始 docx）: {norm_exc}"
                    )
                    normalized_docx_bytes = file_bytes

                # 方案B：把 MathType OLE 公式对象预渲染为 PNG 图片，避免 macOS LO
                # headless 无法调用 MathType 引擎导致公式区域显示为空白方框。
                try:
                    from mathbank.mathtype_ole_renderer import replace_mathtype_ole_with_images
                    normalized_docx_bytes = replace_mathtype_ole_with_images(normalized_docx_bytes)
                except Exception as ole_exc:
                    diagnostics.setdefault("warnings", []).append(
                        f"MathType 公式预渲染失败（已回退原始 docx）: {ole_exc}"
                    )

                src_docx_path.write_bytes(normalized_docx_bytes)
                try:
                    # 注入项目独立 LibreOffice profile（OnScreenOnly 字体替换表，
                    # 仅影响屏显）；PDF 字形正确性由上方 normalize_docx_fonts 保证。
                    subprocess.run(
                        _build_soffice_command(
                            soffice,
                            "--headless", "--norestore", "--convert-to", "pdf",
                            "--outdir", TMP_UPLOAD_DIR, str(src_docx_path),
                        ),
                        capture_output=True, timeout=240,
                    )
                except subprocess.TimeoutExpired:
                    diagnostics.setdefault("warnings", []).append("LibreOffice 转换超时,未生成原卷预览图。")
                pdf_path = Path(TMP_UPLOAD_DIR) / f"{task_id}.pdf"
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    page_images = _render_pdf_bytes_to_page_images(pdf_path.read_bytes(), task_id, temp_assets)
                    # 方案C：逐页提取转 PDF 后的物理文本，按文本重合度把每题定位到对应页，
                    # 写 source_page 供手动截图直接跳到本题所在页（page_images 与文本页序一致）。
                    try:
                        import fitz as _fitz
                        with _fitz.open(pdf_path) as _doc:
                            _docx_page_texts = [_doc.load_page(i).get_text() for i in range(len(_doc))]
                        assign_source_pages_by_overlap(final_questions, _docx_page_texts)
                    except Exception as _e:
                        print(f"[DOCX source_page] 页码映射失败（不影响入库）: {_e}")
                src_docx_path.unlink(missing_ok=True)
                pdf_path.unlink(missing_ok=True)
            else:
                diagnostics.setdefault("warnings", []).append(
                    "未找到 LibreOffice，无法生成原卷预览图（不影响拆题，可在编辑页手动核对）。"
                )
        except Exception as exc:
            diagnostics.setdefault("warnings", []).append(f"生成原卷预览图失败：{exc}")

        DOCUMENT_TASKS.complete(
            task_id,
            log="完成！已提取并拆分 Word 题目，请优先检查带“公式待核对”标记的内容。" if review_count else "完成！已提取并拆分全部 Word 题目卡片。",
            data=final_questions,
            document_type="docx",
            diagnostics=diagnostics,
            temp_assets=list(temp_assets),
            page_images=list(page_images),
            parse_quality=parse_quality,
        )
    except TaskCancelled:
        _delete_task_temp_assets(temp_assets)
    except Exception as ex:
        _delete_task_temp_assets(temp_assets)
        DOCUMENT_TASKS.step_error(
            task_id,
            current_step,
            f"Word 试卷拆解失败: {str(ex)}",
            document_type="docx",
        )


@app.post("/api/upload/docx-task")
def upload_docx_task(
    file: UploadFile = File(...),
    generate_answers: str = Form("false"),
):
    try:
        generate_answers_bool = generate_answers.lower() in ("true", "1", "yes")
        
        # 验证文件扩展名
        filename = file.filename or ""
        if not filename.lower().endswith(".docx"):
            return JSONResponse(
                content={"status": "error", "message": "上传文件格式不正确，必须为 .docx 格式！"},
                status_code=400
            )
            
        try:
            content = read_stream_limited(file.file, MAX_PDF_BYTES)
        except UploadTooLargeError:
            return JSONResponse(
                content={"status": "error", "message": "Word 文件过大，请上传 30MB 以内的试卷文件！"},
                status_code=413
            )
        if len(content) < 4 or content[:4] != b"PK\x03\x04":
            return JSONResponse(
                content={"status": "error", "message": "文件内容不是有效的 Word DOCX 压缩包！"},
                status_code=400,
            )
            
        task_id = str(uuid.uuid4())
        
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="Word 任务已排队，正在准备安全提取公式与配图...",
            document_type="docx",
            temp_assets=[],
        )
        DOCUMENT_TASKS.init_steps(task_id, DOCX_DECOMPOSE_STEPS)
        try:
            DOCUMENT_TASKS.submit(
                task_id,
                run_docx_parsing_task,
                task_id,
                content,
                filename,
                generate_answers_bool,
            )
        except TaskQueueFull as exc:
            DOCUMENT_TASKS.remove(task_id)
            return JSONResponse(
                content={"status": "error", "message": str(exc)},
                status_code=429,
            )
        
        return {
            "status": "success",
            "task_id": task_id
        }
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"创建 Word 解析任务失败: {str(e)}"},
            status_code=500
        )


@app.get("/api/tasks/{task_id}/status")
def get_pdf_task_status(task_id: str):
    task = DOCUMENT_TASKS.snapshot(task_id)
    if not task:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )
    return task


@app.post("/api/tasks/{task_id}/cancel")
def cancel_pdf_task(task_id: str):
    current = DOCUMENT_TASKS.snapshot(task_id)
    if current is None:
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404
        )

    current_status = current.get("status")
    if current_status in {"completed", "error"}:
        # Never delete assets belonging to a task that already produced a
        # result.  The previous behavior reported success and could remove
        # completed PDF crop files after a late ESC/click.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": current_status,
            },
            status_code=409,
        )
    if current_status == "cancelled":
        return {
            "status": "success",
            "message": "任务已中止。",
            "task_status": "cancelled",
        }

    task = DOCUMENT_TASKS.cancel(task_id)
    if task is None:  # Defensive race guard; records are not normally removed here.
        return JSONResponse(
            content={"status": "error", "message": "未找到对应的任务 ID！"},
            status_code=404,
        )
    if task.get("status") != "cancelled":
        # The worker may have completed between the snapshot above and the
        # atomic cancel call.  Never delete assets from that completed result.
        return JSONResponse(
            content={
                "status": "error",
                "message": "任务已结束，无法再中止。",
                "task_status": task.get("status"),
            },
            status_code=409,
        )
    removed = _delete_task_temp_assets(list(task.get("temp_assets", [])))
    return {
        "status": "success",
        "message": f"任务已成功中止，已清理 {removed} 个临时资产",
        "task_status": "cancelled",
    }


@app.post("/api/ai/clear-temp-crops")
def clear_temp_crops(payload: dict):
    """物理删除传递来的未入库临时裁剪图片路径"""
    try:
        paths = payload.get("paths", [])
        if not isinstance(paths, list):
            raise ValueError("paths 必须是数组。")
        removed_count = _delete_task_temp_assets(paths)
        return {"status": "success", "message": f"成功物理清除 {removed_count} 张废弃插图图片。"}
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"清理临时插图出错: {str(e)}"},
            status_code=500
        )


# ----------------- Paper Generation API Endpoints -----------------

@app.get("/api/paper/questions")
def get_paper_questions(ids: str = "", db: Session = Depends(get_db)):
    """获取指定 ID 列表的完整题目数据（组卷试题篮批量拉取）"""
    if not ids:
        return {"status": "success", "data": []}
    try:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
        if not id_list:
            return {"status": "success", "data": []}
        questions = db.query(Question).filter(Question.id.in_(id_list)).all()
        q_map = {q.id: q.to_dict() for q in questions}
        result = [q_map[qid] for qid in id_list if qid in q_map]
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/paper/save-template")
def save_paper_template(payload: dict, db: Session = Depends(get_db)):
    """保存个人组卷预设（模板）：仅存 meta + 细目表 specRows，不含具体题目。"""
    try:
        if not isinstance(payload, dict):
            raise ValueError("模板数据格式不正确。")
        name = str(payload.get("name", "未命名模板")).strip()
        if not name or len(name) > 200:
            raise ValueError("模板名称不能为空且不超过 200 字。")
        meta = payload.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}
        spec_rows = payload.get("spec_rows", [])
        if not isinstance(spec_rows, list):
            spec_rows = []

        tpl_meta = {
            "spec_rows": spec_rows,
            "paper_type": meta.get("paper_type", "exam"),
            "total_score_target": meta.get("total_score_target", ""),
            "solution_space_default": meta.get("solution_space_default", "7.0"),
        }
        paper = Paper(
            title=name,
            subtitle="",
            paper_type=meta.get("paper_type", "exam"),
            total_score=int(meta.get("total_score_target", 0) or 0),
            metadata_json=json.dumps(tpl_meta, ensure_ascii=False),
            is_template=1,
        )
        db.add(paper)
        db.commit()
        return {"status": "success", "message": "模板已保存", "template_id": paper.id}
    except (TypeError, ValueError) as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=400)
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": f"保存模板失败: {str(e)}"}, status_code=500)


@app.get("/api/paper/templates")
def list_paper_templates(db: Session = Depends(get_db)):
    """列出个人保存的组卷预设模板（不含内置模板）。"""
    try:
        rows = db.query(Paper).filter(Paper.is_template == 1).order_by(Paper.created_at.desc()).all()
        result = []
        for r in rows:
            meta = {}
            try:
                meta = json.loads(r.metadata_json or "{}")
            except Exception:
                meta = {}
            result.append({
                "id": r.id,
                "name": r.title,
                "paper_type": r.paper_type,
                "total_score_target": meta.get("total_score_target", ""),
                "spec_rows": meta.get("spec_rows", []),
                "created_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
            })
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


@app.delete("/api/paper/template/{template_id}")
def delete_paper_template(template_id: int, db: Session = Depends(get_db)):
    try:
        tpl = db.query(Paper).filter(Paper.id == template_id, Paper.is_template == 1).first()
        if not tpl:
            return JSONResponse(content={"status": "error", "message": "模板不存在"}, status_code=404)
        db.delete(tpl)
        db.commit()
        return {"status": "success", "message": "模板已删除"}
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/paper/save")
def save_paper(payload: dict, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """保存排版好的试卷，自增被选中题目的 usage_count"""
    try:
        if not isinstance(payload, dict):
            raise ValueError("试卷数据格式不正确。")
        title = str(payload.get("title", "未命名试卷")).strip()
        subtitle = str(payload.get("subtitle", "")).strip()
        paper_type = payload.get("paper_type", "exam")
        questions_payload = payload.get("questions", [])

        if not title or len(title) > 200 or len(subtitle) > 200:
            raise ValueError("试卷标题不能为空，且标题与副标题均不能超过 200 字。")
        if paper_type not in {"exam", "quiz", "exam_19"}:
            raise ValueError("不支持的试卷模板。")
        if not isinstance(questions_payload, list) or not questions_payload:
            raise ValueError("试卷中至少需要包含一道题目。")
        if len(questions_payload) > 200:
            raise ValueError("单份试卷不能超过 200 道题。")

        normalized_items = []
        seen_question_ids = set()
        for item in questions_payload:
            if not isinstance(item, dict):
                raise ValueError("试卷题目数据格式不正确。")
            question_id = int(item.get("id"))
            score = int(item.get("score", 5))
            if question_id <= 0 or score < 0 or score > 100:
                raise ValueError("题目 ID 或分值不在有效范围内。")
            if question_id in seen_question_ids:
                raise ValueError("同一道题不能在一份试卷中重复出现。")
            seen_question_ids.add(question_id)
            normalized_items.append((question_id, score))

        questions = db.query(Question).filter(
            Question.id.in_(seen_question_ids)
        ).all()
        question_map = {question.id: question for question in questions}
        missing_ids = sorted(seen_question_ids - set(question_map))
        if missing_ids:
            raise ValueError("试卷中包含已删除或不存在的题目。")

        total_score = sum(score for _question_id, score in normalized_items)
        
        meta = payload.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        meta["show_secret"] = payload.get("show_secret", True)
        meta["show_notice"] = payload.get("show_notice", True)
        # 抬头学科行与考试用时都没有独立列，随 metadata_json 一起归档，
        # 读回时由 Paper.to_dict 还原 —— 这样不必为两个展示字段做一次库迁移。
        meta["subject_line"] = str(payload.get("subject_line") or "").strip()
        try:
            exam_duration = int(payload.get("exam_duration", 120))
        except (TypeError, ValueError):
            exam_duration = 120
        meta["exam_duration"] = exam_duration if 0 < exam_duration <= 600 else 120

        paper = Paper(
            title=title,
            subtitle=subtitle,
            paper_type=paper_type,
            total_score=total_score,
            metadata_json=json.dumps(meta)
        )
        db.add(paper)
        db.flush()
        
        for idx, (qid, score) in enumerate(normalized_items):
            pq = PaperQuestion(
                paper_id=paper.id,
                question_id=qid,
                order_index=idx + 1,
                score=score
            )
            db.add(pq)
            question = question_map[qid]
            question.usage_count = (question.usage_count or 0) + 1
                
        db.commit()
        schedule_database_export(background_tasks, operation="save_paper")
        return {"status": "success", "message": "试卷保存成功！", "paper_id": paper.id}
    except (TypeError, ValueError) as e:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(e)}, status_code=400
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": f"保存试卷失败: {str(e)}"}, status_code=500)

@app.get("/api/papers")
def list_papers(db: Session = Depends(get_db)):
    """获取所有历史试卷列表"""
    try:
        from sqlalchemy import func

        rows = (
            db.query(Paper, func.count(PaperQuestion.id))
            .outerjoin(PaperQuestion, PaperQuestion.paper_id == Paper.id)
            .group_by(Paper.id)
            .order_by(Paper.created_at.desc())
            .all()
        )
        result = []
        for p, q_count in rows:
            d = p.to_dict()
            d["question_count"] = int(q_count)
            result.append(d)
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/papers/{paper_id}")
def get_paper_detail(paper_id: int, db: Session = Depends(get_db)):
    """获取单张试卷的详细信息及关联题目列表（用于一键载入）"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
        
        rows = (
            db.query(PaperQuestion, Question)
            .join(Question, Question.id == PaperQuestion.question_id)
            .filter(PaperQuestion.paper_id == paper_id)
            .order_by(PaperQuestion.order_index.asc())
            .all()
        )

        questions_list = [
            {
                "id": question.id,
                "score": paper_question.score,
                "question": question.to_dict(),
            }
            for paper_question, question in rows
        ]
                
        result = paper.to_dict()
        result["questions"] = questions_list
        return {"status": "success", "data": result}
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """删除指定的历史试卷，并同步扣减关联题目的 usage_count"""
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            return JSONResponse(content={"status": "error", "message": "试卷不存在"}, status_code=404)
            
        pqs = db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).all()
        reference_counts = {}
        for paper_question in pqs:
            reference_counts[paper_question.question_id] = (
                reference_counts.get(paper_question.question_id, 0) + 1
            )
        questions = db.query(Question).filter(
            Question.id.in_(reference_counts)
        ).all()
        for question in questions:
            if question.usage_count:
                question.usage_count = max(
                    0,
                    question.usage_count - reference_counts.get(question.id, 0),
                )
                
        db.query(PaperQuestion).filter(PaperQuestion.paper_id == paper_id).delete()
        db.delete(paper)
        db.commit()
        schedule_database_export(background_tasks, operation="delete_paper")
        return {"status": "success", "message": "试卷记录已成功删除"}
    except Exception as e:
        db.rollback()
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

def _paper_is_multi_subject(questions_data: list) -> bool:
    """卷内是否含 ≥2 个学科。

    一份卷子可以同时考数学 / 物理 / 化学。这时 exam-19 模板不适用 —— 它的题号锚点
    （单选 1 / 多选 9 / 填空 12 / 解答 15）和配套的 A3 答题卡题块都是数学 19 题卷结构，
    混入物化后题号会跳着走、答题卡也对不上题。
    """

    subjects = {
        normalize_subject((item.get("question") or {}).get("subject"))
        for item in questions_data
    }
    return len(subjects) > 1


def _collect_paper_questions(db: Session, questions_input: list) -> tuple[list, list]:
    """把试题篮 ``[{id, score, ...}]`` 展开成导出用的题目列表。

    返回 ``(questions_data, missing_ids)``。

    旧实现在四个导出端点里各写一份 ``if qid in q_map:`` —— 没有 else 分支，
    试题篮里指向已删除题目的 id 会被无声跳过：导出照常成功，卷子里少一道题，
    而用户看不到任何提示。这里把四处收敛成一份实现，并把「缺失」明确回传给
    调用方，由调用方报错中止导出。
    """

    q_ids = [int(item.get("id")) for item in questions_input if item.get("id")]
    q_map: dict = {}
    if q_ids:
        q_map = {
            q.id: q.to_dict()
            for q in db.query(Question).filter(Question.id.in_(q_ids)).all()
        }

    questions_data: list = []
    missing_ids: list = []
    for item in questions_input:
        raw_id = item.get("id")
        if not raw_id:
            continue
        qid = int(raw_id)
        if qid not in q_map:
            if qid not in missing_ids:
                missing_ids.append(qid)
            continue
        q_dict = dict(q_map[qid])
        if item.get("figure_align"):
            q_dict["figure_align"] = item.get("figure_align")
        q_item = {
            "question": q_dict,
            "score": int(item.get("score", 5)),
        }
        if item.get("solution_space"):
            q_item["solution_space"] = item.get("solution_space")
        questions_data.append(q_item)
    return questions_data, missing_ids


@app.post("/api/paper/export/tex")
def export_paper_tex(payload: dict, db: Session = Depends(get_db)):
    """导出 LaTeX 源码 ZIP 压缩包"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        subject_line = payload.get("subject_line", "")
        exam_duration = payload.get("exam_duration", 120)
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        questions_data, missing_q_ids = _collect_paper_questions(db, questions_input)
        if missing_q_ids:
            return JSONResponse(content={
                "status": "error",
                "message": (
                    f"试题篮中有 {len(missing_q_ids)} 道题已不在题库中（可能已被删除）："
                    + "、".join(f"#{i}" for i in missing_q_ids)
                    + "。为避免静默丢题，本次导出已中止；请把这些题从试题篮移除后重试。"
                ),
            }, status_code=400)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice, subject_line=subject_line, exam_duration=exam_duration)
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice, subject_line=subject_line, exam_duration=exam_duration)
        
        if paper_type == "exam_19" and not _paper_is_multi_subject(questions_data):
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)
        zip_bytes = create_tex_zip_package(title, tex_main, tex_ans, image_paths, answer_sheet_tex=tex_answer_sheet)
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        encoded_filename = quote(f"{safe_title}.zip")
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_export.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成 LaTeX 源码失败: {str(e)}"}, status_code=500)

@app.post("/api/paper/export/bundle")
def export_paper_bundle(payload: dict, db: Session = Depends(get_db)):
    """一键导出合并全套 Zip 压缩包（包含 LaTeX 源码、相关插图以及已编译好的 PDF）"""
    try:
        # 若系统未安装 xelatex，提前给出明确提示，而不是静默返回一个缺失 PDF 的压缩包。
        if shutil.which("xelatex") is None:
            return JSONResponse(content={
                "status": "warning",
                "message": (
                    "系统未检测到 xelatex 编译器（需安装 TeX Live / MacTeX 并加入 PATH），"
                    "无法编译 PDF。请安装排版工具链后重试。"
                ),
            }, status_code=200)
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        subject_line = payload.get("subject_line", "")
        exam_duration = payload.get("exam_duration", 120)
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        questions_data, missing_q_ids = _collect_paper_questions(db, questions_input)
        if missing_q_ids:
            return JSONResponse(content={
                "status": "error",
                "message": (
                    f"试题篮中有 {len(missing_q_ids)} 道题已不在题库中（可能已被删除）："
                    + "、".join(f"#{i}" for i in missing_q_ids)
                    + "。为避免静默丢题，本次导出已中止；请把这些题从试题篮移除后重试。"
                ),
            }, status_code=400)
                
        tex_main = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=False, show_secret=show_secret, show_notice=show_notice, subject_line=subject_line, exam_duration=exam_duration)
        tex_ans = build_latex_document(title + " (参考答案与解析)", subtitle, paper_type, questions_data, include_answers=True, show_secret=show_secret, show_notice=show_notice, subject_line=subject_line, exam_duration=exam_duration)
        
        if paper_type == "exam_19" and not _paper_is_multi_subject(questions_data):
            tex_answer_sheet = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_answer_sheet = None

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)

        # Pre-compile PDFs
        main_pdf_bytes, _ = compile_tex_to_pdf(tex_main, image_paths)
        ans_pdf_bytes, _ = compile_tex_to_pdf(tex_ans, image_paths)
        if paper_type == "exam_19" and tex_answer_sheet:
            answer_sheet_pdf_bytes, _ = compile_tex_to_pdf(tex_answer_sheet, image_paths)
        else:
            answer_sheet_pdf_bytes = None

        zip_bytes = create_full_bundle_zip_package(
            title, tex_main, tex_ans, image_paths,
            answer_sheet_tex=tex_answer_sheet,
            main_pdf_bytes=main_pdf_bytes,
            ans_pdf_bytes=ans_pdf_bytes,
            answer_sheet_pdf_bytes=answer_sheet_pdf_bytes
        )
        
        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', '_', title.strip()) or "试卷"
        filename = f"{safe_title}_全套归档.zip"
        encoded_filename = quote(filename)
        return Response(content=zip_bytes, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=\"paper_bundle.zip\"; filename*=utf-8''{encoded_filename}"
        })
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"生成全套合并包失败: {str(e)}"}, status_code=500)

def explain_latex_compile_error(log_text: str, tex_content: str) -> dict:
    """Explain one compile failure locally, then enrich it with the parse model."""
    diagnostic = build_local_latex_diagnostic(log_text, tex_content)
    parse_model = os.getenv("PREFER_PARSE_MODEL") or os.getenv(
        "DEEPSEEK_PARSE_MODEL", "deepseek-flash"
    )
    provider = resolve_text_provider(parse_model)
    if not provider.api_key:
        diagnostic["ai_note"] = "试卷拆解模型未配置，当前显示本地诊断结果。"
        return diagnostic

    system_prompt, user_prompt = build_latex_error_explanation_prompts(diagnostic)
    payload = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    is_deepseek = (
        "deepseek" in provider.model_name.lower()
        or "deepseek" in (provider.api_base or "").lower()
    ) and provider.model_name not in {"deepseek-chat", "deepseek-reasoner"}
    if is_deepseek and provider.reasoning_effort in {None, "default"}:
        payload["thinking"] = {"type": "disabled"}
    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="latex_diagnostic",
    )

    try:
        response = post_chat_completion(
            provider,
            payload,
            timeout=45,
            provider_name=provider.provider_label,
        )
        raw_text = response.json()["choices"][0]["message"]["content"].strip()
        ai_value = parse_ai_json(raw_text)
        return merge_ai_latex_diagnostic(diagnostic, ai_value)
    except Exception:
        diagnostic["ai_note"] = "AI 暂时无法解释该错误，当前显示本地诊断结果。"
        return diagnostic


@app.post("/api/paper/export/pdf")
def export_paper_pdf(payload: dict, db: Session = Depends(get_db)):
    """在线静默编译生成高清 PDF"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        subject_line = payload.get("subject_line", "")
        exam_duration = payload.get("exam_duration", 120)
        paper_type = payload.get("paper_type", "exam_19")
        target = payload.get("target", "paper")  # "paper" or "sheet"
        include_answers = payload.get("include_answers", False)
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        
        questions_data, missing_q_ids = _collect_paper_questions(db, questions_input)
        if missing_q_ids:
            return JSONResponse(content={
                "status": "error",
                "message": (
                    f"试题篮中有 {len(missing_q_ids)} 道题已不在题库中（可能已被删除）："
                    + "、".join(f"#{i}" for i in missing_q_ids)
                    + "。为避免静默丢题，本次导出已中止；请把这些题从试题篮移除后重试。"
                ),
            }, status_code=400)
                
        if target == "sheet":
            # A3 答题卡是按数学 19 题卷切块的，卡面还写死「数学答题卡」，混科套不出可用的卡。
            # 宁可明确拒绝，也不要吐一张对不上题的答题卡 —— 用户会拿它去印。
            if _paper_is_multi_subject(questions_data):
                return JSONResponse(content={
                    "status": "error",
                    "message": "卷内含多个学科，A3 答题卡只适用于数学 19 题卷。请改用试卷 PDF / Word 导出。",
                }, status_code=400)
            tex_content = build_answer_sheet_latex(title, subtitle, questions_data)
        else:
            tex_content = build_latex_document(title, subtitle, paper_type, questions_data, include_answers=include_answers, show_secret=show_secret, show_notice=show_notice, subject_line=subject_line, exam_duration=exam_duration)

        image_paths = collect_referenced_images(questions_data, UPLOAD_DIR, UPLOAD_DIR_REL)
        pdf_bytes, log_or_err = compile_tex_to_pdf(tex_content, image_paths)

        if pdf_bytes:
            filename = f"sheet_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf" if target == "sheet" else f"paper_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            return Response(content=pdf_bytes, media_type="application/pdf", headers={
                "Content-Disposition": f'inline; filename="{filename}"'
            })
        else:
            # If the compiler is simply missing, return a clean, AI-free
            # diagnostic immediately instead of risking a slow/failing model call.
            if is_xelatex_missing(log_or_err or ""):
                diagnostic = build_local_latex_diagnostic(log_or_err or "", tex_content)
            else:
                diagnostic = explain_latex_compile_error(log_or_err, tex_content)
            # Surface the raw source + full compiler log so the user can debug
            # even when the local diagnostic cannot pinpoint the offending line.
            diagnostic.setdefault("tex_source", tex_content)
            diagnostic.setdefault("full_log", (log_or_err or "")[:6000])
            return JSONResponse(
                content={
                    "status": "error",
                    "message": diagnostic.get("summary", "PDF 编译失败"),
                    "diagnostic": diagnostic,
                },
                status_code=400,
            )
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": f"编译 PDF 异常: {str(e)}"}, status_code=500)


@app.post("/api/paper/export/word")
def export_paper_word(payload: dict, db: Session = Depends(get_db)):
    """导出包含试卷正文与含答案解析两个 Word 文件的 ZIP 压缩包。"""
    try:
        title = payload.get("title", "2026年高中数学模拟考试试卷")
        subtitle = payload.get("subtitle", "")
        subject_line = payload.get("subject_line", "")
        exam_duration = payload.get("exam_duration", 120)
        paper_type = payload.get("paper_type", "exam")
        show_secret = payload.get("show_secret", True)
        show_notice = payload.get("show_notice", True)
        questions_input = payload.get("questions", [])
        as_single_docx = bool(payload.get("as_single_docx", False))
        include_answers = bool(payload.get("include_answers", False))

        questions_data, missing_q_ids = _collect_paper_questions(db, questions_input)
        if missing_q_ids:
            return JSONResponse(content={
                "status": "error",
                "message": (
                    f"试题篮中有 {len(missing_q_ids)} 道题已不在题库中（可能已被删除）："
                    + "、".join(f"#{i}" for i in missing_q_ids)
                    + "。为避免静默丢题，本次导出已中止；请把这些题从试题篮移除后重试。"
                ),
            }, status_code=400)

        if not questions_data:
            return JSONResponse(
                content={"status": "error", "message": "卷面为空，无法导出 Word。"},
                status_code=400,
            )

        from urllib.parse import quote
        safe_title = re.sub(r'[/\\?%*:|"<>]', "_", title.strip()) or "试卷"

        if as_single_docx:
            docx_bytes, diagnostics = build_word_document(
                title,
                subtitle,
                paper_type,
                questions_data,
                include_answers=include_answers,
                show_secret=show_secret,
                show_notice=show_notice,
                uploads_dir=UPLOAD_DIR,
                subject_line=subject_line,
                exam_duration=exam_duration,
            )
            suffix = "_含答案与解析" if include_answers else ""
            filename = f"{safe_title}{suffix}.docx"
            encoded_filename = quote(filename)
            return Response(
                content=docx_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={
                    "Content-Disposition": f"attachment; filename=\"paper.docx\"; filename*=utf-8''{encoded_filename}",
                    "X-Word-Native-Formulas": str(diagnostics.get("native_formulas", 0)),
                    "X-Word-Fallback-Formulas": str(diagnostics.get("fallback_formulas", 0)),
                    "X-Word-Failed-Formulas": str(diagnostics.get("failed_formulas", 0)),
                    "X-Word-Missing-Images": str(diagnostics.get("missing_images", 0)),
                    "X-Word-Answer-Card-Omitted": "1" if diagnostics.get("answer_card_omitted") else "0",
                },
            )

        # Default: build clean student docx and full teacher docx with answers into a ZIP bundle
        main_docx, main_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=False,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=UPLOAD_DIR,
            subject_line=subject_line,
            exam_duration=exam_duration,
        )
        ans_docx, ans_diag = build_word_document(
            title,
            subtitle,
            paper_type,
            questions_data,
            include_answers=True,
            show_secret=show_secret,
            show_notice=show_notice,
            uploads_dir=UPLOAD_DIR,
            subject_line=subject_line,
            exam_duration=exam_duration,
        )

        zip_bytes = create_word_bundle_zip(title, main_docx, ans_docx)
        filename = f"{safe_title}_Word打包.zip"
        encoded_filename = quote(filename)
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=\"paper_word_bundle.zip\"; filename*=utf-8''{encoded_filename}",
                "X-Word-Native-Formulas": str(main_diag.get("native_formulas", 0) + ans_diag.get("native_formulas", 0)),
                "X-Word-Fallback-Formulas": str(main_diag.get("fallback_formulas", 0) + ans_diag.get("fallback_formulas", 0)),
                "X-Word-Failed-Formulas": str(main_diag.get("failed_formulas", 0) + ans_diag.get("failed_formulas", 0)),
                "X-Word-Missing-Images": str(main_diag.get("missing_images", 0) + ans_diag.get("missing_images", 0)),
                "X-Word-Answer-Card-Omitted": "1" if main_diag.get("answer_card_omitted") else "0",
            },
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"生成 Word 试卷包失败: {str(e)}"},
            status_code=500,
        )

# ----------------- 错题扫描工作台（扫描 → 切题 → 点选 → 识别 → 错题本 / 入库） -----------------
# 设计依据：docs/错题扫描与错题本-实施计划-2026-09-13.md
#
# 与题库解耦：三张新表（students / mistake_batches / mistake_records）不碰 questions /
# papers。错题是「某学生某次做错的记录」，题库题是「可复用的备课素材」，两者生命周期
# 不同（题库题会被编辑、删除），所以错题本导出只读快照字段，不 join questions。
#
# 上游前置（实施计划 §7）：去手写由用户在系统外用专门工具完成。本链路**不做任何图像
# 去笔迹处理**，收到的图只含印刷体；上游效果不佳就回上游重做，系统不做事后补救。
#
# 切块复用 mathbank.page_block_split（纯 Pillow、零 token、确定性）；识别复用既有的
# 多模态引擎链（resolve_ocr_fallbacks 的免费优先顺序 + 故障转移），不新造模型配置。

MISTAKE_CUT_STEPS = [
    {"key": "render_pages", "label": "转页图"},
    {"key": "line_projection", "label": "判栏 + 行投影切块"},
    {"key": "merge_blocks", "label": "合并题块并落盘"},
]
#: 识别任务的步骤。原来「后处理归一」是独立一步：模型结果先攒在内存里，整批跑完再
#: 统一归一落库。改成逐题落库之后归一已并入识别循环，这一步不存在了 —— 留着它，步骤条
#: 会永远停在「后处理归一」上不前进，而且中途看不到任何题面。
MISTAKE_RECOGNIZE_STEPS = [
    {"key": "recognize_blocks", "label": "逐块识别题面"},
    {"key": "write_records", "label": "写入记录"},
]

#: 单批次源文件体积上限。刻意不复用单图 10 MB 限制 —— 真实样本（扫描全能王 12 页 PDF）
#: 是 7.6 MB，一份几十页的卷子很容易突破 10 MB。
MAX_MISTAKE_SOURCE_BYTES = 50 * 1024 * 1024
#: 批次工作目录在 static/uploads 下的子目录名
MISTAKE_UPLOAD_SUBDIR = "mistakes"
#: 默认学生。姓名可被新建批次面板里的输入覆盖（见 ensure_mistake_student），
#: 之所以不硬编码真实姓名：这个仓库要开源，个人姓名不该写进代码。
DEFAULT_MISTAKE_STUDENT_NAME = os.getenv("MISTAKE_STUDENT_NAME", "默认学生")
DEFAULT_MISTAKE_STUDENT_GRADE = os.getenv("MISTAKE_STUDENT_GRADE", "高一")
MISTAKE_SUBJECT_LABELS = {
    "math": "数学",
    "physics": "物理",
    "chemistry": "化学",
    "other": "其他",
}
MISTAKE_SUBJECTS = tuple(MISTAKE_SUBJECT_LABELS)
MISTAKE_STATUS_LABELS = {
    "pending": "待切题",
    "cutting": "切题中",
    "cut_failed": "切题失败",
    "reviewing": "待点选",
    "done": "已出 PDF",
}
MISTAKE_SOURCE_EXTENSIONS = (
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
)
GRAD_STATUS_VALUES = ("correct", "incorrect", "unknown")
MISTAKE_QUESTION_TYPES = {item["value"] for item in DEFAULT_QUESTION_TYPES}

#: 人工微调切线后重切时，从旧记录按纵向重叠继承的字段。刻意包含 question_id ——
#: 已入库的题重切后不该在题库里再落一份。
MISTAKE_CARRY_OVER_FIELDS = (
    "question_no",
    "content",
    "answer_markdown",
    "question_type",
    "difficulty",
    "knowledge_tags",
    "solve_method",
    # 分类六列（v1010）。重切会重建记录，漏了它们等于让人在审校页填的分类白填 ——
    # 与上面 question_type/difficulty 同理，属于「一条记录的用户输入」，不是块位置。
    "source",
    "category_compulsory",
    "category_chapter",
    "category_knowledge",
    "related_curriculums",
    "tags",
    "grad_status",
    "recognize_status",
    "include_in_handout",
    "answer_image",
    "answer_source",
    "answer_reviewed",
    "error_reason",
    "image_figure",
    "mastery_status",
    "question_id",
    "snapshot_source",
)


def _mistake_session():
    """后台任务用的独立会话。

    每次调用都从 ``mathbank.database`` 现取 ``SessionLocal``（而不是模块级绑定），
    这样测试里 conftest 对 ``database.SessionLocal`` 的替换才能生效。
    """

    from mathbank.database import SessionLocal

    return SessionLocal()


def _mistake_batch_dir(batch_id: int) -> Path:
    """批次的工作目录（源文件 / 页图 / 题块图 / 导出产物都放这里）。

    按批次隔离的好处：删批次时整目录递归删掉即可，不会误伤别的批次；也不依赖
    ``clean_orphaned_images``（它只扫上传根目录与 ``tmp/``，不会进子目录）。
    """

    return Path(UPLOAD_DIR) / MISTAKE_UPLOAD_SUBDIR / str(int(batch_id))


def _mistake_pages_dir(batch_id: int) -> Path:
    return _mistake_batch_dir(batch_id) / "pages"


def _mistake_blocks_dir(batch_id: int) -> Path:
    return _mistake_batch_dir(batch_id) / "blocks"


def _mistake_exports_dir(batch_id: int) -> Path:
    return _mistake_batch_dir(batch_id) / "exports"


def _mistake_analysis_file(batch_id: int) -> Path:
    return _mistake_batch_dir(batch_id) / "analysis.json"


def _mistake_web_url(batch_id: int, *parts: str) -> str:
    segments = [UPLOAD_DIR_REL, MISTAKE_UPLOAD_SUBDIR, str(int(batch_id))]
    segments.extend(str(part) for part in parts if str(part))
    return "/" + "/".join(segments)


def _mistake_block_web_url(batch_id: int, crop_name: str) -> str:
    """块图的**裸** URL（不带任何查询串）—— 入库时存的就是它。"""

    return _mistake_web_url(batch_id, "blocks", crop_name)


def _page_web_url(batch_id: int, page_name: str) -> str:
    """页图 URL，下发时挂上这一页自己的更新时间指纹。

    页图文件名只由页号决定（``page_1.png``），重切 / 重新渲染是**同名覆盖**：
    不带指纹时浏览器会在同一个文档里复用已经解码好的旧位图 —— 打开框选弹窗看到的
    是上一版的页面，而磁盘上早已是新图。与块图同一套规则：库里存裸路径、出口才挂
    （原因与踩坑记录见 ``_version_stored_block_url``）。
    """

    bare = _mistake_web_url(batch_id, "pages", page_name)
    try:
        stamp = (_mistake_pages_dir(batch_id) / page_name).stat().st_mtime_ns
    except OSError:
        # 图不在盘上就退回裸 URL，宁可让浏览器复用也别给一个必然 404 的地址。
        return bare
    return f"{bare}?v={stamp}"


def _version_stored_block_url(batch_id: int, web_path: str) -> str:
    """把库里的裸块图 URL 挂上这张图自己的更新时间指纹（幂等，可重复调用）。

    块图的文件名只由「页号 + 块号」决定（``p002_b01.png``），重切或应用框选时是
    **同名覆盖**。只按路径当 URL 会踩一个很安静的坑：内容换了、URL 没换，浏览器就
    在同一个文档里复用已经解码好的旧位图 —— 卡片上显示的是上一版的内容，而数据库、
    块图文件、接口返回全是新的（排查全过程见 .workbuddy/memory/2026-09-14.md）。
    所以这层指纹是必须的。

    ⚠️ 但它**绝不能进数据库**。资产安全层（``asset_security._reference_parts``）
    明确拒绝任何含 ``?`` 的引用，而识别取图、导出插图都是拿 ``image_block`` 当磁盘
    路径解析的。9-14 把带 ``?v=`` 的 URL 直接写进 ``image_block`` 列，代价是识别
    100% 失败（整批标 ``failed``、题面全空），见 .workbuddy/memory/2026-09-15.md
    第六轮。

    浏览器要指纹、磁盘要路径 —— 两者在出口分岔：库里存裸路径，下发时才挂。
    """

    raw = str(web_path or "").strip()
    if not raw:
        return ""
    bare = raw.split("?", 1)[0].split("#", 1)[0]
    crop_name = bare.rsplit("/", 1)[-1]
    if not crop_name:
        return bare
    try:
        stamp = (_mistake_blocks_dir(batch_id) / crop_name).stat().st_mtime_ns
    except OSError:
        # 图还没落盘（不该发生）：退回裸 URL，宁可让浏览器复用也别给出一个 404。
        return bare
    return f"{bare}?v={stamp}"


def _mistake_record_client_payload(record, batch_id: int) -> dict:
    """对外下发一条错题记录：把块图字段的指纹在这里补齐。

    前端拿 ``image_block`` 当 ``<img src>``（题卡缩略图 / 块列表 / 点开大图），
    出口这一层必须挂上指纹，否则 9-14 修的「同 URL 复用旧位图」立刻复发。
    ``block_images`` 是合并组的成员图列表，同样处理。
    """

    item = record.to_dict()
    item["image_block"] = _version_stored_block_url(batch_id, item.get("image_block"))
    images = item.get("block_images")
    if isinstance(images, list):
        item["block_images"] = [
            _version_stored_block_url(batch_id, url) for url in images
        ]
    item["block_url"] = item["image_block"]
    return item


def _mistake_source_path(batch_id: int) -> Optional[Path]:
    """返回批次源文件（保留原始扩展名，供 PyMuPDF / Pillow 分流）。"""

    directory = _mistake_batch_dir(batch_id)
    try:
        for candidate in sorted(directory.glob("source.*")):
            if candidate.is_file():
                return candidate
    except OSError:
        return None
    return None


def _reset_directory(directory: Path) -> None:
    """清空目录内文件（不递归删子目录），用于重切前清掉旧题块图。"""

    directory.mkdir(parents=True, exist_ok=True)
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_file():
            try:
                entry.unlink()
            except OSError:
                pass


def ensure_mistake_student(db: Session, *, name: str = "", grade: str = "") -> Student:
    """取（或建）错题工作台使用的学生记录。

    一期界面不做多学生切换（实施计划 §5.1），但表结构按多学生设计：这里只保证
    「至少有一条记录」。若用户在新建批次时填了姓名，就更新这条记录的名字 —— 免得
    默认名把人锁死，也免得为此单开一个接口。
    """

    student = db.query(Student).order_by(Student.id.asc()).first()
    desired_name = str(name or "").strip()
    desired_grade = str(grade or "").strip()
    if student is None:
        student = Student(
            name=desired_name or DEFAULT_MISTAKE_STUDENT_NAME,
            grade=desired_grade or DEFAULT_MISTAKE_STUDENT_GRADE,
        )
        db.add(student)
        db.flush()
        return student
    changed = False
    if desired_name and desired_name != (student.name or ""):
        student.name = desired_name
        changed = True
    if desired_grade and desired_grade != (student.grade or ""):
        student.grade = desired_grade
        changed = True
    if changed:
        db.flush()
    return student


def _mistake_field(record, name, default=None):
    """从 ORM 对象或 ``to_dict()`` 结果里取值（统计函数两种输入都要吃）。"""

    if isinstance(record, dict):
        value = record.get(name, default)
    else:
        value = getattr(record, name, default)
    return default if value is None else value


def _mistake_record_stats(records) -> dict:
    """对错分布 + 流程进度统计（错题本页头与批次列表共用）。"""

    stats = {
        "total": 0,
        "correct": 0,
        "incorrect": 0,
        "unknown": 0,
        "recognized": 0,
        "failed": 0,
        "included": 0,
        "imported": 0,
    }
    for record in records or []:
        stats["total"] += 1
        grad = str(_mistake_field(record, "grad_status", "unknown"))
        stats[grad if grad in GRAD_STATUS_VALUES else "unknown"] += 1
        recognize_status = str(_mistake_field(record, "recognize_status", ""))
        if recognize_status == "done":
            stats["recognized"] += 1
        elif recognize_status == "failed":
            stats["failed"] += 1
        if _mistake_field(record, "include_in_handout", False):
            stats["included"] += 1
        if _mistake_field(record, "question_id", None):
            stats["imported"] += 1
    return stats


def _mistake_record_image_path(record) -> tuple[Optional[Path], str]:
    """题块图的磁盘路径，外加失败原因（``path is None`` 时第二项才是有效文案）。

    两件事必须分开报：「路径被安全层拒绝」与「文件不在磁盘上」是两种完全不同的
    故障 —— 前者是数据里混进了不该有的字符，后者是图真的丢了。合并成一句「题块
    图缺失」时，用户在界面和日志里都无从判断该修哪一边。
    """

    raw = str(_mistake_field(record, "image_block", "") or "").strip()
    if not raw:
        return None, "记录里没有题块图字段"
    # 兼容历史数据：v2.3.x 早期有一版把带 ?v=<mtime_ns> 的 URL 写进了库里
    # （见 .workbuddy/memory/2026-09-15.md 第六轮），剥掉查询串再解析，
    # 否则资产安全层会以「包含不允许的字符」直接拒绝。
    reference = raw.split("?", 1)[0].split("#", 1)[0]
    try:
        path = resolve_upload_asset(
            reference,
            uploads_dir=UPLOAD_DIR,
            url_prefix=UPLOAD_DIR_REL,
            # 故意不让安全层替我们报「文件不存在」：require_file=True 会把这种
            # 情况也伪装成安全检查失败，两类故障就分不开了。下面单独判一次。
            require_file=False,
        )
    except AssetSecurityError as exc:
        return None, f"题块图路径被安全校验拒绝（{exc}）：{raw}"
    if not path.is_file():
        return None, f"题块图不存在：{reference}"
    return path, ""


def _write_mistake_analysis(
    batch_id: int, analyses: list, cross_page_merges: Optional[list] = None
) -> None:
    """把切块元数据落盘（页尺寸、题块位置、候选切点、吸附点）。

    为什么不进数据库：这些是「切块过程的中间产物」，只在点选/微调界面上用，不参与
    错题本导出与统计；单开一张表或往 records 里塞 JSON 都不划算。放在批次目录里
    与页图同生命周期，删批次一起清掉。

    ``cross_page_merges`` 给了就写这一份，没给就把文件里原有的原样带过来。这个函数
    整份重写文件，而跨页组挂在顶层、不属于任何一页 —— 不显式取回，一次「重切」就
    把用户合好的跨页题悄悄拆回两道，还查不出是谁干的。
    """

    if cross_page_merges is None:
        try:
            cross_page_merges = _normalize_cross_page_merges(
                _read_mistake_analysis(batch_id).get("cross_page_merges")
            )
        except MistakeAnalysisError:
            cross_page_merges = []
    payload = {
        # v6：顶层增加 cross_page_merges（跨页合并组：一道题被页边界切成两半）
        # v5：页增加 hidden_blocks（被用户删掉的题块，按几何矩形锚定，重切后依然隐藏）
        # v4：页增加 manual_merges（人工合并组：一道题被切成多块时并为一条）
        # v3：页增加 manual_boxes（人工框选矩形，叠在 blocks 之上的遮蔽层）
        # v2：块增加横向范围与栏号，页增加 layout / 按栏吸附池
        "version": 6,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "cross_page_merges": cross_page_merges,
        "pages": [analysis.to_dict() for analysis in analyses],
    }
    write_private_text_atomic(
        _mistake_analysis_file(batch_id),
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


class MistakeAnalysisError(RuntimeError):
    """切块元数据（``analysis.json``）**存在但读不出来** —— 损坏、结构异常或不可读。

    为什么单独一个异常：这类情况和「还没有元数据」必须分开。旧实现把两者都归成
    一个空壳返回，而 :func:`_update_mistake_page_meta` 是「读回整份 → 改点名键 →
    整份写回」的写法 —— 空壳一写回去，整批人工框选、人工合并、删除名单就被抹平，
    文件本身却还在，用户看到的只是「我画的框全没了」。
    现在损坏一律抛本异常，写路径宁可失败也不覆盖。
    """


def _empty_mistake_analysis() -> dict:
    """「还没有切块元数据」的空壳。

    跨页合并组挂在文件**顶层**（成员带页号，不属于任何单页），空壳也得带上这个键
    —— 漏掉它，第一次「读回 → 改键 → 整份写回」就会把跨页题从文件里抹掉。
    """

    return {"version": 6, "cross_page_merges": [], "pages": []}


def _read_mistake_analysis(batch_id: int) -> dict:
    """读回切块元数据。文件不存在＝正常空壳；文件在但读不出来＝抛异常。

    三种状态要分清：

    - **文件不存在**：还没切过块。这是合法状态，返回空壳。
    - **文件为空**：写到一半被打断（原子写不会留半截，真出现就是异常路径）。空文件
      里没有任何数据可丢，按「还没有」处理比报错更有用。
    - **文件在但解析不出来 / 结构不对**：抛 :class:`MistakeAnalysisError`。调用方
      绝不能拿空壳去覆盖它。

    旧文件（v1–v5）缺顶层 ``cross_page_merges``（v6 新增），v1–v4 还缺
    ``manual_boxes`` / ``manual_merges`` / ``hidden_blocks``，
    就地补成空列表，省得每个调用方都写一次 ``.get("manual_boxes") or []`` 而漏掉
    一处。更早的版本还缺横向范围与按栏吸附池，那些由各自的读取方兜底（它们本来
    就有默认值）。
    """

    path = _mistake_analysis_file(batch_id)
    try:
        # 不用先 is_file() 再读：那中间有个窗口，且要判两次。直接读、按异常分流。
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_mistake_analysis()
    except OSError as exc:
        raise MistakeAnalysisError(
            f"切块元数据无法读取（{type(exc).__name__}: {exc}）。"
            f"为避免覆盖原始文件，本次操作已中止。文件：{path}"
        ) from exc

    if not raw.strip():
        return _empty_mistake_analysis()

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise MistakeAnalysisError(
            f"切块元数据已损坏，JSON 解析失败（{exc}）。"
            f"为避免覆盖原始文件，本次操作已中止。文件：{path}"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise MistakeAnalysisError(
            "切块元数据结构异常（缺少 pages 列表）。"
            f"为避免覆盖原始文件，本次操作已中止。文件：{path}"
        )

    for page in data["pages"]:
        if not isinstance(page, dict):
            continue
        if not isinstance(page.get("manual_boxes"), list):
            page["manual_boxes"] = []
        if not isinstance(page.get("manual_merges"), list):
            page["manual_merges"] = []
        if not isinstance(page.get("hidden_blocks"), list):
            page["hidden_blocks"] = []
    if not isinstance(data.get("cross_page_merges"), list):
        data["cross_page_merges"] = []
    return data


def _mark_mistake_batch_failed(batch_id: int, message: str) -> None:
    """失败落状态用独立会话，避免复用已被 rollback 的那个。"""

    session = _mistake_session()
    try:
        batch = session.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
        if batch is not None:
            batch.status = "cut_failed"
            batch.note = str(message or "")[:500]
            session.commit()
    except Exception as exc:  # noqa: BLE001 - 状态落盘失败不该覆盖原始错误
        print(f"[Mistake Batch Status Error] {type(exc).__name__}: {exc}")
    finally:
        session.close()


# ---- 多模态识别 ----


def _mistake_multimodal_text(
    provider: MultimodalProviderConfig, image_path: str, prompt: str
) -> str:
    """一次「图 + 自定义提示词 → 文本」调用。

    与 ``ocr_via_provider`` 的唯一差别是提示词来源：那个写死 ``COMMON_OCR_PROMPT``
    （通用公式识别），错题块要的是「只出题干与选项」的专用提示词。其余（思考预算注入、
    百炼思考策略、超时、响应解析）保持完全一致，避免长出第二套行为。
    """

    import base64

    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"读取并对题块图进行 Base64 编码失败: {str(e)}")

    payload = {
        "model": provider.model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded_string}"
                        },
                    },
                ],
            }
        ],
        "stream": False,
    }
    payload = inject_reasoning_effort(payload, provider.reasoning_effort)
    payload = apply_bailian_thinking_policy(
        payload,
        provider_code=provider.provider_code,
        model_name=provider.model_name,
        task="ocr",
    )

    # Chat-completion POST 不是幂等的：读超时可能发生在服务端已受理（并计费）之后。
    # 与 ocr_via_provider 一样不做自动重发，把「是否重试」交回给用户。
    response = post_chat_completion(provider, payload, timeout=240, check_status=False)
    if response.status_code != 200:
        raise RuntimeError(
            f"{provider.provider_label} API 识别失败: HTTP {response.status_code}"
        )
    choices = (response.json() or {}).get("choices") or []
    if not choices:
        raise RuntimeError(
            f"{provider.provider_label} 返回的数据中未包含 Choices 结果。"
        )
    return str(choices[0].get("message", {}).get("content", "")).strip()


def recognize_mistake_block(
    image_path: str, subject: str, engine: str = ""
) -> tuple[dict, str]:
    """识别单个题块，返回 ``(payload, provider_label)``。

    引擎顺序沿用项目既有的多模态配置（``resolve_ocr_fallbacks``：默认免费优先
    硅基流动 → 阿里百炼 → 中转站 GPT），故障转移语义与拆卷一致 —— 模型报错就换下一家，
    但**读超时不换**（避免同一张图被重复计费）。``engine`` 供界面上「换模型重试」使用，
    留空即走默认顺序。
    """

    prefer_engine = str(engine or "").strip() or os.getenv(
        "OCR_PREFER_ENGINE", "siliconflow"
    )
    providers = resolve_ocr_fallbacks(prefer_engine)
    if not providers:
        raise RuntimeError(
            "未配置任何识图 Key，请在右上角「API设置」面板中配置识图引擎的 API 密钥。"
        )

    # 照学科传树：物理挂教科版、化学挂人教版、数学用「设置 - 大纲」里的活动大纲。
    # 传树之后模型才会逐字给出学段/章节/小节，_apply_mistake_category 再做受控归一。
    prompt = build_mistake_block_system_prompt(
        subject, get_subject_curriculum_tree(subject)
    )
    errors: list[str] = []
    for provider in providers:
        label = f"{provider.provider_label} ({provider.model_name})"
        try:
            raw = _mistake_multimodal_text(provider, image_path, prompt)
            payload = parse_ai_json(raw)
            if not isinstance(payload, dict):
                raise ValueError("模型没有返回 JSON 对象。")
            return payload, label
        except requests.exceptions.ReadTimeout as exc:
            raise RuntimeError(
                f"{label} 读取超时，请求是否已被处理尚不确定。"
                "为避免重复计费，本次未自动切换到下一家模型，请稍后手动重试。"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 换下一家引擎继续
            message = f"{label} 出错: {str(exc)}"
            print(f"[Mistake Recognize Warning] {message}")
            errors.append(message)
    raise RuntimeError("所有已配置的识图引擎均尝试失败。\n" + "\n".join(errors))


def _mistake_payload_text(payload: dict, key: str) -> str:
    """模型返回字段的取值归一：列表按「，」拼、其余转字符串去空白。"""

    value = (payload or {}).get(key)
    if isinstance(value, (list, tuple)):
        return "，".join(
            str(item).strip() for item in value if str(item).strip()
        )
    return str(value or "").strip()


def _apply_mistake_payload(
    record: MistakeRecord, payload: dict, curriculum: dict | None = None
) -> None:
    """把模型返回的字段写进记录，并做与手动录入一致的归一。

    归一这一步不能省：题面里的 ``\fillin`` 与 ``choices`` 环境是题库侧的统一写法，
    哪天要「单题识别 + 补入库」时，两边写法不一致会让查重指纹与渲染都对不上。

    ``curriculum`` 是该学科的教材树（``get_subject_curriculum_tree``）。传了才会把
    模型给的学段/章节/小节做受控归一后写进 ``category_*``，不传就只写题面那一组字段
    （单测里只关心题面时用得上）。
    """

    def _text(key: str) -> str:
        return _mistake_payload_text(payload, key)

    question_no = _text("question_no")
    if question_no:
        record.question_no = question_no[:50]

    content = _text("content")
    if content:
        content = normalize_fillin_macro(content)
        content = normalize_choice_options_to_latex(content)
        # 图由人工截图补入：模型擅自「画」出来的图形（TikZ / includegraphics / 排版壳子）
        # 一律换算成占位符芯片（零 token 兜底，错题流程不做 TikZ 编译）。
        content = replace_drawn_figures_with_placeholders(content)
        record.content = _strip_leading_question_number(content)

    question_type = _text("question_type")
    if question_type in MISTAKE_QUESTION_TYPES:
        record.question_type = question_type

    record.difficulty = normalize_difficulty(
        _text("difficulty") or None, default=record.difficulty or "normal"
    )

    knowledge_tags = _text("knowledge_tags")
    if knowledge_tags:
        record.knowledge_tags = normalize_tag_list(
            knowledge_tags, field="knowledge_list"
        )
    solve_method = _text("solve_method")
    if solve_method:
        record.solve_method = normalize_tag_list(solve_method, field="solve_method")

    _apply_mistake_category(record, payload, curriculum)


def _apply_mistake_category(
    record: MistakeRecord, payload: dict, curriculum: dict | None
) -> None:
    """把模型给出的教材定位写进记录的 ``category_*``，并做受控归一。

    两个纪律：

    ① **只填空值，且学段冲突时整组跳过**。用户已经在审校页手选过的字段一律不动 ——
       「重新识别这道」的语义是重做题面，不是把人工分类抹掉；而他手选的学段与模型
       判断不一致时，章节/小节也不填（否则会凑出「必修二 + 必修一的某章」这种
       跨书组合，下拉里冒出一个该书根本没有的章节）。
    ② **归一走后端既有那套**。模型给的小节名先当候选塞进 ``knowledge_list``，
       交给 ``normalize_category_fields``（别名表 → 互含唯一 → 知识点反推唯一胜出）
       去受控匹配；匹配不上就退回知识点标签反推，仍不行就留空交人工补 ——
       绝不猜一个模糊值入库，否则它会在下拉里冒出一个教材树上根本没有的选项。

    历史背景：这里以前**根本没有分类写入**（提示词也明文禁止模型判学段），前提是
    「物化没有教材树、物化题一期不进题库」；这两条现在都已作废（教材树随三科错题库
    一起落地、物化照样入库），于是每道物化错题入库后都掉进「未分类」桶，题库按章节
    筛根本找不到 —— 这个函数就是补上那一段。
    """

    if not isinstance(curriculum, dict) or not curriculum:
        return
    raw_comp = _mistake_payload_text(payload, "compulsory")
    raw_chap = _mistake_payload_text(payload, "chapter")
    raw_know = _mistake_payload_text(payload, "category_knowledge")
    if not (raw_comp or raw_chap or raw_know):
        return

    tags = payload.get("knowledge_tags")
    if isinstance(tags, (list, tuple)):
        tag_list = [str(item).strip() for item in tags if str(item).strip()]
    else:
        tag_list = [str(tags).strip()] if str(tags or "").strip() else []

    candidate = {
        "compulsory": raw_comp,
        "chapter": raw_chap,
        # 模型给的小节名排最前：它是最精确的候选（逐字取自教材范围），命中即落；
        # 落空时 normalize_category_fields 会继续拿自由标签去反推。
        "knowledge_list": ([raw_know] if raw_know else []) + tag_list,
    }
    normalize_category_fields([candidate], curriculum)

    matched = {
        field: str(candidate.get(field) or "").strip()
        for field in ("category_compulsory", "category_chapter", "category_knowledge")
    }
    if not any(matched.values()):
        return
    # 人工选的学段与模型判断不一致时，连章节/小节一起跳过。只捂学段、照填章节的话，
    # 会凑出「必修二 + 必修一的某章」这种跨书组合 —— 下拉里会冒出一个该书根本没有的
    # 章节（reviewFillSelect 会把匹配不上的历史值当额外选项留着）。
    chosen = str(record.category_compulsory or "").strip()
    if chosen and matched["category_compulsory"] and chosen != matched["category_compulsory"]:
        return
    for field, value in matched.items():
        if value and not str(getattr(record, field, "") or "").strip():
            setattr(record, field, value)


def _match_mistake_records(previous: list, blocks: list) -> dict:
    """把旧题块记录按矩形重叠贪心匹配到新切出的题块上。

    人工微调切线后重切，必须尽最大努力保住用户已经做过的点选、错因、识别结果与入库
    状态 —— 一次重切把 40 道题的点选全部清空是不可接受的。匹配标准是纵向重叠高度
    （重叠不足较矮一方高度的 30 % 视为不等价），一对一贪心；宁可不匹配也不乱配。

    双栏页面上左右两栏的块纵向范围天然重叠，只看纵向会把左栏的作答状态接到右栏的
    块上，因此横向也要求重叠（旧记录没有横向字段时按「整页宽」处理，等价于不限制）。

    ⚠️ 返回的只是「状态来源」。调用方必须在本页新记录建好之后**把所有旧记录删掉**：
    状态已经被复制到新记录上，旧记录留着就是同一道题在库里存在两份（首版实现漏了这
    一步，实测重切后记录数直接翻倍）。

    返回 ``{block_index: 旧记录}``。
    """

    pairs: list[tuple[float, int, int]] = []
    for prev_index, record in enumerate(previous or []):
        prev_start = float(_mistake_field(record, "block_y_start", 0.0))
        prev_end = float(_mistake_field(record, "block_y_end", 1.0))
        prev_x_start = float(_mistake_field(record, "block_x_start", 0.0))
        prev_x_end = float(_mistake_field(record, "block_x_end", 1.0))
        for block in blocks or []:
            overlap = min(prev_end, block.y_end) - max(prev_start, block.y_start)
            if overlap <= 0:
                continue
            reference = min(prev_end - prev_start, block.height)
            if reference <= 0 or overlap < reference * 0.3:
                continue
            x_reference = min(prev_x_end - prev_x_start, block.width)
            x_overlap = min(prev_x_end, block.x_end) - max(prev_x_start, block.x_start)
            if x_reference <= 0 or x_overlap < x_reference * 0.3:
                continue
            pairs.append((overlap, prev_index, block.block_index))

    pairs.sort(key=lambda item: item[0], reverse=True)
    used_previous: set[int] = set()
    used_blocks: set[int] = set()
    matched: dict[int, object] = {}
    for _overlap, prev_index, block_index in pairs:
        if prev_index in used_previous or block_index in used_blocks:
            continue
        used_previous.add(prev_index)
        used_blocks.add(block_index)
        matched[block_index] = previous[prev_index]
    return matched


# ---- 异步任务：切题 ----


def _normalize_mistake_page_layouts(raw) -> dict:
    """解析前端传来的「本页栏目」。

    接受两种写法：``{"1": "double"}``（只表态，分栏线由自动检测补）或
    ``{"1": {"mode": "double", "boundary": 0.49}}``（人工拖过分栏线）。
    非法页号/模式直接忽略，不报错 —— 多页批量提交时不该因为一页参数脏而整批失败。
    """

    layouts: dict = {}
    if not isinstance(raw, dict):
        return layouts
    for key, value in raw.items():
        try:
            page_no = int(key)
        except (TypeError, ValueError):
            continue
        if page_no <= 0:
            continue
        if isinstance(value, str):
            mode, boundary = value.strip().lower(), None
        elif isinstance(value, dict):
            mode = str(value.get("mode") or "").strip().lower()
            boundary = value.get("boundary")
        else:
            continue
        if mode not in (COLUMN_SINGLE, COLUMN_DOUBLE):
            continue
        layouts[page_no] = manual_column_layout(mode, boundary)
    return layouts


def _normalize_mistake_boxes(raw) -> list[list[float]]:
    """解析前端/落盘的人工框列表，返回可落盘的 ``[[x0, y0, x1, y1], …]``。

    这里只判「结构成不成立」（正好四个数字、都落在 0–1），尺寸与退化交给算法
    层的 :func:`clean_manual_boxes` —— 那是规则的唯一真源。两处各判一套的话，
    迟早会出现「接口收了、写进元数据、前端照着画出来了，算法却不肯把它切成
    题块」这种看得见摸不着的坏状态。
    """

    if not isinstance(raw, (list, tuple)):
        return []
    candidates: list[list[float]] = []
    for box in raw:
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            values = [float(value) for value in box]
        except (TypeError, ValueError):
            continue
        if not all(0.0 <= value <= 1.0 for value in values):
            continue
        candidates.append(values)
    return [
        [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)]
        for x0, y0, x1, y1 in clean_manual_boxes(candidates)
    ]


def _mistake_page_entry(batch_id: int, page_no: int) -> dict | None:
    """从 analysis.json 里取出某页的切块元数据（找不到返回 None）。"""

    for item in _read_mistake_analysis(batch_id).get("pages") or []:
        if not isinstance(item, dict):
            continue
        try:
            if int(item.get("page_no") or 0) == page_no:
                return item
        except (TypeError, ValueError):
            continue
    return None


def _update_mistake_page_meta(batch_id: int, page_no: int, updates: dict) -> None:
    """就地改 analysis.json 某页的若干个键（其余一个不动）。

    刻意不复用 :func:`_write_mistake_analysis`：那个函数吃的是 ``PageAnalysis``
    对象，为改一个字段把整页「还原成对象、再序列化回去」，等于给无损往返埋一个
    隐患 —— 哪天 ``to_dict()`` 漏掉某个字段，这次保存就会把整批页的切块元数据
    悄悄削掉一层。这里只动 ``updates`` 里点名的键。
    """

    data = _read_mistake_analysis(batch_id)
    pages = [item for item in (data.get("pages") or []) if isinstance(item, dict)]
    for item in pages:
        try:
            matched = int(item.get("page_no") or 0) == page_no
        except (TypeError, ValueError):
            matched = False
        if matched:
            item.update(updates)
    data["pages"] = pages
    data["version"] = 6
    data["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_private_text_atomic(
        _mistake_analysis_file(batch_id),
        json.dumps(data, ensure_ascii=False, indent=2),
    )


def _update_mistake_manual_boxes(batch_id: int, page_no: int, boxes: list) -> None:
    """把某页的人工框写回 analysis.json。"""

    _update_mistake_page_meta(batch_id, page_no, {"manual_boxes": boxes})


def _update_mistake_manual_merges(batch_id: int, page_no: int, merges: list) -> None:
    """把某页的人工合并组写回 analysis.json（拆分/改方向/调顺序都落在这里）。"""

    _update_mistake_page_meta(batch_id, page_no, {"manual_merges": merges})


def _update_mistake_hidden_blocks(batch_id: int, page_no: int, rects: list) -> None:
    """把某页「被删除的题块」名单写回 analysis.json（恢复＝写空列表）。"""

    _update_mistake_page_meta(batch_id, page_no, {"hidden_blocks": rects})


def _update_mistake_cross_page_merges(batch_id: int, merges: list) -> None:
    """把批次级「跨页合并组」写回 analysis.json 顶层。

    与页面级的几个写入口一样只动自己那一个键。跨页组的成员带页号，任一页的框选 /
    重切都不该碰到它 —— 只有本函数与 :func:`_write_mistake_analysis` 会改。
    """

    data = _read_mistake_analysis(batch_id)
    data["cross_page_merges"] = merges
    data["version"] = 6
    data["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_private_text_atomic(
        _mistake_analysis_file(batch_id),
        json.dumps(data, ensure_ascii=False, indent=2),
    )


def _sync_mistake_hidden_meta(batch_id: int, page_no: int, result: dict) -> None:
    """框选/合并重建之后同步「已删题块」名单，只在真的变了时才写盘。

    重建会把没命中任何项的矩形丢掉（几何变了），不同步的话它们会一直躺在文件里，
    等哪天几何又撞上就凭空隐藏一道题。
    """

    matched = result.get("hidden_blocks")
    if not isinstance(matched, list):
        return
    try:
        stored = _normalize_mistake_hidden(
            (_mistake_page_entry(batch_id, page_no) or {}).get("hidden_blocks")
        )
        same = len(stored) == len(matched) and all(
            all(abs(left - right) < 1e-9 for left, right in zip(one, two))
            for one, two in zip(stored, matched)
        )
        if not same:
            _update_mistake_hidden_blocks(batch_id, page_no, matched)
    except (OSError, MistakeAnalysisError) as exc:
        print(f"[Mistake Page Hidden Meta Error] {type(exc).__name__}: {exc}")


def _blocks_from_meta(entry: dict, page_no: int) -> list:
    """把元数据里的 blocks（自动基线）还原成 ``BlockRegion`` 列表。"""

    blocks = []
    for item in entry.get("blocks") or []:
        if not isinstance(item, dict):
            continue
        try:
            blocks.append(
                BlockRegion(
                    page_no=page_no,
                    block_index=int(item.get("block_index") or 0),
                    y_start=float(item.get("y_start") or 0.0),
                    y_end=float(item.get("y_end") or 0.0),
                    x_start=float(item.get("x_start") or 0.0),
                    x_end=float(item.get("x_end") or 1.0),
                    column_index=int(item.get("column_index") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return blocks


def _page_column_boundary(entry: dict) -> float:
    """取该页的分栏线（缺失或非法时给 1.0，语义是「整页一栏」）。"""

    layout = entry.get("layout")
    raw = layout.get("boundary") if isinstance(layout, dict) else entry.get("column_boundary")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def _crop_mistake_page_blocks(batch_id: int, page_no: int, page_path, blocks: list) -> None:
    """重裁本页块图（先落临时名，全部成功后再统一改名）。

    直接往最终文件名上写会有个难查的中间态：裁到第 3 个块时失败，前两个块的图
    已经被换掉、记录却还是旧的 —— 页面上的块图与记录对不上，而用户看到的只是
    一个报错。先落临时名，全部成功再换名，失败时旧图原封不动。
    """

    blocks_dir = _mistake_blocks_dir(batch_id)
    blocks_dir.mkdir(parents=True, exist_ok=True)
    staging: list[tuple[Path, Path]] = []
    try:
        for block in blocks:
            target = blocks_dir / f"p{page_no:03d}_b{block.block_index:02d}.png"
            temp = blocks_dir / f".staging_{target.name}"
            crop_region(
                page_path,
                temp,
                y_start=block.y_start,
                y_end=block.y_end,
                x_start=block.x_start,
                x_end=block.x_end,
            )
            staging.append((temp, target))
    except Exception:
        for temp, _target in staging:
            try:
                temp.unlink()
            except OSError:
                pass
        raise
    for temp, target in staging:
        os.replace(temp, target)
    # 序号变少时清掉旧图，避免「记录数 < 块图数」的孤儿图一直堆在目录里
    keep = {target.name for _temp, target in staging}
    for path in blocks_dir.glob(f"p{page_no:03d}_b*.png"):
        if path.name not in keep:
            try:
                path.unlink()
            except OSError:
                pass


def _ensure_cross_page_block_images(
    batch_id: int, page_no: int, groups: list, cache: dict
) -> None:
    """把跨页组里**属于别页**的成员块图按当前几何裁好。

    单页重建只裁本页的块，而跨页拼图要读对页的 ``p{页}_b{块}.png``。不先裁，拼出来
    的可能是上一版几何的图，或者压根读不到文件 —— 后者的异常会被兜底成「保留成员
    图」，用户看到的是「合并了但图没拼上」，控制台只有一行 print。
    """

    others = sorted(
        {
            member["page_no"]
            for group in groups or []
            for member in group.get("members") or []
            if member["page_no"] != page_no
        }
    )
    for other in others:
        blocks = _mistake_page_blocks(batch_id, other, cache)
        if not blocks:
            continue
        page_path = _mistake_pages_dir(batch_id) / f"page_{other}.png"
        if not page_path.is_file():
            continue
        _crop_mistake_page_blocks(batch_id, other, page_path, blocks)



# ---- 人工合并（一道题被切成多块时并为一条记录） ----
#
# 与「人工框」的区别：框是**遮蔽替换**（框内换成框的矩形），合并是**拼接保留**
# （多块按方向拼成一张图，各块原图仍在）。因此合并组存「成员矩形的几何」而不是
# 块序号 —— 重切会让块序号整体位移，但几何位置基本不动，按重叠仍能把成员找回。

#: 成员矩形与题块的交叠面积占比低于此值就认为「这块已经不是原来那块了」
MISTAKE_MERGE_MIN_OVERLAP = 0.3
#: 小于此尺寸的矩形视为误点（约等于 A4 页上 2 mm）
MISTAKE_MERGE_MIN_RECT = 0.004
#: 相邻成员纵向重叠占比达到此值即判定为「左右分栏的两半」→ 横向拼
MISTAKE_MERGE_SIDE_BY_SIDE = 0.3
_MERGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
#: 「删除题块」的命中门槛：交叠面积占**两者中较小者**的比例。
#:
#: 与合并的 0.3 不同，这里必须更严：合并配错是把别人的半截拼进来，肉眼可见且能拆；
#: 而删除配错会让一道**用户没动过的题**直接消失，且不看这条记录根本发现不了。用
#: 「占较小者」而不是「占名单里那个矩形」也是同一个原因 —— 重切把一个块拆成两半后，
#: 两半各自完全落在名单矩形里，各算 1.0，删除照样生效；反过来一个只与名单矩形擦边
#: 的大块，比值很低，不会被误删。
MISTAKE_HIDE_MIN_OVERLAP = 0.6
#: 单页忽略名单的长度上限。用户不会删到这么多，超了只可能是脏数据在滚雪球。
MISTAKE_HIDE_MAX_ENTRIES = 200


def _new_mistake_merge_id() -> str:
    return "m" + uuid.uuid4().hex[:8]


def _normalize_mistake_rect(raw) -> Optional[list]:
    """把一个 ``[x0, y0, x1, y1]`` 归一化到 0–1 且左下/右上顺序正确。"""

    try:
        x0, y0, x1, y1 = (float(value) for value in (raw or [])[:4])
    except (TypeError, ValueError):
        return None
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(1.0, x1), min(1.0, y1)
    if (x1 - x0) < MISTAKE_MERGE_MIN_RECT or (y1 - y0) < MISTAKE_MERGE_MIN_RECT:
        return None
    return [x0, y0, x1, y1]


def _normalize_mistake_merges(raw) -> list:
    """规范化人工合并组，丢掉不成组的（不足 2 个有效矩形）与非法 id。

    ``id`` 由后端生成（``m<8 位十六进制>``）并被前端原样回传，用于「拆分 / 改
    方向 / 调顺序」时定位到同一个组；用户手改坏了或撞号就换新 id，不会让两个组
    共用同一个身份。
    """

    groups: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        rects = []
        for rect in item.get("rects") or []:
            fixed = _normalize_mistake_rect(rect)
            if fixed is not None:
                rects.append(fixed)
        if len(rects) < 2:
            continue
        direction = str(item.get("direction") or "").strip().lower()
        if direction not in ("v", "h"):
            direction = ""
        try:
            primary = int(item.get("primary") or 0)
        except (TypeError, ValueError):
            primary = 0
        primary = max(0, min(primary, len(rects) - 1))
        merge_id = str(item.get("id") or "").strip()
        if not _MERGE_ID_PATTERN.match(merge_id):
            merge_id = _new_mistake_merge_id()
        groups.append(
            {
                "id": merge_id,
                "rects": rects,
                "direction": direction,
                "primary": primary,
            }
        )
    seen: set[str] = set()
    for group in groups:
        while group["id"] in seen:
            group["id"] = _new_mistake_merge_id()
        seen.add(group["id"])
    return groups


def _guess_merge_direction(rects: list) -> str:
    """猜拼接方向：成员两两纵向压着 → 左右拼（h）；纵向错开 → 上下拼（v）。

    双栏卷上一道题常被切成「左栏一半 + 右栏一半」，两块纵坐标大量重叠；而跨页
    眉/栏间接续的两块几乎不重叠。取**所有相邻对的最小值**，避免一个三块组里只
    有一对重叠就整体误判成横向。
    """

    if len(rects) < 2:
        return "v"
    ratios = []
    for first, second in zip(rects, rects[1:]):
        overlap = min(first[3], second[3]) - max(first[1], second[1])
        reference = min(first[3] - first[1], second[3] - second[1])
        ratios.append(max(0.0, overlap) / reference if reference > 0 else 0.0)
    return "h" if min(ratios) >= MISTAKE_MERGE_SIDE_BY_SIDE else "v"


def _match_blocks_by_rects(rects: list, blocks: list) -> list:
    """按矩形把题块配成成员，返回**块下标**列表（顺序同 ``rects``，配不上则缺）。

    一对一贪心：交叠面积占矩形面积比例大的优先。宁可配不上（该成员缺席）也不
    乱配 —— 配错的后果是把别的题的半截拼进这道题。
    """

    pairs = []
    for rect_index, rect in enumerate(rects):
        rect_area = max(1e-9, (rect[2] - rect[0]) * (rect[3] - rect[1]))
        for block_index, block in enumerate(blocks or []):
            width = min(rect[2], block.x_end) - max(rect[0], block.x_start)
            height = min(rect[3], block.y_end) - max(rect[1], block.y_start)
            if width <= 0 or height <= 0:
                continue
            ratio = (width * height) / rect_area
            if ratio < MISTAKE_MERGE_MIN_OVERLAP:
                continue
            pairs.append((ratio, rect_index, block_index))
    pairs.sort(key=lambda item: item[0], reverse=True)
    used_rects: set[int] = set()
    used_blocks: set[int] = set()
    chosen: list = [None] * len(rects)
    for _ratio, rect_index, block_index in pairs:
        if rect_index in used_rects or block_index in used_blocks:
            continue
        used_rects.add(rect_index)
        used_blocks.add(block_index)
        chosen[rect_index] = block_index
    return [index for index in chosen if index is not None]


def _item_rect(members: list) -> list:
    """渲染项的外接矩形（合并组＝成员并集）。忽略名单就是按它比对的。"""

    return [
        min(block.x_start for block in members),
        min(block.y_start for block in members),
        max(block.x_end for block in members),
        max(block.y_end for block in members),
    ]


def _apply_manual_merges(blocks: list, merges: list) -> list:
    """把合并组作用到题块列表上，返回「渲染项」（合并组 + 未合并块）。

    每项 ``{"kind", "block_index", "members", "rect", "direction", "merge_id",
    "primary_index"}``。合并组的 ``block_index`` 取成员中最小的那个，未合并块用
    自己的 —— 两类中不会有重复（成员块已被占用，不会再单列）。
    """

    used: set[int] = set()
    items: list[dict] = []
    for group in merges or []:
        indices = [
            index
            for index in _match_blocks_by_rects(group["rects"], blocks)
            if index not in used
        ]
        if len(indices) < 2:
            continue
        used.update(indices)
        members = [blocks[index] for index in indices]
        member_rects = [
            [block.x_start, block.y_start, block.x_end, block.y_end]
            for block in members
        ]
        direction = group["direction"] or _guess_merge_direction(member_rects)
        primary = group["primary"] if group["primary"] < len(members) else 0
        items.append(
            {
                "kind": "merged",
                "block_index": min(block.block_index for block in members),
                "members": members,
                "rect": _item_rect(members),
                "direction": direction,
                "merge_id": group["id"],
                "primary_index": primary,
            }
        )
    for index, block in enumerate(blocks or []):
        if index in used:
            continue
        items.append(
            {
                "kind": "single",
                "block_index": block.block_index,
                "members": [block],
                "rect": _item_rect([block]),
                "direction": "v",
                "merge_id": "",
                "primary_index": 0,
            }
        )
    items.sort(key=lambda item: item["block_index"])
    return items


# ---- 跨页合并（一道题被页边界切成两半：一半在上一页尾部，一半在下一页头部） ----
#
# 与页内合并的**唯一**结构差异：成员必须带页号。``page.manual_merges[].rects`` 是
# 页内相对比例（0–1），页 9 的 0.891 与页 10 的 0.039 值域完全重叠 —— 后端没有任何
# 办法判断哪个属于哪一页。所以跨页组只能挂在文件顶层，成员写成
# ``{"page_no": 9, "rect": [x0, y0, x1, y1]}``。
#
# 由此带来三件必须一起处理的事：
#   1. 记录落在哪一页 —— 取 ``primary`` 成员所在页，卡片与页图才对得上；
#   2. 非宿主页上的那一半 —— 被「占用」，不再单独出记录，否则同一道题库里有两份；
#   3. 拼图要跨页取图 —— 宿主页重建前必须先把对页的块图按当前几何裁好。

#: 一个跨页组的成员上限。题干 + 选项 + 尾注是常态，再多只可能是脏数据。
MISTAKE_CROSS_PAGE_MAX_MEMBERS = 8


def _normalize_cross_page_members(raw) -> list:
    """校验跨页组的成员：每个成员必须带合法页号与合法页内矩形。"""

    members: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            page_no = int(item.get("page_no") or 0)
        except (TypeError, ValueError):
            continue
        rect = _normalize_mistake_rect(item.get("rect"))
        if page_no <= 0 or rect is None:
            continue
        members.append({"page_no": page_no, "rect": rect})
    return members


def _normalize_cross_page_merges(raw) -> list:
    """校验跨页组列表。

    刻意**拒绝**成员全在同一页的组：那种组属于 ``page.manual_merges``，放进来会让
    同一件事有两套落盘位置，重建时谁先谁后就成了隐式约定。宁可在这里丢掉。
    """

    groups: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        members = _normalize_cross_page_members(item.get("members"))
        if len(members) < 2 or len(members) > MISTAKE_CROSS_PAGE_MAX_MEMBERS:
            continue
        if len({member["page_no"] for member in members}) < 2:
            continue
        merge_id = str(item.get("id") or "").strip()
        if not _MERGE_ID_PATTERN.match(merge_id) or merge_id in seen:
            merge_id = _new_mistake_merge_id()
        seen.add(merge_id)
        try:
            primary = int(item.get("primary") or 0)
        except (TypeError, ValueError):
            primary = 0
        groups.append(
            {
                "id": merge_id,
                "members": members,
                # 跨页组只有「上下拼」一个正确答案：两页的横向坐标各自独立，左右
                # 拼没有意义。锁死在这里，前端也就不会长出必然被拒的入口。
                "direction": "v",
                "primary": max(0, min(primary, len(members) - 1)),
            }
        )
    return groups


def _cross_page_merges(batch_id: int) -> list:
    """读回本批次的跨页合并组。

    读不出来时按「没有」处理：调用点是重建流程，退化成「不合并」只是这次少拼一张
    图，而抛异常会让一次普通的翻页/框选直接失败。
    """

    try:
        return _normalize_cross_page_merges(
            _read_mistake_analysis(batch_id).get("cross_page_merges")
        )
    except MistakeAnalysisError as exc:
        print(f"[Mistake Cross Page Read Error] {type(exc).__name__}: {exc}")
        return []


def _mistake_page_blocks(batch_id: int, page_no: int, cache: dict) -> list:
    """取某页「自动基线 + 人工框」的块列表（带缓存，页不存在返回空列表）。

    跨页组要按矩形在**对页**上找成员，而重建一次的入口只带了一页的 entry。这里统一
    走「查缓存 → 没有就按同一套规则重算」，保证同一个矩形在两边解出的是同一个块。
    """

    if page_no in cache:
        return cache[page_no]
    try:
        entry = _mistake_page_entry(batch_id, page_no)
    except MistakeAnalysisError:
        entry = None
    blocks: list = []
    if entry:
        blocks = merge_manual_boxes(
            _blocks_from_meta(entry, page_no),
            entry.get("manual_boxes") or [],
            page_no=page_no,
            column_boundary=_page_column_boundary(entry),
        )
    cache[page_no] = blocks
    return blocks


def _resolve_cross_page_group(batch_id: int, group: dict, cache: dict):
    """把跨页组的成员逐个解成 ``(页号, 块)``；**任一成员配不上就整组作废**。

    页内合并允许「凑够两块就行」，跨页组不能：只解到一半意味着这道题的另一半会以
    独立卡片重新出现，用户看到的是「合了个寂寞」，还不如原样不动。
    """

    resolved: list = []
    for member in group.get("members") or []:
        blocks = _mistake_page_blocks(batch_id, member["page_no"], cache)
        indices = _match_blocks_by_rects([member["rect"]], blocks)
        if not indices:
            return None
        resolved.append((member["page_no"], blocks[indices[0]]))
    return resolved or None


def _apply_cross_page_merges(
    batch_id: int, page_no: int, blocks: list, groups: list, cache: dict
) -> tuple[list, set]:
    """把跨页组作用到本页，返回 ``(本页的跨页合并项, 本页被占用的块编号)``。

    - 宿主页（``primary`` 成员所在页）→ 产出合并项，成员图**跨页**取名；
    - 非宿主页 → 只产出「占用」，那一半不再单独出记录。

    项的形状与 :func:`_apply_manual_merges` 的产物一致，额外带 ``member_names`` /
    ``member_count`` / ``primary_block_index``：成员可能不在本页，``members`` 只留
    本页那几块（外接矩形、栏号都必须是页内的量）。
    """

    items: list[dict] = []
    consumed: set = set()
    for group in groups or []:
        members = group.get("members") or []
        if not any(member["page_no"] == page_no for member in members):
            continue
        resolved = _resolve_cross_page_group(batch_id, group, cache)
        if resolved is None:
            continue
        local = [block for member_page, block in resolved if member_page == page_no]
        if not local:
            continue
        consumed.update(block.block_index for block in local)
        primary_page, primary_block = resolved[group["primary"]]
        if primary_page != page_no:
            continue
        items.append(
            {
                "kind": "merged",
                "cross": True,
                "block_index": min(block.block_index for block in local),
                "members": local,
                "member_names": [
                    f"p{member_page:03d}_b{block.block_index:02d}.png"
                    for member_page, block in resolved
                ],
                "member_count": len(resolved),
                "rect": _item_rect(local),
                "direction": "v",
                "merge_id": group["id"],
                "primary_index": 0,
                "primary_block_index": primary_block.block_index,
            }
        )
    return items, consumed




def _rect_area(rect: list) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _rect_intersection_area(first: list, second: list) -> float:
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height


def _hidden_hit(rect: list, other: list) -> bool:
    """名单里的矩形 ``rect`` 是否命中某个渲染项 ``other``（＝它就是被删的那一块）。

    两个条件都要满足，缺一个都会出问题：

    - 交叠占**较小者** ≥ 阈值：重切把一个块拆成两半后，两半各自完全落在名单矩形
      里（比值 1.0），删除照样生效；只跟名单擦个边的大块比值很低，不会误删。
    - 交叠占**这一项自身** ≥ 阈值：反向保护。用户删掉一个小碎片（页眉、选项残段）
      之后，重切若把那一片并进了包含一道完整题的更大的块，必须判「不命中」——
      否则一次重切就顺手吞掉一道用户从没动过的题，而且不看卡片列表根本发现不了。
      真删错了让用户再点一次，比悄悄少一道题好。
    """

    overlap = _rect_intersection_area(rect, other)
    if overlap <= 0:
        return False
    smallest = min(_rect_area(rect), _rect_area(other))
    own = _rect_area(other)
    if smallest <= 1e-9 or own <= 1e-9:
        return False
    return (
        overlap / smallest >= MISTAKE_HIDE_MIN_OVERLAP
        and overlap / own >= MISTAKE_HIDE_MIN_OVERLAP
    )


def _normalize_mistake_hidden(raw) -> list:
    """规范化「被删除的题块」名单：一串 ``[x0,y0,x1,y1]`` 矩形。

    存几何而不是块序号，理由与人工合并组相同 —— 重切后块号会变，几何不会。
    """

    rects: list[list] = []
    seen: set[tuple] = set()
    for item in raw or []:
        fixed = _normalize_mistake_rect(item)
        if fixed is None:
            continue
        key = tuple(fixed)
        if key in seen:
            continue
        seen.add(key)
        rects.append(fixed)
        if len(rects) >= MISTAKE_HIDE_MAX_ENTRIES:
            break
    return rects


def _apply_hidden_blocks(items: list, hidden: list) -> tuple[list, list]:
    """剔掉被删除的渲染项，返回 ``(保留的项, 真正命中的矩形)``。

    第二个返回值要写回元数据：没命中任何项的矩形（比如用户删完又重切、几何全变了）
    留着只会在每次重建时做一遍无用比对，越积越多。
    """

    if not hidden:
        return items, []
    kept: list[dict] = []
    matched: list[list] = []
    for item in items:
        hit = None
        for rect in hidden:
            if _hidden_hit(rect, item["rect"]):
                hit = rect
                break
        if hit is None:
            kept.append(item)
        elif hit not in matched:
            matched.append(hit)
    return kept, matched


def _compose_merged_block_image(sources: list, target: Path, direction: str) -> None:
    """把成员块图拼成一张（白底）：v 上下堆（统一宽）、h 横向拼（统一高）。

    不缩放的那张原样贴，其余等比例缩放 —— 双栏左右两半的裁图宽度本来就有 1–2
    像素差，直接按原尺寸贴会留白缝。
    """

    images = []
    for path in sources or []:
        with Image.open(path) as handle:
            images.append(handle.convert("RGB"))
    if not images:
        raise ValueError("合并组没有可拼接的块图。")
    resample = getattr(Image, "LANCZOS", Image.BICUBIC)
    if direction == "h":
        height = max(image.height for image in images)
        scaled = [
            image
            if image.height == height
            else image.resize(
                (max(1, round(image.width * height / image.height)), height), resample
            )
            for image in images
        ]
        canvas = Image.new("RGB", (max(1, sum(im.width for im in scaled)), max(1, height)), "white")
        offset = 0
        for image in scaled:
            canvas.paste(image, (offset, 0))
            offset += image.width
    else:
        width = max(image.width for image in images)
        scaled = [
            image
            if image.width == width
            else image.resize(
                (width, max(1, round(image.height * width / image.width))), resample
            )
            for image in images
        ]
        canvas = Image.new("RGB", (max(1, width), max(1, sum(im.height for im in scaled))), "white")
        offset = 0
        for image in scaled:
            canvas.paste(image, (0, offset))
            offset += image.height
    canvas.save(target, format="PNG")


def _rebuild_mistake_page_records(db, batch, batch_id: int, page_no: int, page_path, entry: dict) -> dict:
    """按「自动基线 + 人工框」重建一页的题块记录。

    与整批切题共用同一套规则（合并 → 裁图 → 按矩形重叠继承作答状态 → 删旧插新），
    区别只在范围限死一页：用户改一页不该等整批重新渲染，也不该把其他页没动过的
    块图重裁一遍。
    """

    blocks = merge_manual_boxes(
        _blocks_from_meta(entry, page_no),
        entry.get("manual_boxes") or [],
        page_no=page_no,
        column_boundary=_page_column_boundary(entry),
    )
    if not blocks:
        raise PageSplitError("本页没有可用的题块：请先切题，或画一个框。")
    # 跨页组：本页可能是宿主（出记录），也可能只是另一半（被占用）。先把对页的块图
    # 按当前几何裁好 —— 拼图跨页取文件，晚一步拿到的是上一版，或者根本没有。
    blocks_cache: dict = {page_no: blocks}
    cross_groups = _cross_page_merges(batch_id)
    _ensure_cross_page_block_images(batch_id, page_no, cross_groups, blocks_cache)
    cross_items, cross_consumed = _apply_cross_page_merges(
        batch_id, page_no, blocks, cross_groups, blocks_cache
    )
    # 人工合并组：矩形→成员块，得到「合并组 + 未合并块」的渲染项列表。
    # 被跨页组占用的块先摘掉，否则同一块会既进页内合并组、又进跨页项。
    remaining = [block for block in blocks if block.block_index not in cross_consumed]
    merges = _normalize_mistake_merges(entry.get("manual_merges"))
    items = _apply_manual_merges(remaining, merges)
    items = items + cross_items
    items.sort(key=lambda item: item["block_index"])
    # 方向没指定时由几何判定，判定结果要**回写进元数据**：否则落盘的是空方向，
    # 前端拿不到「当前是上下还是左右」，也就无从提供切换入口。
    resolved = {item["merge_id"]: item for item in items if item["kind"] == "merged"}
    merges = [
        {
            **group,
            "direction": resolved[group["id"]]["direction"],
            "primary": resolved[group["id"]]["primary_index"],
        }
        for group in merges
        if group["id"] in resolved
    ]
    # 被删掉的题块：在合并之后再剔，这样「删掉一整道合并题」＝名单里那个外接矩形
    # 命中合并项，一次删干净，不用逐个成员去点。
    hidden = _normalize_mistake_hidden(entry.get("hidden_blocks"))
    items, hidden = _apply_hidden_blocks(items, hidden)

    previous = (
        db.query(MistakeRecord)
        .filter(
            MistakeRecord.batch_id == batch_id,
            MistakeRecord.page_no == page_no,
        )
        .order_by(MistakeRecord.block_index.asc())
        .all()
    )
    matched = _match_mistake_records(previous, blocks)

    _crop_mistake_page_blocks(batch_id, page_no, page_path, blocks)
    blocks_dir = _mistake_blocks_dir(batch_id)
    composed: set[str] = set()
    for item in items:
        members = item["members"]
        # 跨页项自带成员图名（成员不在本页）与 primary 的块号；页内项按本页拼。
        # primary 必须按**块号**回查作答状态：跨页项的 primary_block_index 是它自己
        # 那一页的块号，直接用 members[primary] 会在别的页上取错。
        primary_index = item.get("primary_index", 0)
        primary = members[primary_index] if primary_index < len(members) else members[0]
        primary_block_index = item.get("primary_block_index", primary.block_index)
        member_names = item.get("member_names") or [
            f"p{page_no:03d}_b{block.block_index:02d}.png" for block in members
        ]
        if item["kind"] == "merged":
            crop_name = f"p{page_no:03d}_m{item['merge_id']}.png"
            staging = blocks_dir / f".staging_{crop_name}"
            try:
                _compose_merged_block_image(
                    [blocks_dir / name for name in member_names],
                    staging,
                    item["direction"],
                )
            except (OSError, ValueError) as exc:
                # 拼图失败就把成员当独立题块留下：宁可多一道题，也不能让这道题
                # 从记录里消失。
                print(f"[Mistake Merge Compose Error] {type(exc).__name__}: {exc}")
                crop_name = member_names[item["primary_index"]]
            else:
                os.replace(staging, blocks_dir / crop_name)
                composed.add(crop_name)
        else:
            crop_name = member_names[0]
        columns = {block.column_index for block in members}
        record = MistakeRecord(
            batch_id=batch_id,
            student_id=batch.student_id,
            subject=batch.subject or "math",
            page_no=page_no,
            block_index=item["block_index"],
            block_y_start=round(min(block.y_start for block in members), 6),
            block_y_end=round(max(block.y_end for block in members), 6),
            block_x_start=round(min(block.x_start for block in members), 6),
            block_x_end=round(max(block.x_end for block in members), 6),
            # 跨栏合并后不再属于某一栏；只有成员同栏才保留栏号
            column_index=list(columns)[0] if len(columns) == 1 else 0,
            image_block=_mistake_block_web_url(batch_id, crop_name),
            recognize_status="pending",
            grad_status="unknown",
            merge_id=item["merge_id"] if item["kind"] == "merged" else "",
            merged_block_count=item.get("member_count") or len(members),
            block_images=json.dumps(
                [_mistake_block_web_url(batch_id, name) for name in member_names],
                ensure_ascii=False,
            )
            if item["kind"] == "merged"
            else "[]",
        )
        origin = matched.get(primary_block_index)
        if origin is not None:
            for field_name in MISTAKE_CARRY_OVER_FIELDS:
                setattr(record, field_name, getattr(origin, field_name))
        db.add(record)
    # 拆分后旧合成图会留在目录里变成孤儿（名字里带 merge id，不会被块图清理扫到）
    for path in blocks_dir.glob(f"p{page_no:03d}_m*.png"):
        if path.name not in composed:
            try:
                path.unlink()
            except OSError:
                pass
    # 状态已经复制进新记录，旧记录整体删除（含没匹配上的）—— 留着就是同一道题
    # 在库里存在两份，错题本与统计都会跟着翻倍。
    for stale in previous:
        db.delete(stale)
    db.flush()
    return {
        "block_count": len(items),
        "removed": len(previous),
        "merges": merges,
        "hidden_blocks": hidden,
    }


def run_mistake_cut_task(
    task_id: str,
    batch_id: int,
    page_layouts: Optional[dict] = None,
) -> None:
    """切题任务：转页图 → 判栏 → 行投影切块 → 合并题块（裁剪题块图并写记录）。

    ``page_layouts`` 非空表示人工改过栏目后重切：``{"1": "double"}`` 人工指定
    的栏目（覆盖自动判栏），``{"1": {"mode": "double", "boundary": 0.6}}`` 还
    带上用户拖过的分栏线。

    重切会按纵向+横向重叠继承已有记录的作答状态。
    """

    db = _mistake_session()
    step_key = MISTAKE_CUT_STEPS[0]["key"]
    try:
        batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
        if batch is None:
            DOCUMENT_TASKS.fail(task_id, "批次不存在或已被删除。")
            return
        source = _mistake_source_path(batch_id)
        if source is None:
            DOCUMENT_TASKS.fail(task_id, "批次的源文件已丢失，请重新上传。")
            return

        # 先探一次元数据可读性：重切要把已有人工框/合并/删除名单带过去，而读失败时
        # 只能拿空壳顶上 —— 那等于「一点重切、辛苦画的框全没了」，最后还会把空壳
        # 写回文件。放在这里是为了在渲染整批页图之前就失败，不必白跑几分钟。
        try:
            _read_mistake_analysis(batch_id)
        except MistakeAnalysisError as exc:
            message = str(exc)
            print(f"[Mistake Cut Error] batch={batch_id} 元数据不可读：{message}")
            _mark_mistake_batch_failed(batch_id, message)
            DOCUMENT_TASKS.step_error(task_id, step_key, message)
            return

        DOCUMENT_TASKS.step_start(task_id, step_key, "正在把扫描件转成页图…")
        page_paths = render_source_to_pages(
            source,
            _mistake_pages_dir(batch_id),
            dpi=MISTAKE_RENDER_DPI,
            max_pages=MISTAKE_MAX_SOURCE_PAGES,
            stem="page",
        )
        DOCUMENT_TASKS.check_cancelled(task_id)
        batch.page_count = len(page_paths)
        db.commit()
        DOCUMENT_TASKS.update(
            task_id, progress=30, log=f"已生成 {len(page_paths)} 张页图。"
        )

        step_key = "line_projection"
        DOCUMENT_TASKS.step_start(task_id, step_key, "正在判栏并做行投影切块…")
        # PDF 文本层：既用来判栏（电子版最准），也用来找题号切点（行投影切不开
        # 密排单栏卷子，见 page_block_split 的「题号切点」一节）。整份只读一次，
        # 带文字的那份顺手去掉文字就是判栏要的那份。
        # 读取失败 / 纯扫描件时两者都为空：判栏自动回退图像，切点一个都不加。
        page_text_lines = extract_pdf_text_lines(source)
        page_text_rows = {
            page_no: [(x0, x1, y0, y1) for x0, x1, y0, y1, _text in rows]
            for page_no, rows in page_text_lines.items()
        }
        normalized_layouts = _normalize_mistake_page_layouts(page_layouts)
        analyses = analyze_page_images(
            page_paths,
            start_page_no=1,
            page_text_rows=page_text_rows,
            page_text_lines=page_text_lines,
            page_layouts=normalized_layouts,
        )
        # 人工框选：整批重切时把已存的人工框原样带过来。它们叠在 blocks 之上，
        # 不属于「按行投影重算」的范围；不带上就成了「一点重切、辛苦画的框全没了」。
        previous_pages = {
            str(item.get("page_no")): item
            for item in (_read_mistake_analysis(batch_id).get("pages") or [])
            if isinstance(item, dict)
        }
        for analysis in analyses:
            analysis.manual_boxes = _normalize_mistake_boxes(
                (previous_pages.get(str(analysis.page_no)) or {}).get("manual_boxes")
            )
            # 人工合并同理：成员用几何定位，重切后按重叠找回来，因此原样带过即可
            analysis.manual_merges = _normalize_mistake_merges(
                (previous_pages.get(str(analysis.page_no)) or {}).get("manual_merges")
            )
            # 用户删掉的题块也带过：不落盘的话，一次重切就让它们全部复活
            analysis.hidden_blocks = _normalize_mistake_hidden(
                (previous_pages.get(str(analysis.page_no)) or {}).get("hidden_blocks")
            )
        DOCUMENT_TASKS.check_cancelled(task_id)

        step_key = "merge_blocks"
        DOCUMENT_TASKS.step_start(task_id, step_key, "正在裁剪题块图并写入记录…")
        # 题块图按页内序号命名，重切后序号可能变少，先清空目录避免残留孤儿图
        _reset_directory(_mistake_blocks_dir(batch_id))
        blocks_dir = _mistake_blocks_dir(batch_id)
        page_by_no = {index + 1: path for index, path in enumerate(page_paths)}

        total_blocks = 0
        page_total = max(1, len(analyses))
        # 第一趟：把每一页的块图裁完。跨页组的成员跨两页，拼图要读对页的文件；原本
        # 「裁一页 → 建这一页的记录」交替着来，先建的那页会拿到还没裁的对页。
        blocks_by_page: dict = {}
        for analysis in analyses:
            page_path = page_by_no.get(analysis.page_no)
            if page_path is None:
                continue
            # 落盘的 blocks 是「自动基线」，裁图用的是「基线 + 人工框」的合并结果。
            # 两者分开，是为了让用户删掉一个画错的框后，被它压住的自动块能原样
            # 回来（合并结果一旦写回 blocks，被吸收的碎片就再也找不回了）。
            page_blocks = merge_manual_boxes(
                analysis.blocks,
                analysis.manual_boxes,
                page_no=analysis.page_no,
                column_boundary=analysis.layout.boundary,
            )
            blocks_by_page[analysis.page_no] = page_blocks
            for block in page_blocks:
                crop_path = (
                    blocks_dir / f"p{analysis.page_no:03d}_b{block.block_index:02d}.png"
                )
                crop_region(
                    page_path,
                    crop_path,
                    y_start=block.y_start,
                    y_end=block.y_end,
                    x_start=block.x_start,
                    x_end=block.x_end,
                )
            DOCUMENT_TASKS.check_cancelled(task_id)

        # 跨页组：成员找不全的组直接丢掉，不留一组永远解不开的成员在文件里
        cross_groups = [
            group
            for group in _cross_page_merges(batch_id)
            if _resolve_cross_page_group(batch_id, group, blocks_by_page) is not None
        ]

        # 第二趟：建记录（块图已齐，跨页拼图随时可读）
        for analysis in analyses:
            page_path = page_by_no.get(analysis.page_no)
            if page_path is None:
                continue
            page_blocks = blocks_by_page.get(analysis.page_no) or []
            previous = (
                db.query(MistakeRecord)
                .filter(
                    MistakeRecord.batch_id == batch_id,
                    MistakeRecord.page_no == analysis.page_no,
                )
                .order_by(MistakeRecord.block_index.asc())
                .all()
            )
            matched = _match_mistake_records(previous, page_blocks)
            cross_items, cross_consumed = _apply_cross_page_merges(
                batch_id, analysis.page_no, page_blocks, cross_groups, blocks_by_page
            )
            # 人工合并：成员图已裁好，直接按方向合成，建**一条**记录
            merges = _normalize_mistake_merges(analysis.manual_merges)
            remaining = [
                block
                for block in page_blocks
                if block.block_index not in cross_consumed
            ]
            items = _apply_manual_merges(remaining, merges)
            items = items + cross_items
            items.sort(key=lambda item: item["block_index"])
            # 与单页重建一致：只保留真正成组的，并把几何判定出的方向写回元数据
            resolved = {
                item["merge_id"]: item for item in items if item["kind"] == "merged"
            }
            analysis.manual_merges = [
                {
                    **group,
                    "direction": resolved[group["id"]]["direction"],
                    "primary": resolved[group["id"]]["primary_index"],
                }
                for group in merges
                if group["id"] in resolved
            ]
            # 被删掉的题块同理：几何锚定，重切后按重叠剔掉，命中的版本写回元数据，
            # 没命中的（几何已变）自然从名单里掉出去，不会越积越多。
            items, analysis.hidden_blocks = _apply_hidden_blocks(
                items, _normalize_mistake_hidden(analysis.hidden_blocks)
            )
            for item in items:
                members = item["members"]
                # 跨页项自带成员图名（成员不在本页）与 primary 的块号
                primary_index = item.get("primary_index", 0)
                primary = (
                    members[primary_index] if primary_index < len(members) else members[0]
                )
                primary_block_index = item.get(
                    "primary_block_index", primary.block_index
                )
                member_names = item.get("member_names") or [
                    f"p{analysis.page_no:03d}_b{block.block_index:02d}.png"
                    for block in members
                ]
                if item["kind"] == "merged":
                    crop_name = f"p{analysis.page_no:03d}_m{item['merge_id']}.png"
                    try:
                        _compose_merged_block_image(
                            [blocks_dir / name for name in member_names],
                            blocks_dir / crop_name,
                            item["direction"],
                        )
                    except (OSError, ValueError) as exc:
                        print(
                            f"[Mistake Merge Compose Error] {type(exc).__name__}: {exc}"
                        )
                        crop_name = member_names[item["primary_index"]]
                else:
                    crop_name = member_names[0]
                columns = {block.column_index for block in members}
                record = MistakeRecord(
                    batch_id=batch_id,
                    student_id=batch.student_id,
                    subject=batch.subject or "math",
                    page_no=analysis.page_no,
                    block_index=item["block_index"],
                    block_y_start=round(min(b.y_start for b in members), 6),
                    block_y_end=round(max(b.y_end for b in members), 6),
                    block_x_start=round(min(b.x_start for b in members), 6),
                    block_x_end=round(max(b.x_end for b in members), 6),
                    column_index=list(columns)[0] if len(columns) == 1 else 0,
                    image_block=_mistake_block_web_url(batch_id, crop_name),
                    recognize_status="pending",
                    grad_status="unknown",
                    merge_id=item["merge_id"] if item["kind"] == "merged" else "",
                    merged_block_count=item.get("member_count") or len(members),
                    block_images=json.dumps(
                        [
                            _mistake_block_web_url(batch_id, name)
                            for name in member_names
                        ],
                        ensure_ascii=False,
                    )
                    if item["kind"] == "merged"
                    else "[]",
                )
                origin = matched.get(primary_block_index)
                if origin is not None:
                    for field_name in MISTAKE_CARRY_OVER_FIELDS:
                        setattr(record, field_name, getattr(origin, field_name))
                db.add(record)
                total_blocks += 1
            # 状态已经复制进新记录，旧记录整体删除（含没匹配上的）—— 否则同一道题
            # 会在库里留两份：一份带状态、份不带，错题本与统计都会跟着翻倍。
            for stale in previous:
                db.delete(stale)
            db.flush()
            DOCUMENT_TASKS.update(
                task_id,
                progress=60 + int(analysis.page_no / page_total * 35),
                log=(
                    f"第 {analysis.page_no}/{len(analyses)} 页："
                    f"切出 {len(items)} 个题块。"
                ),
            )
            DOCUMENT_TASKS.check_cancelled(task_id)

        _write_mistake_analysis(batch_id, analyses, cross_groups)
        batch.status = "reviewing"
        batch.note = ""
        db.commit()
        DOCUMENT_TASKS.step_complete_all(task_id)
        DOCUMENT_TASKS.complete(
            task_id,
            batch_id=batch_id,
            page_count=len(page_paths),
            block_count=total_blocks,
            log=f"切题完成：{len(page_paths)} 页，共 {total_blocks} 个题块。",
        )
    except TaskCancelled:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成步骤级失败，前端能定位到哪一步
        db.rollback()
        message = str(exc) or type(exc).__name__
        print(f"[Mistake Cut Error] batch={batch_id} step={step_key}: {message}")
        _mark_mistake_batch_failed(batch_id, message)
        DOCUMENT_TASKS.step_error(task_id, step_key, message)
    finally:
        db.close()


# ---- 异步任务：识别 ----


def run_mistake_recognize_task(
    task_id: str, batch_id: int, record_ids: list, engine: str = ""
) -> None:
    """识别任务：逐块识别（N/M）→ 后处理归一 → 写入记录。

    单块失败**不阻断整批**：该块标 ``recognize_status = failed``，错误汇总进任务状态，
    前端可单块重试（沿用「失败卡片加重试」的既有模式）。识别进度条的 M 是选中题数，
    不是总题块数 —— 未点选为错题的题块根本不进队列。
    """

    db = _mistake_session()
    step_key = MISTAKE_RECOGNIZE_STEPS[0]["key"]
    try:
        batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
        if batch is None:
            DOCUMENT_TASKS.fail(task_id, "批次不存在或已被删除。")
            return

        ordered_ids: list[int] = []
        for raw in record_ids or []:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value not in ordered_ids:
                ordered_ids.append(value)
        if ordered_ids:
            records = (
                db.query(MistakeRecord)
                .filter(
                    MistakeRecord.batch_id == batch_id,
                    MistakeRecord.id.in_(ordered_ids),
                )
                .order_by(MistakeRecord.page_no.asc(), MistakeRecord.block_index.asc())
                .all()
            )
        else:
            records = []
        total = len(records)
        if total == 0:
            DOCUMENT_TASKS.step_complete_all(task_id)
            DOCUMENT_TASKS.complete(
                task_id, batch_id=batch_id, recognized=0, failed=0,
                log="没有需要识别的题目。",
            )
            return

        subject = batch.subject or "math"
        # 教材树按学科取一次、整批共用（数学是活动大纲，物化是内置只读树）。
        # 取错树比不取树更糟：拿数学树去套物理题会把物理题打上数学章节。
        curriculum = get_subject_curriculum_tree(subject)
        pending: list[int] = []
        done_ids: list[int] = []
        errors: list[str] = []
        providers: list[str] = []

        DOCUMENT_TASKS.step_start(task_id, step_key, f"共 {total} 道待识别…")
        # 把队列挂到任务上：前端据此渲染「识别中 / 排队中」角标，并在轮到某道题时
        # 把它锁成只读 —— 否则用户手写完，识别结果落库会无声覆盖。
        DOCUMENT_TASKS.update(
            task_id,
            recognize_queue=[record.id for record in records],
            recognize_done=[],
            recognize_current=None,
        )
        for index, record in enumerate(records, start=1):
            DOCUMENT_TASKS.check_cancelled(task_id)
            DOCUMENT_TASKS.step_start(
                task_id, step_key, f"正在识别第 {index}/{total} 题…"
            )
            DOCUMENT_TASKS.update(
                task_id,
                recognize_current=record.id,
                progress=int((index - 1) / total * 95),
                log=f"正在识别第 {index}/{total} 题…",
            )
            image_path, image_error = _mistake_record_image_path(record)
            if image_path is None:
                record.recognize_status = "failed"
                errors.append(
                    f"第 {index} 题（记录 {record.id}）：{image_error}"
                )
                done_ids.append(record.id)
                db.commit()
                DOCUMENT_TASKS.update(
                    task_id,
                    recognize_done=list(done_ids),
                    progress=int(index / total * 95),
                )
                continue
            try:
                payload, label = recognize_mistake_block(
                    str(image_path), subject, engine
                )
            except Exception as exc:  # noqa: BLE001 - 单块失败不阻断整批
                record.recognize_status = "failed"
                errors.append(f"第 {index} 题（记录 {record.id}）：{exc}")
                done_ids.append(record.id)
                db.commit()
                DOCUMENT_TASKS.update(
                    task_id,
                    recognize_done=list(done_ids),
                    progress=int(index / total * 95),
                )
                continue
            record.recognize_status = "done"
            if label not in providers:
                providers.append(label)
            # 模型结果立刻写进这条记录并提交 —— 原来是攒在内存里等整批跑完才统一归一，
            # 于是「状态已就绪、点进去题面还是空的」，前端拿不到任何中间结果。
            # 逐题落库之后，识别一道就能审校一道。
            _apply_mistake_payload(record, payload, curriculum)
            pending.append(record.id)
            done_ids.append(record.id)
            db.commit()
            DOCUMENT_TASKS.update(
                task_id,
                recognize_done=list(done_ids),
                progress=int(index / total * 95),
            )

        step_key = "write_records"
        DOCUMENT_TASKS.step_start(task_id, step_key, "正在写入记录…")
        DOCUMENT_TASKS.update(task_id, recognize_current=None)
        db.commit()
        DOCUMENT_TASKS.step_complete_all(task_id)
        DOCUMENT_TASKS.complete(
            task_id,
            batch_id=batch_id,
            recognized=len(pending),
            failed=len(errors),
            providers=providers,
            errors=errors[:8],
            log=(
                f"识别完成：成功 {len(pending)} 道，失败 {len(errors)} 道。"
                + (f" 失败详情：{'；'.join(errors[:3])}" if errors else "")
            ),
        )
    except TaskCancelled:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        message = str(exc) or type(exc).__name__
        print(f"[Mistake Recognize Error] batch={batch_id} step={step_key}: {message}")
        DOCUMENT_TASKS.step_error(task_id, step_key, message)
    finally:
        db.close()


# ---- 入库 ----


def _import_mistake_record_to_bank(
    db: Session, record: MistakeRecord, *, force: bool = False
) -> dict:
    """把一条错题记录写进题库，返回结构化结果（成功 / 已入库 / 撞车）。

    幂等：``question_id`` 非空即视为已入库，重复调用直接返回 ``already``，不会
    在题库里落第二份。命中查重且未显式 ``force`` 时**不静默丢弃** —— 把撞车信息
    回传前端逐题确认（实施计划 §6）。
    """

    if record.question_id:
        return {
            "status": "already",
            "record_id": record.id,
            "question_id": record.question_id,
            "message": "该题已入库，无需重复操作。",
        }
    # 一期「物化错题不进题库」的约束已作废 —— 用户决策：物理（教科版）、化学
    # （人教版）同样要建错题库。三科都入库，差别只在 subject 字段与所挂的教材树；
    # origin 记成 mistake，让题库能把错题与自录/导入题区分开。
    subject_value = normalize_subject(record.subject or "math")
    content = str(record.content or "").strip()
    if not content:
        return {
            "status": "skipped",
            "record_id": record.id,
            "message": "题面尚未识别，无法入库。",
        }

    question_type = (
        record.question_type
        if record.question_type in MISTAKE_QUESTION_TYPES
        else "detailed_answer"
    )
    prepared = _strip_leading_question_number(
        normalize_choice_options_to_latex(normalize_fillin_macro(content))
    )
    batch = db.query(MistakeBatch).filter(MistakeBatch.id == record.batch_id).first()
    # 来源优先级：审校页手填的 > 上次入库时的快照 > 批次标题 > 扫描文件名。
    # 手填排第一是本轮的关键 —— 用户在审校页写了「2025 全国甲卷」，入库就该是它，
    # 而不是被批次文件名（如「IMG_2043.jpg」）顶掉。
    source = normalize_source(
        record.source
        or record.snapshot_source
        or (batch.title if batch else "")
        or (batch.source_name if batch else "")
    )
    # 学段/章节缺失时与题库录入表单同口径归入「未分类」：留空会让这道题在题库的
    # 「章节」筛选里彻底找不到（既不属于任何章节，也不属于未分类桶）。
    category_compulsory = str(record.category_compulsory or "").strip() or "未分类"
    category_chapter = str(record.category_chapter or "").strip() or "未分类"
    category_knowledge = str(record.category_knowledge or "").strip()

    if not force:
        dup_id, dup_sim = find_duplicate_question(
            db, prepared, question_type, subject_value
        )
        if dup_id is not None:
            payload = duplicate_warning_payload(db, dup_id, dup_sim)
            payload.update(
                {
                    "status": "duplicate",
                    "record_id": record.id,
                    "question_no": record.question_no or "",
                    "new_preview": prepared[:120],
                }
            )
            return payload

    question = Question(
        subject=subject_value,
        origin="mistake",
        content=prepared,
        content_fingerprint=_normalize_question_content(prepared),
        question_type=question_type,
        category_compulsory=category_compulsory,
        category_chapter=category_chapter,
        category_knowledge=category_knowledge,
        difficulty=normalize_difficulty(record.difficulty or None),
        source=source,
        answer_markdown=record.answer_markdown or "",
        knowledge_list=normalize_tag_list(record.knowledge_tags, field="knowledge_list"),
        solve_method=normalize_tag_list(record.solve_method, field="solve_method"),
        tags=str(record.tags or ""),
        related_curriculums=parse_related_curriculums(record.related_curriculums),
    )
    question.image_paths = normalize_upload_asset_references(
        list(record.figure_images or []),
        uploads_dir=UPLOAD_DIR,
        url_prefix=UPLOAD_DIR_REL,
    )
    db.add(question)
    db.flush()

    db.add(
        QuestionCurriculum(
            question_id=question.id,
            version_code=get_subject_version_code(subject_value),
            compulsory=category_compulsory,
            chapter=category_chapter,
            knowledge=category_knowledge,
        )
    )
    record.question_id = question.id
    record.snapshot_source = source
    return {
        "status": "imported",
        "record_id": record.id,
        "question_id": question.id,
        "question_no": record.question_no or "",
    }


# ---- 端点：学生 / 批次 ----


@app.get("/api/students")
def list_students(db: Session = Depends(get_db)):
    """错题工作台的学生列表（一期恒为单条，表结构按多学生设计）。"""

    student = ensure_mistake_student(db)
    db.commit()
    return {"status": "success", "students": [student.to_dict()]}


@app.post("/api/mistakes/batches")
def create_mistake_batch(
    file: UploadFile = File(...),
    subject: str = Form("math"),
    title: str = Form(""),
    batch_date: str = Form(""),
    student_name: str = Form(""),
    student_grade: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """新建批次：只落盘源文件 + 建批次记录。

    转页图与切块放在异步任务里（见 ``run_mistake_cut_task``）—— 12 页 200 DPI 渲染
    要好几秒，放进这个请求会拖住首屏；而且切块步骤条的第 1 步本来就是「转页图」。
    """

    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension not in MISTAKE_SOURCE_EXTENSIONS:
        return JSONResponse(
            content={
                "status": "error",
                "message": "只支持 PDF 或图片（png / jpg / webp / bmp / tif）。",
            },
            status_code=400,
        )

    subject_value = str(subject or "math").strip().lower()
    if subject_value not in MISTAKE_SUBJECTS:
        subject_value = "other"

    try:
        raw = read_stream_limited(file.file, MAX_MISTAKE_SOURCE_BYTES)
    except UploadTooLargeError:
        return JSONResponse(
            content={"status": "error", "message": "文件过大，请上传 50MB 以内的扫描件。"},
            status_code=413,
        )
    if extension == ".pdf" and not raw.lstrip().startswith(b"%PDF-"):
        return JSONResponse(
            content={"status": "error", "message": "文件内容不是有效的 PDF 文档。"},
            status_code=400,
        )

    parsed_date = None
    if str(batch_date or "").strip():
        try:
            parsed_date = datetime.date.fromisoformat(str(batch_date).strip())
        except ValueError:
            parsed_date = None
    if parsed_date is None:
        parsed_date = datetime.date.today()

    try:
        student = ensure_mistake_student(
            db, name=student_name, grade=student_grade
        )
        batch = MistakeBatch(
            student_id=student.id,
            subject=subject_value,
            title=str(title or "").strip()
            or f"{parsed_date.isoformat()} {MISTAKE_SUBJECT_LABELS.get(subject_value, '理科')}错题",
            batch_date=parsed_date,
            source_name=filename[:200],
            page_count=0,
            status="pending",
            note=str(note or "").strip(),
        )
        db.add(batch)
        db.flush()

        batch_dir = _mistake_batch_dir(batch.id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        source_path = batch_dir / f"source{extension}"
        with open(source_path, "wb") as handle:
            handle.write(raw)
        harden_private_path(source_path)

        payload = batch.to_dict(stats=_mistake_record_stats([]))
        batch_id = batch.id
        student_payload = student.to_dict()
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Mistake Batch Create Error] {type(e).__name__}: {e}")
        return JSONResponse(
            content={"status": "error", "message": f"新建批次失败: {str(e)}"},
            status_code=500,
        )

    return {
        "status": "success",
        "batch": payload,
        "student": student_payload,
        "source_url": _mistake_web_url(batch_id, source_path.name),
    }


@app.get("/api/mistakes/batches")
def list_mistake_batches(
    page: int = 1,
    page_size: int = 20,
    subject: Optional[str] = None,
    student_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """批次列表（分页）。统计用一条聚合查询取回，避免每行再查一次。"""

    try:
        page_value = max(1, int(page))
        size_value = max(1, min(100, int(page_size)))
    except (TypeError, ValueError):
        page_value, size_value = 1, 20

    query = db.query(MistakeBatch)
    if subject and str(subject).strip().lower() in MISTAKE_SUBJECTS:
        query = query.filter(MistakeBatch.subject == str(subject).strip().lower())
    if student_id:
        query = query.filter(MistakeBatch.student_id == int(student_id))

    total = query.count()
    batches = (
        query.order_by(MistakeBatch.id.desc())
        .offset((page_value - 1) * size_value)
        .limit(size_value)
        .all()
    )

    stats_map: dict[int, list] = {}
    batch_ids = [batch.id for batch in batches]
    if batch_ids:
        rows = (
            db.query(
                MistakeRecord.batch_id,
                MistakeRecord.grad_status,
                MistakeRecord.recognize_status,
                MistakeRecord.include_in_handout,
                MistakeRecord.question_id,
            )
            .filter(MistakeRecord.batch_id.in_(batch_ids))
            .all()
        )
        for row in rows:
            stats_map.setdefault(row[0], []).append(
                {
                    "grad_status": row[1],
                    "recognize_status": row[2],
                    "include_in_handout": row[3],
                    "question_id": row[4],
                }
            )

    student = ensure_mistake_student(db)
    db.commit()
    items = [
        batch.to_dict(stats=_mistake_record_stats(stats_map.get(batch.id, [])))
        for batch in batches
    ]
    return {
        "status": "success",
        "total": total,
        "page": page_value,
        "page_size": size_value,
        "students": [student.to_dict()],
        "subjects": [
            {"value": value, "label": MISTAKE_SUBJECT_LABELS[value]}
            for value in MISTAKE_SUBJECTS
        ],
        "status_labels": MISTAKE_STATUS_LABELS,
        "batches": items,
    }


@app.get("/api/mistakes/batches/{batch_id}")
def get_mistake_batch(batch_id: int, db: Session = Depends(get_db)):
    """批次详情：批次 + 页图与切块元数据 + 全部题目记录 + 错因词表。"""

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )

    records = (
        db.query(MistakeRecord)
        .filter(MistakeRecord.batch_id == batch_id)
        .order_by(MistakeRecord.page_no.asc(), MistakeRecord.block_index.asc())
        .all()
    )
    try:
        analysis = _read_mistake_analysis(batch_id)
    except MistakeAnalysisError as exc:
        # 不降级成空 pages：那样界面会把「元数据坏了」显示成「这个批次没有题块」，
        # 用户看不到任何异常，接着按空状态重新切题 —— 正好把原件覆盖掉。
        # 返回 409 后前端会 toast 提示并退回批次列表（mistake.js 的 !res.ok 分支）。
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=409
        )
    page_meta = {
        str(item.get("page_no")): item
        for item in analysis.get("pages") or []
        if isinstance(item, dict)
    }

    pages: list[dict] = []
    for page_no in range(1, int(batch.page_count or 0) + 1):
        page_file = _mistake_pages_dir(batch_id) / f"page_{page_no}.png"
        if not page_file.is_file():
            continue
        meta = page_meta.get(str(page_no), {})
        pages.append(
            {
                "page_no": page_no,
                "url": _page_web_url(batch_id, page_file.name),
                "width": meta.get("width", 0),
                "height": meta.get("height", 0),
                "blocks": meta.get("blocks", []),
                "gap_candidates": meta.get("gap_candidates", []),
                "snap_points": meta.get("snap_points", []),
                # 栏结构：前端据此画分栏线、显示「单栏/双栏」开关与置信度提示
                "layout": meta.get("layout") or {
                    "mode": COLUMN_SINGLE,
                    "boundary": 1.0,
                    "source": "assumed",
                    "confidence": 1.0,
                },
                "column_boundary": meta.get("column_boundary", 1.0),
                # 按栏吸附池 / 候选切点（key 为栏号字符串）
                "column_snap_points": meta.get("column_snap_points") or {},
                "column_gap_candidates": meta.get("column_gap_candidates") or {},
                # 人工框选矩形（[[x0,y0,x1,y1], …]）—— 前端回显已有框、并与记录比对
                # 出「哪些框还没落库」以决定要不要画预览卡片
                "manual_boxes": meta.get("manual_boxes") or [],
                # 人工合并组（[{id, rects, direction, primary}]）—— 前端据此给合并
                # 后的卡片打「已合并 N 块」标记、并提供拆分 / 调序 / 切方向
                "manual_merges": meta.get("manual_merges") or [],
                # 被用户删掉的题块（[[x0,y0,x1,y1], …]）—— 前端显示「已隐藏 N 块」
                # 与「恢复」入口；没有它，用户就无从知道自己删过什么、也没法反悔
                "hidden_blocks": meta.get("hidden_blocks") or [],
            }
        )

    # 库里存的是裸路径（识别取图、导出插图都要按路径找文件），指纹在出口补。
    record_items = [
        _mistake_record_client_payload(record, batch_id) for record in records
    ]

    student = (
        db.query(Student).filter(Student.id == batch.student_id).first()
        if batch.student_id
        else None
    )
    return {
        "status": "success",
        "batch": batch.to_dict(stats=_mistake_record_stats(records)),
        "student": student.to_dict() if student else None,
        "subjects": [
            {"value": value, "label": MISTAKE_SUBJECT_LABELS[value]}
            for value in MISTAKE_SUBJECTS
        ],
        "status_labels": MISTAKE_STATUS_LABELS,
        "grad_status_values": list(GRAD_STATUS_VALUES),
        "question_types": DEFAULT_QUESTION_TYPES,
        "difficulties": [
            {"value": value} for value in sorted(DIFFICULTY_VALUES)
        ],
        "mistake_reasons": list_mistake_reasons(),
        # 跨页合并组（[{id, members:[{page_no, rect}], direction, primary}]）—— 挂在
        # 顶层而不是某一页上：成员带页号，页 9 的 0.891 与页 10 的 0.039 在不同坐标系
        # 里，只有页号能把它们分开。前端据此标「跨页 N 块」并提供拆分入口。
        "cross_page_merges": analysis.get("cross_page_merges") or [],
        "pages": pages,
        "records": record_items,
    }


@app.delete("/api/mistakes/batches/{batch_id}")
def delete_mistake_batch(batch_id: int, db: Session = Depends(get_db)):
    """删批次：记录级联删除 + 清掉该批次的整个工作目录。"""

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )
    try:
        db.query(MistakeRecord).filter(MistakeRecord.batch_id == batch_id).delete(
            synchronize_session=False
        )
        db.delete(batch)
        db.commit()
    except Exception as e:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": f"删除批次失败: {str(e)}"},
            status_code=500,
        )

    shutil.rmtree(_mistake_batch_dir(batch_id), ignore_errors=True)
    return {"status": "success", "batch_id": batch_id}


# ---- 端点：切题 / 识别 ----


@app.post("/api/mistakes/batches/{batch_id}/cut")
def cut_mistake_batch(
    batch_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """异步切题。

    可选 body：

    - ``page_layouts``：``{"1": "double"}`` 人工指定栏目，覆盖自动判栏；
      ``{"1": {"mode": "double", "boundary": 0.6}}`` 还可带上拖过的分栏线。

    省略即纯自动判栏切块。
    """

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )
    if batch.status == "cutting":
        return JSONResponse(
            content={"status": "error", "message": "该批次正在切题，请等待当前任务结束。"},
            status_code=409,
        )
    if _mistake_source_path(batch_id) is None:
        return JSONResponse(
            content={"status": "error", "message": "批次的源文件已丢失，请重新上传。"},
            status_code=400,
        )

    body = payload if isinstance(payload, dict) else {}

    task_id = f"mistake-cut-{batch_id}-{uuid.uuid4().hex[:8]}"
    # 同步落 "cutting" 状态：异步任务开始前还有排队时间，前端应立即进入等待态
    batch.status = "cutting"
    db.commit()

    try:
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="任务已排队，正在准备切题…",
            document_type="mistake_cut",
            batch_id=batch_id,
            temp_assets=[],
        )
        DOCUMENT_TASKS.init_steps(task_id, MISTAKE_CUT_STEPS)
        DOCUMENT_TASKS.submit(
            task_id,
            run_mistake_cut_task,
            task_id,
            batch_id,
            body.get("page_layouts"),
        )
    except TaskQueueFull as exc:
        DOCUMENT_TASKS.remove(task_id)
        batch.status = "reviewing" if batch.page_count else "pending"
        db.commit()
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=429
        )
    except Exception as exc:
        DOCUMENT_TASKS.remove(task_id)
        batch.status = "cut_failed"
        batch.note = str(exc)[:500]
        db.commit()
        return JSONResponse(
            content={"status": "error", "message": f"切题任务创建失败: {str(exc)}"},
            status_code=500,
        )
    return {"status": "success", "task_id": task_id, "batch_id": batch_id}


def _mistake_recognize_targets(db: Session, batch_id: int, record_ids) -> list[int]:
    """确定要识别的记录：显式传了就按传的，否则默认取本批次已标「错」的题。"""

    explicit: list[int] = []
    for raw in record_ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in explicit:
            explicit.append(value)
    if explicit:
        return explicit
    rows = (
        db.query(MistakeRecord.id)
        .filter(
            MistakeRecord.batch_id == batch_id,
            MistakeRecord.grad_status == "incorrect",
        )
        .order_by(MistakeRecord.page_no.asc(), MistakeRecord.block_index.asc())
        .all()
    )
    return [row[0] for row in rows]


def _mistake_page_write_context(db, batch_id: int, page_no: int):
    """校验「这一页现在可以改」，返回 ``(batch, page_path, entry)``。

    不合法时返回 ``JSONResponse``（调用方直接 ``return`` 即可）。框选与合并两个
    写入口共用同一套前置判断，避免一边放行一边拒绝。
    """

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )
    if batch.status == "cutting":
        return JSONResponse(
            content={
                "status": "error",
                "message": "该批次正在切题，请等待当前任务结束。",
            },
            status_code=409,
        )
    if page_no <= 0:
        return JSONResponse(
            content={"status": "error", "message": "页号不合法。"}, status_code=400
        )

    page_path = _mistake_pages_dir(batch_id) / f"page_{page_no}.png"
    if not page_path.is_file():
        return JSONResponse(
            content={
                "status": "error",
                "message": f"第 {page_no} 页的页图不存在，请先切题。",
            },
            status_code=400,
        )
    try:
        entry = _mistake_page_entry(batch_id, page_no)
    except MistakeAnalysisError as exc:
        # 关键：在读不出元数据时**在动任何数据之前**就退出。放行的话，接下来
        # 「读回 → 改点名键 → 整份写回」会把读到的空壳写进 analysis.json，
        # 整批人工框选/合并/删除名单一次性抹平，且不可恢复。
        return JSONResponse(
            content={
                "status": "error",
                "message": (
                    f"{exc} 本页记录未改动。可先备份该批次目录下的 analysis.json，"
                    "再对该批次执行一次「重新切题」以重建元数据。"
                ),
            },
            status_code=409,
        )
    if entry is None:
        return JSONResponse(
            content={
                "status": "error",
                "message": f"第 {page_no} 页还没有切块结果，请先切题。",
            },
            status_code=400,
        )
    return batch, page_path, entry


@app.post("/api/mistakes/batches/{batch_id}/pages/{page_no}/blocks")
def apply_mistake_page_boxes(
    batch_id: int,
    page_no: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """应用本页框选：写回人工框，并按「自动基线 + 人工框」重建这一页的记录。

    body：``{"boxes": [[x0, y0, x1, y1], …]}``（0–1 归一化，y 向下）。
    空数组＝清除本页人工框，回到纯自动结果。

    为什么不复用 ``/cut``：整批重切要重新渲染全部页图、重算所有页的投影，还得
    清空块图目录重裁一遍 —— 用户只改一页，代价不该按整批算。这里只碰一页。
    """

    context = _mistake_page_write_context(db, batch_id, page_no)
    if isinstance(context, JSONResponse):
        return context
    batch, page_path, entry = context

    body = payload if isinstance(payload, dict) else {}
    boxes = _normalize_mistake_boxes(body.get("boxes"))
    entry["manual_boxes"] = boxes
    try:
        result = _rebuild_mistake_page_records(
            db, batch, batch_id, page_no, page_path, entry
        )
        db.commit()
    except PageSplitError as exc:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=400
        )
    except Exception as exc:  # noqa: BLE001 - 兜底成 500，绝不让半截状态落库
        db.rollback()
        print(f"[Mistake Page Boxes Error] {type(exc).__name__}: {exc}")
        return JSONResponse(
            content={
                "status": "error",
                "message": "应用框选失败，本页记录未改动。",
            },
            status_code=500,
        )

    try:
        _update_mistake_manual_boxes(batch_id, page_no, boxes)
    except (OSError, MistakeAnalysisError) as exc:
        # 记录已经落库且是对的，元数据没写成功只会影响「下次整批重切时保留人工
        # 框」。为这个把请求报成失败，反而让用户以为这次改动没生效。
        # （正常情况下这里到不了：真正的损坏会在上面的写入口前置检查里被拦下。）
        print(f"[Mistake Page Boxes Meta Error] {type(exc).__name__}: {exc}")

    # 顺手同步「已删题块」名单：这次重建里没命中的矩形（几何变了）本来就已经失效，
    # 不同步的话它会一直留在文件里，等哪天几何又撞上就凭空隐藏一道题。
    _sync_mistake_hidden_meta(batch_id, page_no, result)

    return {
        "status": "success",
        "page_no": page_no,
        "box_count": len(boxes),
        "block_count": result["block_count"],
        "removed": result["removed"],
    }


@app.post("/api/mistakes/batches/{batch_id}/pages/{page_no}/merges")
def apply_mistake_page_merges(
    batch_id: int,
    page_no: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """应用本页的**人工合并组**（全量覆盖）：落盘并按新分组重建这一页的记录。

    body：``{"merges": [{"id": "m…", "rects": [[x0,y0,x1,y1], …],
    "direction": "v"|"h", "primary": 0}]}``

    - ``rects`` 即拼接顺序；成员用**几何**定位，重切后按重叠找回，不是块序号。
    - ``direction`` 省略时按成员相对位置自动判定（纵向互相压着 → 左右拼）。
    - ``primary`` 指状态以第几块为准（两块批改状态冲突时由前端面板定）。
    - 全量覆盖：合并、拆分、调顺序、切方向都是「前端构造新的 merges 列表再提交」，
      与人工框一个路子，幂等、无增量状态。
    """

    context = _mistake_page_write_context(db, batch_id, page_no)
    if isinstance(context, JSONResponse):
        return context
    batch, page_path, entry = context

    body = payload if isinstance(payload, dict) else {}
    merges = _normalize_mistake_merges(body.get("merges"))
    entry["manual_merges"] = merges
    try:
        result = _rebuild_mistake_page_records(
            db, batch, batch_id, page_no, page_path, entry
        )
        db.commit()
    except PageSplitError as exc:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=400
        )
    except Exception as exc:  # noqa: BLE001 - 兜底成 500，绝不让半截状态落库
        db.rollback()
        print(f"[Mistake Page Merges Error] {type(exc).__name__}: {exc}")
        return JSONResponse(
            content={
                "status": "error",
                "message": "应用合并失败，本页记录未改动。",
            },
            status_code=500,
        )

    # 落盘的是「成组且方向已确定」的版本：没凑够成员的组不写回，否则下次重建会
    # 反复尝试一个永远组不起来的合并。
    merged_groups = result.get("merges") or []
    try:
        _update_mistake_manual_merges(batch_id, page_no, merged_groups)
    except (OSError, MistakeAnalysisError) as exc:
        print(f"[Mistake Page Merges Meta Error] {type(exc).__name__}: {exc}")

    _sync_mistake_hidden_meta(batch_id, page_no, result)

    return {
        "status": "success",
        "page_no": page_no,
        "merge_count": len(merged_groups),
        "block_count": result["block_count"],
        "removed": result["removed"],
        "manual_merges": merged_groups,
    }


@app.post("/api/mistakes/batches/{batch_id}/cross-merges")
def apply_mistake_cross_page_merges(
    batch_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """应用**跨页合并组**（全量覆盖）：落盘顶层元数据，并重建涉及的每一页。

    body：``{"cross_merges": [{"id": "m…", "primary": 0,
    "members": [{"page_no": 9, "rect": [x0,y0,x1,y1]}, …]}]}``

    - 成员用 ``(页号, 页内矩形)`` 定位。页内矩形是相对比例，跨页不可比 —— 页号是
      必需的，不能靠数值大小猜。
    - 记录只落在 ``primary`` 成员所在页；其余页上的那一半被「占用」，不再单独出记录。
    - 重建范围＝**新旧两个列表**的组员涉及的全部页：删掉一个组也得把它原来占的两页
      重建回来，否则那两页上的记录会一直停在「已合并」的样子。
    """

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )
    if batch.status == "cutting":
        return JSONResponse(
            content={
                "status": "error",
                "message": "该批次正在切题，请等待当前任务结束。",
            },
            status_code=409,
        )

    body = payload if isinstance(payload, dict) else {}
    merges = _normalize_cross_page_merges(body.get("cross_merges"))
    try:
        previous = _normalize_cross_page_merges(
            _read_mistake_analysis(batch_id).get("cross_page_merges")
        )
    except MistakeAnalysisError as exc:
        # 与页级写入口同一理由：读不出元数据时，接下来「整份写回」会把人工框选、
        # 合并、删除名单一次性抹平。宁可失败也不覆盖。
        return JSONResponse(
            content={
                "status": "error",
                "message": f"{exc} 本次改动未执行。",
            },
            status_code=409,
        )

    pages = sorted(
        {
            member["page_no"]
            for group in (list(merges) + list(previous))
            for member in group["members"]
        }
    )
    if not pages:
        return {
            "status": "success",
            "cross_merge_count": 0,
            "dropped": 0,
            "rebuilt_pages": [],
        }

    for page_no in pages:
        page_path = _mistake_pages_dir(batch_id) / f"page_{page_no}.png"
        if not page_path.is_file():
            return JSONResponse(
                content={
                    "status": "error",
                    "message": f"第 {page_no} 页的页图不存在，请先切题。",
                },
                status_code=400,
            )
        try:
            entry = _mistake_page_entry(batch_id, page_no)
        except MistakeAnalysisError as exc:
            return JSONResponse(
                content={"status": "error", "message": str(exc)}, status_code=409
            )
        if entry is None:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": f"第 {page_no} 页还没有切块结果，请先切题。",
                },
                status_code=400,
            )

    try:
        _update_mistake_cross_page_merges(batch_id, merges)
        # 重建之前先把涉及页的块图裁好：跨页拼图要跨页取文件。
        cache: dict = {}
        for page_no in pages:
            blocks = _mistake_page_blocks(batch_id, page_no, cache)
            if blocks:
                _crop_mistake_page_blocks(
                    batch_id,
                    page_no,
                    _mistake_pages_dir(batch_id) / f"page_{page_no}.png",
                    blocks,
                )
        # 成员找不全的组当场丢掉（留一组永远解不开的成员，下次重建还会失败一次）
        survivors = [
            group
            for group in merges
            if _resolve_cross_page_group(batch_id, group, cache) is not None
        ]
        if len(survivors) != len(merges):
            _update_mistake_cross_page_merges(batch_id, survivors)

        for page_no in pages:
            page_path = _mistake_pages_dir(batch_id) / f"page_{page_no}.png"
            entry = _mistake_page_entry(batch_id, page_no)
            if entry is None:
                continue
            result = _rebuild_mistake_page_records(
                db, batch, batch_id, page_no, page_path, entry
            )
            _sync_mistake_hidden_meta(batch_id, page_no, result)
        db.commit()
    except PageSplitError as exc:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=400
        )
    except Exception as exc:  # noqa: BLE001 - 兜底成 500，绝不让半截状态落库
        db.rollback()
        print(f"[Mistake Cross Page Merge Error] {type(exc).__name__}: {exc}")
        return JSONResponse(
            content={
                "status": "error",
                "message": "应用跨页合并失败，本次改动未落库。",
            },
            status_code=500,
        )

    return {
        "status": "success",
        "cross_merge_count": len(survivors),
        "dropped": len(merges) - len(survivors),
        "rebuilt_pages": pages,
    }


@app.post("/api/mistakes/batches/{batch_id}/pages/{page_no}/hidden")
def apply_mistake_page_hidden(
    batch_id: int,
    page_no: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """删除（隐藏）本页若干题块 —— 全量覆盖式，传空数组即「全部恢复」。

    body：``{"rects": [[x0, y0, x1, y1], …]}``（0–1 归一化，y 向下）。

    - 「删除」＝把这块从本页的渲染项里剔掉、连数据库记录一起不建：一次画框/合并都会
      整页重建，所以只删记录必然复活，必须落盘到 ``analysis.json`` 的
      ``hidden_blocks``。
    - 矩形按**外接矩形**比对，因此「删掉一道已合并的题」＝名单里那一个矩形，不用逐
      成员去点。
    - 恢复＝传空数组。注意被删期间那条记录的状态（判定/错因/解析）随记录一起没了，
      恢复回来是一道全新的未批题 —— 想留住状态就先别删。
    """

    context = _mistake_page_write_context(db, batch_id, page_no)
    if isinstance(context, JSONResponse):
        return context
    batch, page_path, entry = context

    body = payload if isinstance(payload, dict) else {}
    rects = _normalize_mistake_hidden(body.get("rects"))
    entry["hidden_blocks"] = rects
    try:
        result = _rebuild_mistake_page_records(
            db, batch, batch_id, page_no, page_path, entry
        )
        db.commit()
    except PageSplitError as exc:
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=400
        )
    except Exception as exc:  # noqa: BLE001 - 兜底成 500，绝不让半截状态落库
        db.rollback()
        print(f"[Mistake Page Hidden Error] {type(exc).__name__}: {exc}")
        return JSONResponse(
            content={
                "status": "error",
                "message": "删除失败，本页记录未改动。",
            },
            status_code=500,
        )

    # 只把**真正命中**的矩形写回：没命中的（比如重切后几何全变了）留着只会在每次
    # 重建时白跑一遍比对，还会让「已隐藏 N 块」这个数字对不上实际。
    matched = result.get("hidden_blocks") or []
    try:
        _update_mistake_hidden_blocks(batch_id, page_no, matched)
    except (OSError, MistakeAnalysisError) as exc:
        # 同上：记录已提交，元数据没落盘只影响「下次重切时保留删除名单」。
        print(f"[Mistake Page Hidden Meta Error] {type(exc).__name__}: {exc}")

    return {
        "status": "success",
        "page_no": page_no,
        "hidden_count": len(matched),
        "block_count": result["block_count"],
        "removed": result["removed"],
        "hidden_blocks": matched,
    }


@app.post("/api/mistakes/batches/{batch_id}/pages/{page_no}/crop-figure")
def crop_mistake_page_figure(
    batch_id: int,
    page_no: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """按前端框选的归一化坐标，从原卷页面上裁一张图。

    「补图形 / 补解析截图」用：用户要补的图形本来就在原卷页面上，让人先去别处截成
    文件再上传是绕路。坐标是 0–1（y 向下），与切块、人工框选同一套 —— 前端按显示
    尺寸换算，服务端不碰像素。

    产物落在批次目录的 ``figures/``：块图目录会在重切时被整体重置
    （``_reset_directory(_mistake_blocks_dir(batch_id))``），人工补的图放那里会被清掉。

    返回**裸** web 路径（不带 ``?v=``）：文件名带随机后缀，不存在同名覆盖，
    也就不需要指纹（这一条很重要，见 .workbuddy/memory/2026-09-15.md 第六轮）。
    """

    import math

    context = _mistake_page_write_context(db, batch_id, page_no)
    if isinstance(context, JSONResponse):
        return context
    _batch, page_path, _entry = context

    body = payload if isinstance(payload, dict) else {}
    try:
        xmin = float(body.get("xmin"))
        ymin = float(body.get("ymin"))
        xmax = float(body.get("xmax"))
        ymax = float(body.get("ymax"))
    except (TypeError, ValueError):
        return JSONResponse(
            content={"status": "error", "message": "框选坐标不合法。"},
            status_code=400,
        )
    if not all(math.isfinite(value) for value in (xmin, ymin, xmax, ymax)):
        return JSONResponse(
            content={"status": "error", "message": "框选坐标必须是有限数值。"},
            status_code=400,
        )
    if not (0.0 <= xmin < xmax <= 1.0 and 0.0 <= ymin < ymax <= 1.0):
        return JSONResponse(
            content={
                "status": "error",
                "message": "框选区域必须落在页面内，且不能为空。",
            },
            status_code=400,
        )
    if (xmax - xmin) < 0.01 or (ymax - ymin) < 0.01:
        return JSONResponse(
            content={"status": "error", "message": "框选区域太小了，请重新框选。"},
            status_code=400,
        )

    figures_dir = _mistake_batch_dir(batch_id) / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    crop_name = f"crop_p{page_no:03d}_{uuid.uuid4().hex[:10]}.png"
    try:
        crop_region(
            page_path,
            figures_dir / crop_name,
            y_start=ymin,
            y_end=ymax,
            x_start=xmin,
            x_end=xmax,
        )
    except Exception as exc:  # noqa: BLE001 - 裁图失败要把原因原样告诉用户
        return JSONResponse(
            content={"status": "error", "message": f"截取失败：{exc}"},
            status_code=500,
        )

    with Image.open(figures_dir / crop_name) as image:
        width, height = image.size
    return {
        "status": "success",
        "image_path": _mistake_web_url(batch_id, "figures", crop_name),
        "width": width,
        "height": height,
    }


@app.post("/api/mistakes/batches/{batch_id}/recognize")
def recognize_mistake_batch(
    batch_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """异步识别本批次的错题（不传 ``record_ids`` 时默认取 ``grad_status = incorrect``）。"""

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )

    body = payload if isinstance(payload, dict) else {}
    target_ids = _mistake_recognize_targets(db, batch_id, body.get("record_ids"))
    if not target_ids:
        return JSONResponse(
            content={
                "status": "error",
                "message": "没有待识别的题目：请先在点选区标记错题，或显式传入 record_ids。",
            },
            status_code=400,
        )

    engine = str(body.get("engine") or "").strip()
    task_id = f"mistake-recognize-{batch_id}-{uuid.uuid4().hex[:8]}"
    try:
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="任务已排队，正在准备识别…",
            document_type="mistake_recognize",
            batch_id=batch_id,
            record_count=len(target_ids),
            temp_assets=[],
        )
        DOCUMENT_TASKS.init_steps(task_id, MISTAKE_RECOGNIZE_STEPS)
        DOCUMENT_TASKS.submit(
            task_id, run_mistake_recognize_task, task_id, batch_id, target_ids, engine
        )
    except TaskQueueFull as exc:
        DOCUMENT_TASKS.remove(task_id)
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=429
        )
    except Exception as exc:
        DOCUMENT_TASKS.remove(task_id)
        return JSONResponse(
            content={"status": "error", "message": f"识别任务创建失败: {str(exc)}"},
            status_code=500,
        )
    return {
        "status": "success",
        "task_id": task_id,
        "batch_id": batch_id,
        "record_count": len(target_ids),
    }


@app.post("/api/mistakes/records/{record_id}/recognize")
def recognize_mistake_record(
    record_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """单题识别 / 重试（对应卡片上的单题按钮），走同一个异步任务。"""

    record = db.query(MistakeRecord).filter(MistakeRecord.id == record_id).first()
    if record is None:
        return JSONResponse(
            content={"status": "error", "message": "题目记录不存在。"}, status_code=404
        )
    engine = ""
    if isinstance(payload, dict):
        engine = str(payload.get("engine") or "").strip()

    task_id = f"mistake-recognize-{record.batch_id}-{uuid.uuid4().hex[:8]}"
    try:
        DOCUMENT_TASKS.create(
            task_id,
            status="pending",
            log="任务已排队，正在准备识别…",
            document_type="mistake_recognize",
            batch_id=record.batch_id,
            record_count=1,
            temp_assets=[],
        )
        DOCUMENT_TASKS.init_steps(task_id, MISTAKE_RECOGNIZE_STEPS)
        DOCUMENT_TASKS.submit(
            task_id,
            run_mistake_recognize_task,
            task_id,
            record.batch_id,
            [record_id],
            engine,
        )
    except TaskQueueFull as exc:
        DOCUMENT_TASKS.remove(task_id)
        return JSONResponse(
            content={"status": "error", "message": str(exc)}, status_code=429
        )
    except Exception as exc:
        DOCUMENT_TASKS.remove(task_id)
        return JSONResponse(
            content={"status": "error", "message": f"识别任务创建失败: {str(exc)}"},
            status_code=500,
        )
    return {"status": "success", "task_id": task_id, "record_id": record_id}


# ---- 端点：更新单条记录 ----


@app.put("/api/mistakes/records/{record_id}")
def update_mistake_record(
    record_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    """更新单条记录（题面编辑、对错点选、错因、收录开关、解析、图形）。

    点选语义：``grad_status`` 改动时，若本次没有显式给 ``include_in_handout``，
    收录开关跟随对错（错 → 收录）。已识别过的题面**不回滚** —— 改对错只是改判对错，
    不该把已经识别好的题面清掉（实施计划 §8.2）。
    """

    record = db.query(MistakeRecord).filter(MistakeRecord.id == record_id).first()
    if record is None:
        return JSONResponse(
            content={"status": "error", "message": "题目记录不存在。"}, status_code=404
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            content={"status": "error", "message": "请求体必须是 JSON 对象。"},
            status_code=400,
        )

    try:
        if "question_no" in payload:
            record.question_no = str(payload.get("question_no") or "").strip()[:50]
        if "content" in payload:
            content = str(payload.get("content") or "")
            if content.strip():
                content = normalize_fillin_macro(content)
                content = normalize_choice_options_to_latex(content)
                content = _strip_leading_question_number(content)
            record.content = content
        if "question_type" in payload:
            value = str(payload.get("question_type") or "").strip()
            if value in MISTAKE_QUESTION_TYPES:
                record.question_type = value
        if "difficulty" in payload:
            record.difficulty = normalize_difficulty(
                payload.get("difficulty"), default=record.difficulty or "normal"
            )
        if "knowledge_tags" in payload:
            record.knowledge_tags = normalize_tag_list(
                payload.get("knowledge_tags"), field="knowledge_list"
            )
        if "solve_method" in payload:
            record.solve_method = normalize_tag_list(
                payload.get("solve_method"), field="solve_method"
            )
        # 分类信息（审校页中栏，与题库录入表单同款）。来源/学段/章节/小节/关联章节/
        # 自定义标签在 v1010 之前没有落脚点，只能丢掉；现在原样收下，入库时搬进题库。
        if "source" in payload:
            record.source = str(payload.get("source") or "").strip()[:200]
        if "category_compulsory" in payload:
            record.category_compulsory = str(payload.get("category_compulsory") or "").strip()[:100]
        if "category_chapter" in payload:
            record.category_chapter = str(payload.get("category_chapter") or "").strip()[:100]
        if "category_knowledge" in payload:
            record.category_knowledge = str(payload.get("category_knowledge") or "").strip()[:100]
        if "related_curriculums" in payload:
            record.related_curriculums = parse_related_curriculums(
                payload.get("related_curriculums")
            )
        if "tags" in payload:
            # 与题库录入/更新保持一致：自定义标签按原文存（只去首尾空白），
            # 这里**不**走 normalize_tag_list —— 那会把逗号分隔改成半角并去重，
            # 两个入口对同一个字段就会存出两种格式。
            record.tags = str(payload.get("tags") or "").strip()
        if "error_reason" in payload:
            record.error_reason = normalize_mistake_reason_list(
                payload.get("error_reason")
            )
        if "mastery_status" in payload:
            mastery = str(payload.get("mastery_status") or "").strip()
            if mastery in ("pending", "redone", "mastered"):
                record.mastery_status = mastery
        if "answer_markdown" in payload:
            record.answer_markdown = str(payload.get("answer_markdown") or "")
        if "answer_source" in payload:
            source = str(payload.get("answer_source") or "none").strip().lower()
            record.answer_source = source if source in ("none", "manual", "ai") else "none"
        if "answer_reviewed" in payload:
            record.answer_reviewed = bool(payload.get("answer_reviewed"))
        if "answer_images" in payload:
            record.answer_images = normalize_upload_asset_references(
                payload.get("answer_images") or [],
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
        if "figure_images" in payload:
            record.figure_images = normalize_upload_asset_references(
                payload.get("figure_images") or [],
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
        if "image_block" in payload:
            record.image_block = str(payload.get("image_block") or "")
        for key in ("block_y_start", "block_y_end"):
            if key in payload:
                try:
                    setattr(record, key, max(0.0, min(1.0, float(payload.get(key)))))
                except (TypeError, ValueError):
                    pass

        grad_changed = False
        if "grad_status" in payload:
            grad = str(payload.get("grad_status") or "").strip()
            if grad not in GRAD_STATUS_VALUES:
                return JSONResponse(
                    content={
                        "status": "error",
                        "message": f"对错状态只能是 {'/'.join(GRAD_STATUS_VALUES)}。",
                    },
                    status_code=400,
                )
            if grad != record.grad_status:
                grad_changed = True
            record.grad_status = grad

        if "include_in_handout" in payload:
            record.include_in_handout = bool(payload.get("include_in_handout"))
        elif grad_changed:
            record.include_in_handout = record.grad_status == "incorrect"

        db.commit()
        db.refresh(record)
    except Exception as e:
        db.rollback()
        print(f"[Mistake Record Update Error] {type(e).__name__}: {e}")
        return JSONResponse(
            content={"status": "error", "message": f"更新题目失败: {str(e)}"},
            status_code=500,
        )

    item = _mistake_record_client_payload(record, record.batch_id)
    stats = _mistake_record_stats(
        db.query(MistakeRecord).filter(MistakeRecord.batch_id == record.batch_id).all()
    )
    return {"status": "success", "record": item, "stats": stats}


# ---- 端点：入库 ----


@app.post("/api/mistakes/records/{record_id}/import-to-bank")
def import_mistake_record_to_bank(
    record_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """单题入库。body 可带 ``{"force": true}`` 跳过查重。"""

    record = db.query(MistakeRecord).filter(MistakeRecord.id == record_id).first()
    if record is None:
        return JSONResponse(
            content={"status": "error", "message": "题目记录不存在。"}, status_code=404
        )
    force = bool((payload or {}).get("force")) if isinstance(payload, dict) else False
    try:
        result = _import_mistake_record_to_bank(db, record, force=force)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Mistake Import Error] record={record_id}: {type(e).__name__}: {e}")
        return JSONResponse(
            content={"status": "error", "message": f"入库失败: {str(e)}"},
            status_code=500,
        )
    if result.get("status") == "duplicate":
        return JSONResponse(content=result, status_code=409)
    return {"status": "success", "result": result}


@app.post("/api/mistakes/batches/{batch_id}/import-to-bank")
def import_mistake_batch_to_bank(
    batch_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """批量入库：默认全部已识别、尚未入库的题；撞车的逐题回传，不静默丢弃。"""

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )
    body = payload if isinstance(payload, dict) else {}
    force = bool(body.get("force"))

    explicit: list[int] = []
    for raw in body.get("record_ids") or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in explicit:
            explicit.append(value)

    query = db.query(MistakeRecord).filter(MistakeRecord.batch_id == batch_id)
    if explicit:
        query = query.filter(MistakeRecord.id.in_(explicit))
    else:
        # 默认口径：本批次已识别、尚未入库的记录。**不在这里按学科过滤** ——
        # 三科现在都入库，逐题逻辑负责给出「为什么没进」的原因，而不是在 SQL 层
        # 悄悄筛掉、让前端收到一个全是 0 的响应。
        query = query.filter(
            MistakeRecord.recognize_status == "done",
            MistakeRecord.question_id.is_(None),
        )
    records = (
        query.order_by(MistakeRecord.page_no.asc(), MistakeRecord.block_index.asc())
        .all()
    )

    imported: list[dict] = []
    # 前端据此把刚入库的题直接放进组卷试题篮（并给解答题一个合理默认分值）。
    imported_items: list[dict] = []
    duplicates: list[dict] = []
    skipped: list[dict] = []
    already: list[dict] = []
    for record in records:
        try:
            result = _import_mistake_record_to_bank(db, record, force=force)
        except Exception as exc:  # noqa: BLE001 - 单题失败不该中断整批
            db.rollback()
            skipped.append(
                {
                    "record_id": record.id,
                    "question_no": record.question_no or "",
                    "message": f"入库失败: {exc}",
                }
            )
            continue
        status = result.get("status")
        if status == "imported":
            imported.append(result)
            imported_items.append(
                {
                    "question_id": result.get("question_id"),
                    "question_type": record.question_type or "detailed_answer",
                }
            )
        elif status == "duplicate":
            duplicates.append(result)
        elif status == "already":
            already.append(result)
        else:
            skipped.append(result)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return JSONResponse(
            content={"status": "error", "message": f"批量入库提交失败: {exc}"},
            status_code=500,
        )

    stats = _mistake_record_stats(
        db.query(MistakeRecord).filter(MistakeRecord.batch_id == batch_id).all()
    )
    return {
        "status": "success",
        "batch_id": batch_id,
        "imported": len(imported),
        "imported_items": imported_items,
        "duplicates": duplicates,
        "skipped": skipped,
        "already": len(already),
        "stats": stats,
    }


# ---- 端点：错题本导出 ----


@app.post("/api/mistakes/batches/{batch_id}/export")
def export_mistake_handout(
    batch_id: int,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """生成错题本 PDF。

    只收录 ``include_in_handout = True`` 的记录（默认随「错」标记，但人工可改），
    AI 生成的解析必须人工核对过才进 PDF —— 错题本里出现错误解析会直接误导学生。
    """

    batch = db.query(MistakeBatch).filter(MistakeBatch.id == batch_id).first()
    if batch is None:
        return JSONResponse(
            content={"status": "error", "message": "批次不存在。"}, status_code=404
        )
    body = payload if isinstance(payload, dict) else {}

    options = HandoutOptions(
        solution_space_cm=body.get("solution_space_cm", 6.0),
        include_reason=body.get("include_reason", True),
        include_figures=body.get("include_figures", True),
        answer_mode=body.get("answer_mode", "none"),
        font_size=body.get("font_size", "3"),
        show_original_number=body.get("show_original_number", True),
    ).normalized()

    explicit: list[int] = []
    for raw in body.get("record_ids") or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in explicit:
            explicit.append(value)

    query = db.query(MistakeRecord).filter(MistakeRecord.batch_id == batch_id)
    if explicit:
        query = query.filter(MistakeRecord.id.in_(explicit))
    else:
        query = query.filter(MistakeRecord.include_in_handout.is_(True))
    records = (
        query.order_by(MistakeRecord.page_no.asc(), MistakeRecord.block_index.asc())
        .all()
    )
    if not records:
        return JSONResponse(
            content={
                "status": "error",
                "message": "本批次没有需要收录的题目：请先在点选区把错题标记为「进错题本」。",
            },
            status_code=400,
        )

    # 导出前重排题号：错题本按「第 1..N 题」顺排，原卷题号走 question_no 快照展示
    record_dicts = []
    for record in records:
        item = record.to_dict()
        record_dicts.append(item)

    student = (
        db.query(Student).filter(Student.id == batch.student_id).first()
        if batch.student_id
        else None
    )
    try:
        built = build_mistake_handout_latex(
            title=batch.title or "错题本",
            subject=batch.subject or "math",
            student_name=student.name if student else "",
            batch_date=batch.batch_date.isoformat() if batch.batch_date else "",
            records=record_dicts,
            options=options,
            static_dir=STATIC_DIR,
        )
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": f"装配错题本 LaTeX 失败: {str(e)}"},
            status_code=500,
        )

    exports_dir = _mistake_exports_dir(batch_id)
    exports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_name = f"mistakes_batch{batch_id}_{timestamp}.pdf"
    tex_name = f"mistakes_batch{batch_id}_{timestamp}.tex"
    pdf_path = exports_dir / pdf_name
    try:
        write_private_text_atomic(exports_dir / tex_name, built.tex_content)
    except OSError as exc:
        print(f"[Mistake Export Warning] 保存 TeX 源码失败: {exc}")

    pdf_bytes, log_or_error = compile_tex_to_pdf(built.tex_content, built.image_paths)
    if not pdf_bytes:
        if is_xelatex_missing(log_or_error or ""):
            diagnostic = build_local_latex_diagnostic(log_or_error or "", built.tex_content)
        else:
            diagnostic = explain_latex_compile_error(log_or_error, built.tex_content)
        diagnostic.setdefault("tex_source", built.tex_content)
        diagnostic.setdefault("full_log", (log_or_error or "")[:6000])
        return JSONResponse(
            content={
                "status": "error",
                "message": diagnostic.get("summary", "错题本 PDF 编译失败"),
                "diagnostic": diagnostic,
            },
            status_code=400,
        )

    try:
        pdf_path.write_bytes(pdf_bytes)
        harden_private_path(pdf_path)
    except OSError as exc:
        return JSONResponse(
            content={"status": "error", "message": f"保存错题本 PDF 失败: {exc}"},
            status_code=500,
        )

    try:
        batch.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 状态落库失败不该丢掉已生成的 PDF
        db.rollback()
        print(f"[Mistake Export Warning] 批次状态更新失败: {exc}")

    return {
        "status": "success",
        "batch_id": batch_id,
        "filename": pdf_name,
        "download_name": f"{batch.title or '错题本'}.pdf",
        "pdf_url": _mistake_web_url(batch_id, "exports", pdf_name),
        "tex_url": _mistake_web_url(batch_id, "exports", tex_name),
        "pdf_size": len(pdf_bytes),
        "included_count": built.included_count,
        "answer_count": built.answer_count,
        "blocked_ai_answers": built.blocked_ai_answers,
        "options": {
            "solution_space_cm": options.solution_space_cm,
            "include_reason": options.include_reason,
            "include_figures": options.include_figures,
            "answer_mode": options.answer_mode,
            "font_size": options.font_size,
            "show_original_number": options.show_original_number,
        },
    }


@app.get("/api/mistakes/batches/{batch_id}/export/download")
def download_mistake_handout(batch_id: int, filename: Optional[str] = None):
    """下载导出产物；不给 ``filename`` 就取最近生成的那份。"""

    exports_dir = _mistake_exports_dir(batch_id)
    target: Optional[Path] = None
    if filename:
        candidate = (exports_dir / os.path.basename(str(filename))).resolve()
        try:
            candidate.relative_to(exports_dir.resolve())
        except ValueError:
            return JSONResponse(
                content={"status": "error", "message": "非法的文件名。"}, status_code=400
            )
        if candidate.is_file():
            target = candidate
    if target is None:
        try:
            candidates = sorted(
                (path for path in exports_dir.glob("*.pdf") if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            candidates = []
        if candidates:
            target = candidates[0]
    if target is None:
        return JSONResponse(
            content={"status": "error", "message": "还没有生成错题本 PDF。"},
            status_code=404,
        )

    return FileResponse(
        str(target),
        media_type="application/pdf",
        filename=target.name,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ----------------- Disable browser cache for static assets -----------------
# 前端 JS/HTML/CSS 频繁改动，默认 StaticFiles 会让浏览器长期缓存，
# 导致每次改完都得强制刷新。这里统一给 /static 响应加 no-cache 头。
@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ----------------- Mount Static Folder last to allow API override -----------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
