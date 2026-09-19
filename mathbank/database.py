import datetime
import sqlite3
import json
import re
from pathlib import Path
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import declarative_base, sessionmaker
from mathbank.paths import DATABASE_FILE, sqlite_url

# SQLite Database URL
SQLALCHEMY_DATABASE_URL = sqlite_url(DATABASE_FILE)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    # 改用 QueuePool 多连接：每个线程/请求获取独立连接，彻底避免单连接被多线程
    # 交替使用造成的 sqlite3 InterfaceError；SQLite 文件锁 + busy_timeout(5000)
    # 自行串行化写操作。pool 容量放大以容纳批量导入与轮询并发，避免耗尽。
    poolclass=QueuePool,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):
    """Apply relational safety settings to every SQLite connection."""

    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _utcnow_naive():
    """Return UTC without tzinfo for the existing SQLite DateTime columns."""

    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def configure_sqlite_wal(database_engine: Engine) -> str | None:
    """Enable and verify WAL mode for a persistent SQLite database."""

    database_name = database_engine.url.database
    if not database_name or database_name == ":memory:":
        return None
    with database_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        mode = str(
            connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
        ).lower()
        if mode != "wal":
            raise RuntimeError(f"SQLite WAL 模式启用失败，当前模式: {mode}")
        connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
        connection.exec_driver_sql("PRAGMA wal_autocheckpoint=1000")
    try:
        Path(database_name).resolve().chmod(0o600)
    except OSError:
        pass
    return mode

