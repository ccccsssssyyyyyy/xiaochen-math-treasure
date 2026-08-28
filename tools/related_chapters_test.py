"""Unit tests for parse_related_curriculums (融合题多章节校验/归一化)."""
import sys, os
sys.path.insert(0, "/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank")
# 让 main 的 IS_TESTING 守卫跳过运行时锁（服务正在运行）
sys.argv.insert(0, "pytest")
from main import parse_related_curriculums
import json


def check(label, cond):
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"PASS: {label}")


# 1. 空/非法输入 -> "[]"
check("empty string -> []", parse_related_curriculums("") == "[]")
check("whitespace -> []", parse_related_curriculums("   ") == "[]")
check("garbage -> []", parse_related_curriculums("{not json") == "[]")
check("None -> []", parse_related_curriculums(None) == "[]")
check("non-list -> []", parse_related_curriculums('"hello"') == "[]")

# 2. 列表直接传入
raw = [{"compulsory": "必修一", "chapter": "5. 三角函数", "knowledge": "5.1 任意角"}]
out = json.loads(parse_related_curriculums(raw))
check("list passthrough", out == raw)

# 3. 缺 chapter 的条目被丢弃
raw = [{"compulsory": "必修一", "chapter": "", "knowledge": "x"}, {"compulsory": "必修一", "chapter": "集合", "knowledge": "子集"}]
out = json.loads(parse_related_curriculums(raw))
check("drop empty-chapter entries", len(out) == 1 and out[0]["chapter"] == "集合")

# 4. 去重 (compulsory,chapter,knowledge)
raw = [
    {"compulsory": "必修一", "chapter": "集合", "knowledge": "子集"},
    {"compulsory": "必修一", "chapter": "集合", "knowledge": "子集"},
    {"compulsory": "必修一", "chapter": "集合", "knowledge": "交集"},
]
out = json.loads(parse_related_curriculums(raw))
check("dedupe identical", len(out) == 2)

# 5. 非 dict 元素忽略
raw = ["bad", {"chapter": "集合"}, 123]
out = json.loads(parse_related_curriculums(raw))
check("ignore non-dict", len(out) == 1 and out[0]["chapter"] == "集合")

# 6. 字符串 JSON 解析（未给 knowledge 时保持空串，不回退 chapter）
s = '[{"chapter":"导数"},{"compulsory":"选修","chapter":"圆锥曲线"}]'
out = json.loads(parse_related_curriculums(s))
check("json string parsed", len(out) == 2 and out[0]["chapter"] == "导数" and out[0]["knowledge"] == "")

print("\nALL PASS: parse_related_curriculums")
