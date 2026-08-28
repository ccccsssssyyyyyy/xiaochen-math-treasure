#!/usr/bin/env python3
"""E2E verification for the 关联章节 (related_curriculums) feature.

Keeps the test question alive through BOTH filter checks, then deletes it.
"""
import json
import os
import sys
import urllib.request
import urllib.error

ROOT = "/Users/ccsssy/WorkBuddy/2026-08-24-00-26-36/math-question-bank"
TOKEN = open(os.path.join(ROOT, ".system_generated", "local_token")).read().strip()
BASE = "http://127.0.0.1:8000"

CH1 = "1. 集合与常用逻辑用语"          # 主分类 chapter
CH2 = "2. 一元二次函数、方程和不等式"   # 关联章节 chapter

REL = [{"compulsory": "必修一", "chapter": CH2, "knowledge": "2.2 基本不等式"}]


def call(method, path, form=None, raw=None, is_json=True):
    url = BASE + path
    headers = {"X-Local-Token": TOKEN}
    data = None
    if form is not None:
        # build multipart manually
        boundary = "----E2EBOUNDARY"
        parts = []
        for k, v in form.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
            parts.append(v.encode("utf-8") if isinstance(v, str) else v)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif raw is not None:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8")
            return r.status, (json.loads(body) if is_json else body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    results = []
    # 1) create test question
    form = {
        "content": "E2E关联章节测试题（可忽略）",
        "question_type": "选择题",
        "category_compulsory": "必修一",
        "category_chapter": CH1,
        "category_knowledge": "1.1 集合的概念",
        "difficulty": "中",
        "source": "E2E-test",
        "related_curriculums": json.dumps(REL, ensure_ascii=False),
    }
    st, body = call("POST", "/api/questions", form=form)
    assert st == 200, f"create failed: {st} {body}"
    qid = (body.get("question") or {}).get("id") or body.get("id")
    assert qid, f"no id in create response: {body}"
    print(f"[1] created test question id={qid}")

    # 2) detail returns related_curriculums
    st, body = call("GET", f"/api/questions/{qid}")
    assert st == 200, f"detail GET failed {st}"
    rc = body.get("related_curriculums")
    ok_detail = isinstance(rc, list) and rc and rc[0].get("chapter") == CH2
    results.append(("detail returns related_curriculums", ok_detail, rc))
    print(f"[2] detail.related_curriculums = {rc}")

    # 3) filter by PRIMARY chapter (CH1) -> should hit
    st, body = call("GET", "/api/questions?chapter=" + urllib.parse.quote(CH1))
    arr = body if isinstance(body, list) else (body.get("items") or [])
    total1 = len(arr) if isinstance(body, list) else body.get("total")
    hit1 = any((it.get("id") == qid) for it in arr)
    results.append((f"primary chapter filter hits id={qid}", hit1, f"total={total1}"))
    print(f"[3] CH1(主分类) total={total1} hit={hit1}")

    # 4) filter by RELATED chapter (CH2 via JSON LIKE) -> should hit
    st, body = call("GET", "/api/questions?chapter=" + urllib.parse.quote(CH2))
    arr = body if isinstance(body, list) else (body.get("items") or [])
    total2 = len(arr) if isinstance(body, list) else body.get("total")
    hit2 = any((it.get("id") == qid) for it in arr)
    results.append((f"related chapter filter hits id={qid}", hit2, f"total={total2}"))
    print(f"[4] CH2(关联章节) total={total2} hit={hit2}")

    # 5) cleanup: delete then confirm gone
    st, body = call("DELETE", f"/api/questions/{qid}")
    print(f"[5] DELETE id={qid} -> {st} {body}")
    st, body = call("GET", f"/api/questions/{qid}")
    gone = (st == 404)
    results.append(("test question cleaned up (404 after delete)", gone, f"status={st}"))
    print(f"[6] after delete GET status={st} (expect 404)")

    print("\n=== SUMMARY ===")
    all_ok = True
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({detail})")
        all_ok = all_ok and ok
    print("ALL PASS" if all_ok else "SOME FAILED")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    import urllib.parse
    main()