def _safe_json_list(value):
    """Parse a JSON-encoded list stored as TEXT; return [] on any failure."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def normalize_question_content(content: str) -> str:
    """将题干归一化为查重指纹：去题号前缀、去所有空白、简化 LaTeX 等价差异。

    这是查重指纹的唯一权威实现。入库前查重（create_question）与迁移回填
    共用同一份逻辑，避免多处各写一份导致指纹不一致。
    """
    if not content or not isinstance(content, str):
        return ""
    text = content
    # 剥离图片 markdown（![alt](url)）：OCR 每次导入都会随机生成 uuid/hash，
    # 若不剔除会污染指纹，使带图题目二次入库时漏报重复（指纹被随机串稀释，
    # 相似度被拉低到 0.92 阈值以下）。归一化只比数学文本，图片引用不参与。
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    # 去掉开头的题号（如 "1." "（1）" "一、" "1、" 等）
    text = re.sub(r"^\s*[\d一二三四五六七八九十]+[\.、\)）\s]+", "", text)
    text = re.sub(r"^\s*\([\d]+\)\s*", "", text)
    # 去掉所有空白（含中文全角空格）
    text = re.sub(r"\s+", "", text)
    # 简化常见 LaTeX 等价写法差异
    text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", text)
    text = re.sub(r"\\times|\\cdot", "*", text)
    text = text.replace("\\", "")
    text = text.lower()
    return text


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String(50), default="math", index=True)  # math / physics / chemistry
    # 题目来源渠道：bank（题库工作台录入或导入）/ mistake（错题工作台入库）。
    # 用户要求错题与题库自录题可区分，故单列一个字段而不是塞进 tags 自由文本。
    origin = Column(String(20), default="bank", index=True)
    content = Column(Text, nullable=False)  # 题干 (LaTeX + markdown)
    content_fingerprint = Column(String, nullable=True)  # 查重归一化指纹（normalize_question_content 结果）
    question_type = Column(String(50), default="single_choice", index=True)  # single_choice, multi_choice, fill_in_blank, detailed_answer
    category_compulsory = Column(String(100), default="", index=True)  # 必修/选修/选择性必修
    category_chapter = Column(String(100), default="", index=True)  # 章节
    category_knowledge = Column(String(100), default="", index=True)  # 知识点
    difficulty = Column(String(50), default="normal", index=True)  # easy_error, normal, challenge, qiangji
    source = Column(String(200), default="")  # 来源
    answer_markdown = Column(Text, default="")  # 答案与解析 (LaTeX + markdown)
    review = Column(Text, default="")  # 评述 (允许空白)
    association_group_id = Column(String(100), default="", index=True)  # 关联题目分组ID (支持传递关系)
    _image_paths = Column(Text, default="[]", name="image_paths")  # 以JSON字符串形式存储相对路径列表
    tikz_code = Column(Text, default="")  # TikZ 几何绘图源代码
    figure_align = Column(String(50), default="right")  # 插图排版位置: right (题干右侧), center (下方居中), bottom_right (下方居右)
    tags = Column(Text, default="")  # 自定义标签 (逗号分隔或字符串)
    knowledge_list = Column(Text, default="")  # 知识点多标签 (逗号分隔，AI 自动打标 + 手动修正)
    solve_method = Column(Text, default="")  # 解题方法多标签 (逗号分隔，AI 自动打标 + 手动修正)
    related_curriculums = Column(Text, default="[]")  # 关联章节(JSON数组: [{compulsory,chapter,knowledge}])，融合题多章节归属
    usage_count = Column(Integer, default=0, index=True)  # 组卷引用次数
    created_at = Column(DateTime, default=_utcnow_naive)

    @property
    def image_paths(self):
        try:
            value = json.loads(self._image_paths)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @image_paths.setter
    def image_paths(self, value):
        if isinstance(value, list):
            self._image_paths = json.dumps(value)
        else:
            self._image_paths = "[]"

    def to_dict(self):
        return {
            "id": self.id,
            "subject": self.subject or "math",
            "origin": self.origin or "bank",
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "difficulty": self.difficulty,
            "source": self.source,
            "answer_markdown": self.answer_markdown,
            "has_answer": bool((self.answer_markdown or "").strip()),
            "review": self.review,
            "association_group_id": self.association_group_id,
            "image_paths": self.image_paths,
            "tikz_code": self.tikz_code,
            "figure_align": self.figure_align or "right",
            "tags": self.tags,
            "knowledge_list": self.knowledge_list or "",
            "solve_method": self.solve_method or "",
            "related_curriculums": _safe_json_list(self.related_curriculums),
            "usage_count": self.usage_count or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

    def to_summary_dict(self):
        return {
            "id": self.id,
            "subject": self.subject or "math",
            "origin": self.origin or "bank",
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "difficulty": self.difficulty,
            "source": self.source,
            "has_answer": bool((self.answer_markdown or "").strip()),
            "association_group_id": self.association_group_id,
            "image_paths": self.image_paths,
            "tikz_code": self.tikz_code,
            "figure_align": self.figure_align or "right",
            "tags": self.tags,
            "knowledge_list": self.knowledge_list or "",
            "solve_method": self.solve_method or "",
            "related_curriculums": _safe_json_list(self.related_curriculums),
            "usage_count": self.usage_count or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

class QuestionCurriculum(Base):
    __tablename__ = "question_curriculums"

    __table_args__ = (
        UniqueConstraint(
            "question_id",
            "version_code",
            name="uq_question_curriculum_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_code = Column(String(50), index=True, nullable=False)  # 'A', 'B', 'S'
    compulsory = Column(String(100), default="", index=True)
    chapter = Column(String(100), default="", index=True)
    knowledge = Column(String(100), default="", index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "question_id": self.question_id,
            "version_code": self.version_code,
            "compulsory": self.compulsory,
            "chapter": self.chapter,
            "knowledge": self.knowledge
        }

class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    subtitle = Column(String(200), default="")
    paper_type = Column(String(50), default="exam")  # exam, quiz, handout
    total_score = Column(Integer, default=150)
    metadata_json = Column(Text, default="{}")
    is_template = Column(Integer, default=0)  # 1 = 个人预设模板
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        meta = {}
        try:
            parsed_meta = json.loads(self.metadata_json or "{}")
            meta = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            meta = {}
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "paper_type": self.paper_type,
            "total_score": self.total_score,
            "show_secret": meta.get("show_secret", True),
            "show_notice": meta.get("show_notice", True),
            # 抬头学科行（可手填，空串 = 不显示）与考试用时都随 metadata_json 走，
            # 不单独加列 —— 它们纯属展示参数，不值得为它们做一次库迁移。
            "subject_line": str(meta.get("subject_line") or ""),
            "exam_duration": meta.get("exam_duration", 120),
            "is_template": bool(self.is_template),
            "metadata_json": self.metadata_json,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

class PaperQuestion(Base):
    __tablename__ = "paper_questions"

    __table_args__ = (
        UniqueConstraint("paper_id", "order_index", name="uq_paper_question_order"),
        CheckConstraint("score >= 0", name="ck_paper_question_score_nonnegative"),
    )

    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(
        Integer,
        ForeignKey("papers.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    order_index = Column(Integer, default=0)
    score = Column(Integer, default=5)

    def to_dict(self):
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "question_id": self.question_id,
            "order_index": self.order_index,
            "score": self.score
        }

# ----------------- 错题扫描（学生 / 批次 / 题目记录） -----------------
# 与题库解耦：错题是「某个学生某次做错的记录」，题库题是「可复用的备课素材」。
# 同一道题会被不同学生错、题库题也会被编辑或删除，因此错题本导出只读快照字段，
# 不 join questions，避免题库增删让已出的错题本变形。


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    grade = Column(String(50), default="")  # 学段，如「高一」
    note = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "grade": self.grade or "",
            "note": self.note or "",
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
        }


class MistakeBatch(Base):
    """一次扫描 = 一个批次（可为一周的多页 PDF、单页或单题）。"""

    __tablename__ = "mistake_batches"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subject = Column(String(50), default="math", index=True)  # math / physics / chemistry / other
    title = Column(String(200), nullable=False, default="")
    batch_date = Column(Date, nullable=True)
    source_name = Column(String(200), default="")  # 原始扫描文件名
    page_count = Column(Integer, default=0)
    # pending（已建批次）/ cutting / cutting_failed / reviewing（待点选）/ done
    status = Column(String(50), default="pending", index=True)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    def to_dict(self, *, stats: dict | None = None):
        def _date_str(value):
            if value is None:
                return None
            return value.isoformat() if hasattr(value, "isoformat") else str(value)

        payload = {
            "id": self.id,
            "student_id": self.student_id,
            "subject": self.subject or "math",
            "title": self.title or "",
            "batch_date": _date_str(self.batch_date),
            "source_name": self.source_name or "",
            "page_count": self.page_count or 0,
            "status": self.status or "pending",
            "note": self.note or "",
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
            "updated_at": (self.updated_at.isoformat() + "Z") if self.updated_at else None,
        }
        if stats:
            payload.update(stats)
        return payload


class MistakeRecord(Base):
    """批次内的**全部题目**记录（不只错题）。

    粒度差别：每道题都有记录（页号 / 题块位置 / 三态 / 切块图），但只有被点选为
    「错」的题会识别题面（``recognize_status = done``、``content`` 有值）。对 / 未批
    的题 ``recognize_status = skipped``、``content`` 留空，属于留档记录，用于对错
    分布与正确率统计，不进题库、不被题库检索。
    """

    __tablename__ = "mistake_records"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(
        Integer,
        ForeignKey("mistake_batches.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    student_id = Column(Integer, nullable=True, index=True)  # 冗余，便于按学生聚合
    subject = Column(String(50), default="math")  # 冗余
    page_no = Column(Integer, default=1)  # 所在页（1 起）
    question_no = Column(String(50), default="")  # 题号原文，如「7」「12(2)」
    block_index = Column(Integer, default=0)  # 页内题块序号（0 起）
    block_y_start = Column(Float, default=0.0)  # 题块在页面的纵向占比 0-1
    block_y_end = Column(Float, default=1.0)
    # 题块在页面上的横向范围。单栏页面恒为 0/1；双栏页面上左栏块是 [0, 分栏线]、
    # 右栏块是 [分栏线, 1]。没有这两列时块图会被裁成整页宽 —— 双栏卷上那意味着
    # 一张图里左栏半道题 + 右栏半道题拼在一起。
    block_x_start = Column(Float, default=0.0)
    block_x_end = Column(Float, default=1.0)
    # 所属栏：0=整页/通栏，1=左栏，2=右栏
    column_index = Column(Integer, default=0)
    content = Column(Text, default="")  # 干净题面（LaTeX + markdown）
    answer_markdown = Column(Text, default="")  # 解析（可空）
    question_type = Column(String(50), default="detailed_answer")
    difficulty = Column(String(50), default="normal")
    knowledge_tags = Column(Text, default="")  # 逗号分隔；物化用自由文本
    solve_method = Column(Text, default="")  # 解题方法（数学）
    # ---- 分类信息（审校页可编辑，入库时带进题库）----
    # 用户 2026-09-15 定：审校页中栏做成与题库录入表单同款的「分类信息」。
    # 这几列原本只存在于 questions 表，错题记录里没有落脚点，入库时被写死成空字符串，
    # 于是错题入题库后学段/章节/小节全空、筛选里根本找不到。命名与 questions 表
    # 逐字对齐，入库映射就是直接搬运，不做二次翻译。
    source = Column(String(200), default="")  # 来源（试卷/出处）；与 snapshot_source（批次文件名快照）区分
    category_compulsory = Column(String(100), default="")  # 学段
    category_chapter = Column(String(100), default="")  # 章节
    category_knowledge = Column(String(100), default="")  # 小节
    related_curriculums = Column(Text, default="[]")  # 关联章节(JSON: [{compulsory,chapter,knowledge}])
    tags = Column(Text, default="")  # 自定义标签
    # 人工点选结果：correct / incorrect / unknown（默认 unknown，避免漏点即算对）
    grad_status = Column(String(20), default="unknown", index=True)
    # pending（切块后默认）/ done / failed / skipped（未选中未识别）
    recognize_status = Column(String(20), default="pending")
    # 是否收录进错题本。默认随 grad_status（错→true），但人工可改：未批的题也能收录
    include_in_handout = Column(Boolean, default=False)
    answer_image = Column(Text, default="[]")  # 人工上传的解析截图路径（JSON 数组）
    answer_source = Column(String(20), default="none")  # none / manual / ai
    # AI 生成的解析必须人工核对后才允许进 PDF（错题本出现错误解析会直接误导学生）。
    # 实施计划要求这条约束，但不含记录其状态的字段，故在此补一个。
    answer_reviewed = Column(Boolean, default=False)
    error_reason = Column(Text, default="")  # 错因标签（逗号分隔）
    image_block = Column(Text, default="")  # 题块图（输入即去手写后的干净件）
    # 人工合并：一道题被分栏/排版切成多块时，用户把它们并为一条记录。
    # merge_id 为空＝未合并；非空时 merged_block_count 是成员块数，block_images
    # 按拼接顺序存各成员的块图 URL，而 image_block 始终是合成后的**一张**图 ——
    # 识别、导出、预览都只认这一张，多图只是留档与「拆分」时的还原依据。
    merge_id = Column(String(50), default="")
    merged_block_count = Column(Integer, default=1)
    block_images = Column(Text, default="[]")
    image_figure = Column(Text, default="[]")  # 人工从干净件截取的图形（JSON 数组）
    mastery_status = Column(String(20), default="pending")  # pending / redone / mastered
    # 数学题入库后回填；非空即表示已入库，被查重拦下或人工跳过的题保持为空
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    snapshot_source = Column(String(200), default="")  # 入库时的来源快照
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    @property
    def answer_images(self):
        return _safe_json_list(self.answer_image)

    @answer_images.setter
    def answer_images(self, value):
        self.answer_image = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)

    @property
    def figure_images(self):
        return _safe_json_list(self.image_figure)

    @figure_images.setter
    def figure_images(self, value):
        self.image_figure = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)

    @property
    def block_image_list(self):
        return _safe_json_list(self.block_images)

    @block_image_list.setter
    def block_image_list(self, value):
        self.block_images = json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)

    def to_dict(self):
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "student_id": self.student_id,
            "subject": self.subject or "math",
            "page_no": self.page_no or 1,
            "question_no": self.question_no or "",
            "block_index": self.block_index or 0,
            "block_y_start": float(self.block_y_start or 0.0),
            "block_y_end": float(self.block_y_end or 1.0),
            "block_x_start": float(self.block_x_start if self.block_x_start is not None else 0.0),
            "block_x_end": float(self.block_x_end if self.block_x_end is not None else 1.0),
            "column_index": int(self.column_index or 0),
            "content": self.content or "",
            "answer_markdown": self.answer_markdown or "",
            "question_type": self.question_type or "detailed_answer",
            "difficulty": self.difficulty or "normal",
            "knowledge_tags": self.knowledge_tags or "",
            "solve_method": self.solve_method or "",
            "source": self.source or "",
            "category_compulsory": self.category_compulsory or "",
            "category_chapter": self.category_chapter or "",
            "category_knowledge": self.category_knowledge or "",
            "related_curriculums": _safe_json_list(self.related_curriculums),
            "tags": self.tags or "",
            "grad_status": self.grad_status or "unknown",
            "recognize_status": self.recognize_status or "pending",
            "include_in_handout": bool(self.include_in_handout),
            "answer_images": self.answer_images,
            "answer_source": self.answer_source or "none",
            "answer_reviewed": bool(self.answer_reviewed),
            "error_reason": self.error_reason or "",
            "image_block": self.image_block or "",
            "figure_images": self.figure_images,
            "merge_id": self.merge_id or "",
            "merged_block_count": int(self.merged_block_count or 1),
            "block_images": self.block_image_list,
            "mastery_status": self.mastery_status or "pending",
            "question_id": self.question_id,
            "snapshot_source": self.snapshot_source or "",
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
            "updated_at": (self.updated_at.isoformat() + "Z") if self.updated_at else None,
        }


# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables
def init_db():
    from mathbank.db_migrations import (
        LATEST_SCHEMA_VERSION,
        REQUIRED_TABLES,
        create_pre_migration_backup,
        migrate_database,
        schema_version,
    )

    # Refuse a future schema before create_all or any legacy ALTER can mutate it.
    current_version = schema_version(engine)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current_version} 高于程序支持版本 "
            f"{LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    with engine.connect() as connection:
        existing_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        core_tables = existing_tables & REQUIRED_TABLES
        if existing_tables:
            upgradeable_layouts = (
                {"questions"},
                {"questions", "question_curriculums"},
                REQUIRED_TABLES,
            )
            if current_version != 0:
                upgradeable_layouts = (REQUIRED_TABLES,)
            if core_tables not in upgradeable_layouts:
                missing = ", ".join(sorted(REQUIRED_TABLES - core_tables))
                raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")

            # Old releases legitimately had only the question tables.  Accept
            # those known layouts, but reject a similarly named damaged table
            # before create_all can disguise the missing core columns.
            required_columns = {
                "questions": {
                    "id", "content", "question_type", "category_compulsory",
                    "category_chapter", "category_knowledge", "difficulty",
                    "source", "answer_markdown", "image_paths", "created_at",
                },
                "question_curriculums": {
                    "id", "question_id", "version_code", "compulsory",
                    "chapter", "knowledge",
                },
                "papers": {
                    "id", "title", "subtitle", "paper_type", "total_score",
                    "metadata_json", "created_at",
                },
                "paper_questions": {
                    "id", "paper_id", "question_id", "order_index", "score",
                },
            }
            for table_name in core_tables:
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        f'PRAGMA table_info("{table_name}")'
                    ).fetchall()
                }
                missing_columns = required_columns[table_name] - columns
                if missing_columns:
                    missing = ", ".join(sorted(missing_columns))
                    raise RuntimeError(
                        f"数据库表 {table_name} 缺少核心字段: {missing}"
                    )
    pre_migration_backup = None
    if current_version < LATEST_SCHEMA_VERSION and existing_tables:
        pre_migration_backup = create_pre_migration_backup(
            engine,
            from_version=current_version,
            to_version=LATEST_SCHEMA_VERSION,
        )

    Base.metadata.create_all(bind=engine)
    # Create indexes manually and execute automatic migrations for SQLite databases to ensure maximum performance at scale
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            # Check column existence
            cursor = conn.execute(text("PRAGMA table_info(questions)"))
            columns = [row[1] for row in cursor.fetchall()]
            
            if "review" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN review TEXT DEFAULT ''"))
                print("Added column 'review' to questions table successfully.")
                
            if "association_group_id" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN association_group_id VARCHAR(100) DEFAULT ''"))
                print("Added column 'association_group_id' to questions table successfully.")
                
            if "tikz_code" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tikz_code TEXT DEFAULT ''"))
                print("Added column 'tikz_code' to questions table successfully.")

            if "figure_align" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN figure_align VARCHAR(50) DEFAULT 'right'"))
                print("Added column 'figure_align' to questions table successfully.")
                
            if "tags" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tags TEXT DEFAULT ''"))
                print("Added column 'tags' to questions table successfully.")
                
            if "usage_count" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN usage_count INTEGER DEFAULT 0"))
                print("Added column 'usage_count' to questions table successfully.")

            if "knowledge_list" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN knowledge_list TEXT DEFAULT ''"))
                print("Added column 'knowledge_list' to questions table successfully.")

            if "solve_method" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN solve_method TEXT DEFAULT ''"))
                print("Added column 'solve_method' to questions table successfully.")

            if "related_curriculums" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN related_curriculums TEXT DEFAULT '[]'"))
                print("Added column 'related_curriculums' to questions table successfully.")

            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_compulsory ON questions (category_compulsory)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_chapter ON questions (category_chapter)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_knowledge ON questions (category_knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_question_type ON questions (question_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions (difficulty)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_association_group_id ON questions (association_group_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions (tags)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_usage_count ON questions (usage_count)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_knowledge_list ON questions (knowledge_list)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_solve_method ON questions (solve_method)"))

            # Migrate papers table: add is_template column if missing (幂等)
            try:
                conn.execute(text("ALTER TABLE papers ADD COLUMN is_template INTEGER DEFAULT 0"))
                print("Added column 'is_template' to papers table successfully.")
            except Exception as alter_err:
                # 列已存在（幂等场景）时忽略 duplicate column 错误
                err_msg = str(alter_err).lower()
                if "duplicate column" in err_msg or "already exists" in err_msg:
                    pass
                else:
                    raise

            # Create indexes on question_curriculums
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_lookup ON question_curriculums (version_code, compulsory, chapter, knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_qid ON question_curriculums (question_id)"))

            # Create indexes on the mistake-scanning tables（按学生 + 学科聚合，
            # 供三期学生档案使用；单列索引已由 ORM 的 index=True 建出）
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_mistake_records_student_subject ON mistake_records (student_id, subject)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_mistake_batches_student ON mistake_batches (student_id, batch_date)"))

            # Auto-migrate legacy data to A-version question_curriculums
            cursor = conn.execute(text("SELECT COUNT(*) FROM question_curriculums"))
            count = cursor.fetchone()[0]
            if count == 0:
                conn.execute(text("""
                    INSERT INTO question_curriculums (question_id, version_code, compulsory, chapter, knowledge)
                    SELECT id, 'A', category_compulsory, category_chapter, category_knowledge
                    FROM questions
                """))
                print("Successfully auto-migrated legacy question categories to A-version question_curriculums mapping.")
    except Exception as e:
        raise RuntimeError("数据库旧字段或索引迁移失败，服务已停止启动") from e

    # Remap legacy / invalid difficulty values to the canonical default.
    # Keeps the difficulty column consistent with the 4-value vocabulary
    # (easy_error / normal / challenge / qiangji) used across the app.
    # Idempotent: only rows whose difficulty is NULL or outside the vocabulary
    # (e.g. the legacy "medium") are touched.
    try:
        from mathbank.curriculums import DIFFICULTY_VALUES

        valid_tuple = tuple(DIFFICULTY_VALUES)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE questions SET difficulty = 'normal' "
                    f"WHERE difficulty IS NULL OR difficulty NOT IN {valid_tuple}"
                )
            )
    except Exception as remap_err:
        print(f"[Database] Difficulty remap skipped: {remap_err}")

    migration_result = migrate_database(
        engine, pre_migration_backup=pre_migration_backup
    )
    configure_sqlite_wal(engine)
    if migration_result.get("from_version") != migration_result.get("to_version"):
        print(f"[Database] Schema migration complete: {migration_result}")
