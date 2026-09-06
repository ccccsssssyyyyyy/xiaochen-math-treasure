import random
import pytest
from mathbank.database import SessionLocal, Question
from main import get_seq_mapping


def test_get_seq_mapping_full_table_matches_expected():
    db = SessionLocal()
    try:
        expected = {q.id: idx + 1 for idx, q in enumerate(db.query(Question).order_by(Question.id.asc()).all())}
        assert get_seq_mapping(db, None) == expected
    finally:
        db.close()


def test_get_seq_mapping_small_subset_matches_expected():
    db = SessionLocal()
    try:
        all_ids = [q.id for q in db.query(Question.id).order_by(Question.id.asc()).all()]
        sample = random.sample(all_ids, min(10, len(all_ids)))
        expected = {qid: idx + 1 for idx, qid in enumerate(all_ids) if qid in sample}
        assert get_seq_mapping(db, sample) == expected
    finally:
        db.close()


def test_get_seq_mapping_large_subset_matches_expected():
    db = SessionLocal()
    try:
        all_ids = [q.id for q in db.query(Question.id).order_by(Question.id.asc()).all()]
        subset = random.sample(all_ids, min(100, len(all_ids)))
        expected = {qid: idx + 1 for idx, qid in enumerate(all_ids) if qid in subset}
        assert get_seq_mapping(db, subset) == expected
    finally:
        db.close()


def test_get_seq_mapping_empty_returns_empty():
    db = SessionLocal()
    try:
        assert get_seq_mapping(db, []) == {}
    finally:
        db.close()
