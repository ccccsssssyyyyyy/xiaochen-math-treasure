import json
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_JS_DIR = PROJECT_ROOT / "static" / "js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_insert_answer_image_tag_inline_vs_append():
    """方案 E：光标在文末→保留原「图文分离」追加；光标在文字中间→内联去空行。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    tag_start = ocr_source.index("function insertAnswerImageTag(filePath, caretPos)")
    tag_end = ocr_source.index("function renderAnswerImageBadges", tag_start)
    tag_source = ocr_source[tag_start:tag_end]

    script = """
    function Event() {}
    const fakeTextarea = {
      value: '',
      selectionStart: 0,
      selectionEnd: 0,
      setSelectionRange(s, e) { this.selectionStart = s; this.selectionEnd = e; },
      dispatchEvent() {},
      focus() {}
    };
    const document = {
      getElementById: (id) => (id === 'editAnswerMarkdown' ? fakeTextarea : null)
    };
    function syncAnswerImagesFromMarkdown() {}
    """ + tag_source + """
    const out = {};

    // Case 1: 末尾追加（caretPos=null）→ 保留原行为，图文之间留空行
    fakeTextarea.value = '这是解析的第一段文字。';
    fakeTextarea.selectionStart = fakeTextarea.value.length;
    insertAnswerImageTag('/static/uploads/a.png', null);
    out.appendResult = fakeTextarea.value;

    // Case 2: 光标在文字中间内联（caretPos=6）→ 用空格包裹，不强制换行
    fakeTextarea.value = '这是解析的第一段文字。';
    fakeTextarea.selectionStart = 6;
    insertAnswerImageTag('/static/uploads/b.png', 6);
    out.inlineResult = fakeTextarea.value;

    // Case 3: 光标在开头内联（caretPos=0）→ 图片 tag 在最前，后接一个空格
    fakeTextarea.value = '开头文字。';
    fakeTextarea.selectionStart = 0;
    insertAnswerImageTag('/static/uploads/c.png', 0);
    out.inlineHead = fakeTextarea.value;

    console.log(JSON.stringify(out));
    """
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    out = json.loads(result.stdout.strip().splitlines()[-1])

    # 末尾追加：原文字在前，图片 tag 在末尾且前后保留空行
    assert out["appendResult"].startswith("这是解析的第一段文字。")
    assert out["appendResult"].endswith(
        "\n\n![图片解答](/static/uploads/a.png)\n\n"
    ), out["appendResult"]

    # 中间内联：用空格包裹，不强制换行，原文字被切分
    assert " ![图片解答](/static/uploads/b.png) " in out["inlineResult"], out["inlineResult"]
    assert out["inlineResult"].startswith("这是解析的"), out["inlineResult"]
    assert out["inlineResult"].endswith("一段文字。"), out["inlineResult"]

    # 开头内联：图片 tag 在最前，后接一个空格
    assert out["inlineHead"].startswith(
        "![图片解答](/static/uploads/c.png) "
    ), out["inlineHead"]
