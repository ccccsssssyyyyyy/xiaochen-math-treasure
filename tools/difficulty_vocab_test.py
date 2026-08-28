"""Difficulty vocabulary consistency test.

Guards the single-source-of-truth refactor:
- curriculums.DIFFICULTY_VALUES is the canonical 4-value set and includes normal
- normalize_difficulty() maps legacy/invalid values (medium, None, "") -> normal
- AI prompts enumerate all 4 values and default ambiguous difficulty to normal
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.curriculums import DIFFICULTY_VALUES, normalize_difficulty
import mathbank.prompts as prompts


EXPECTED = {"easy_error", "normal", "challenge", "qiangji"}


def test_vocab_is_canonical():
    assert DIFFICULTY_VALUES == EXPECTED, DIFFICULTY_VALUES
    assert "normal" in DIFFICULTY_VALUES


def test_normalize_difficulty():
    # canonical pass-through
    assert normalize_difficulty("qiangji") == "qiangji"
    assert normalize_difficulty("normal") == "normal"
    # legacy / invalid -> default normal
    assert normalize_difficulty("medium") == "normal"
    assert normalize_difficulty(None) == "normal"
    assert normalize_difficulty("") == "normal"
    assert normalize_difficulty("weird") == "normal"
    # custom default
    assert normalize_difficulty("medium", default="easy_error") == "easy_error"


def _contains_all_four(text):
    for v in EXPECTED:
        assert v in text, f"prompt missing difficulty value: {v}"


def test_prompts_enumerate_normal():
    curriculum = {"必修一": {"1. 集合": ["1.1 集合的概念"]}}
    classify_prompt = prompts.build_classification_system_prompt(curriculum)
    _contains_all_four(classify_prompt)
    # the classify prompt must default ambiguous difficulty to normal (not easy_error)
    assert "默认为 `normal`" in classify_prompt, "classify prompt default should be normal"
    parse_prompt = prompts.build_pdf_parse_system_prompt(curriculum, generate_answers_bool=False)
    _contains_all_four(parse_prompt)


if __name__ == "__main__":
    test_vocab_is_canonical()
    test_normalize_difficulty()
    test_prompts_enumerate_normal()
    print("difficulty_vocab_test: ALL PASS")
