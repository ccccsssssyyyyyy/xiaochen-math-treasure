"""Curriculum preset loading and metadata defaults.

The four textbook trees live in JSON resources so the backend and browser use
one authoritative copy instead of maintaining independent Python and
JavaScript constants.
"""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json
import re
import unicodedata

from mathbank.paths import CURRICULUMS_DIR


CURRICULUM_NAMES = {
    "A": "人教A版",
    "B": "人教B版",
    "S": "苏教版",
    "H": "沪教版",
}

DEFAULT_QUESTION_TYPES = [
    {"value": "single_choice", "label": "单选题"},
    {"value": "multi_choice", "label": "多选题"},
    {"value": "fill_in_blank", "label": "填空题"},
    {"value": "detailed_answer", "label": "解答题"},
]

DEFAULT_DIFFICULTIES = [
    {
        "value": "easy_error",
        "label": "易错题",
        "color": "text-green-600 bg-green-50 border-green-200",
    },
    {
        "value": "normal",
        "label": "常规题",
        "color": "text-blue-600 bg-blue-50 border-blue-200",
    },
    {
        "value": "challenge",
        "label": "挑战题",
        "color": "text-red-600 bg-red-50 border-red-200",
    },
    {
        "value": "qiangji",
        "label": "强基题",
        "color": "text-purple-600 bg-purple-50 border-purple-200",
    },
]

# Canonical difficulty vocabulary — the single source of truth consumed by
# prompts, input validation, DB defaults and the frontend health-check.
# Keep this in sync with DEFAULT_DIFFICULTIES above.
DIFFICULTY_VALUES = {d["value"] for d in DEFAULT_DIFFICULTIES}


def normalize_difficulty(value: str | None, default: str = "normal") -> str:
    """Return a difficulty value from the canonical vocabulary.

    Anything outside DIFFICULTY_VALUES (None, "", or the legacy "medium")
    falls back to ``default`` (常规题 / normal).
    """

    value = str(value or "").strip()
    return value if value in DIFFICULTY_VALUES else default


# ---------------------------------------------------------------------------
# Tag vocabulary — controlled vocabulary for the free-text multi-tag fields
# (knowledge_list / solve_method). This is the single source of truth so that
# AI auto-tagging and manual entry converge on canonical names instead of
# spawning near-duplicate variants ("函数单调性" vs "函数的单调性").
# ---------------------------------------------------------------------------

RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
TAG_VOCAB_PATH = RESOURCES_DIR / "tag_vocabulary.json"

TAG_FIELDS = ("knowledge_list", "solve_method")


def _norm_text(value) -> str:
    """NFKC normalize + collapse all whitespace (incl. full-width spaces)."""

    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value)))


@lru_cache(maxsize=1)
def load_tag_vocabulary() -> dict:
    """Return ``{field: {canonical: [aliases]}}`` from the bundled JSON.

    A missing file or an empty field falls back to an empty structure so
    callers never have to guard against ``None``.
    """

    empty = {f: {} for f in TAG_FIELDS}
    try:
        with open(TAG_VOCAB_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return empty
    return {f: data.get(f, {}) for f in TAG_FIELDS}


@lru_cache(maxsize=1)
def tag_alias_index() -> dict:
    """Return ``{field: {normalized_alias: canonical}}`` for O(1) lookup."""

    index = {f: {} for f in TAG_FIELDS}
    for field, vocab in load_tag_vocabulary().items():
        if field not in index:
            continue
        for canonical, aliases in vocab.items():
            for alias in aliases or []:
                index[field][_norm_text(alias)] = canonical
    return index


def normalize_tag_list(raw, field: str | None = None) -> str:
    """Normalize a multi-tag value into a comma-joined, de-duplicated string.

    Pipeline:
    1. Accept ``str`` **or** ``list``/``tuple`` (AI classification returns lists).
    2. Split on comma / 、 / ; / newline, strip, NFKC-normalize each token.
    3. If ``field`` is a controlled tag field, map every token to its canonical
       name via the vocabulary, so legacy variants (e.g. ``函数的单调性``)
       collapse into the canonical ``函数单调性``.
    4. De-duplicate on the *final* (possibly remapped) value, preserving order.

    Tokens absent from the vocabulary are kept verbatim — they become new
    canonical candidates for later review rather than being silently dropped.
    """

    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    if not isinstance(raw, str):
        raw = str(raw)

    tokens = [s.strip() for s in re.split(r"[,，;；\n]+", raw) if s and s.strip()]
    if not tokens:
        return ""

    alias = tag_alias_index().get(field) if field in TAG_FIELDS else None
    if alias:
        tokens = [alias.get(_norm_text(t), t) for t in tokens]

    seen = set()
    result = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return ", ".join(result)


def normalize_version_code(version: str) -> str:
    """Validate and normalize a curriculum version code."""

    code = str(version or "A").strip().upper()
    if code not in CURRICULUM_NAMES:
        raise ValueError(f"不支持的教材大纲版本: {version}")
    return code


@lru_cache(maxsize=len(CURRICULUM_NAMES))
def _load_curriculum_cached(version: str) -> dict:
    code = normalize_version_code(version)
    path = CURRICULUMS_DIR / f"{code}.json"
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"教材大纲资源格式错误: {path}")
    return data


def load_curriculum(version: str = "A") -> dict:
    """Return an isolated copy of one curriculum tree."""

    return deepcopy(_load_curriculum_cached(normalize_version_code(version)))


def build_default_metadata(version: str = "A") -> dict:
    """Build the editable metadata payload used by settings and first boot."""

    code = normalize_version_code(version)
    return {
        "question_types": deepcopy(DEFAULT_QUESTION_TYPES),
        "difficulties": deepcopy(DEFAULT_DIFFICULTIES),
        "curriculum": load_curriculum(code),
    }


def get_curriculum_preset(version: str = "A") -> dict:
    """Return the API representation of a curriculum preset."""

    code = normalize_version_code(version)
    return {
        "version": code,
        "name": CURRICULUM_NAMES[code],
        "metadata": build_default_metadata(code),
    }
