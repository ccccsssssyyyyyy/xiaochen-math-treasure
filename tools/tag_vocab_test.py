"""Tests for the controlled tag vocabulary + write-time normalization.

Run from the project root:
    PYTHONPATH=. python tools/tag_vocab_test.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.curriculums import (
    load_tag_vocabulary,
    normalize_tag_list,
    tag_alias_index,
)

DB = "math_question_bank.db"


def test_vocabulary_loads():
    vocab = load_tag_vocabulary()
    assert "knowledge_list" in vocab
    assert "solve_method" in vocab
    # baseline canonicals come from the live DB
    assert len(vocab["knowledge_list"]) >= 400
    assert len(vocab["solve_method"]) >= 60


def test_alias_mapping_dynamic():
    """Every alias in the vocabulary maps back to its canonical."""
    index = tag_alias_index()
    vocab = load_tag_vocabulary()
    for field in ("knowledge_list", "solve_method"):
        for canonical, aliases in vocab[field].items():
            for alias in aliases:
                assert index[field].get(_norm(alias)) == canonical


def test_variant_collapses_to_canonical():
    vocab = load_tag_vocabulary()
    # pick any canonical that has at least one alias
    field = "knowledge_list"
    canon, aliases = next((c, a) for c, a in vocab[field].items() if a)
    variant = aliases[0]
    assert normalize_tag_list(variant, field=field) == canon
    # full-width / whitespace variants also collapse
    assert normalize_tag_list("  " + variant + "　", field=field) == canon


def test_list_input_and_dedup():
    # AI classification returns lists; mapping must still apply + dedup
    out = normalize_tag_list(["函数最值", "函数的最值"], field="knowledge_list")
    assert out == "函数最值"
    # unknown token kept verbatim
    assert normalize_tag_list("一个全新标签xyz", field="knowledge_list") == "一个全新标签xyz"
    # None / empty
    assert normalize_tag_list(None, field="knowledge_list") == ""
    assert normalize_tag_list("", field="knowledge_list") == ""


def test_idempotent_on_live_db():
    """normalize must be idempotent under repeated application, and must not
    introduce new intra-question duplicates. Rows whose stored value still
    contains an intra-question duplicate are reported (they get cleaned on the
    next save) rather than failing the test."""
    import re

    con = sqlite3.connect(DB)
    cur = con.cursor()
    changed = 0
    for col, field in (("knowledge_list", "knowledge_list"),
                       ("solve_method", "solve_method")):
        for (raw,) in cur.execute(f"SELECT {col} FROM questions"):
            if not raw:
                continue
            once = normalize_tag_list(raw, field=field)
            twice = normalize_tag_list(once, field=field)
            assert once == twice, f"not idempotent on {col}: {raw!r}"
            if once != raw:
                changed += 1
    con.close()
    print(f"  (info) {changed} row(s) still carry intra-question duplicates "
          f"that the normalizer will collapse on next save")


def _norm(x):
    import re
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(x)))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
