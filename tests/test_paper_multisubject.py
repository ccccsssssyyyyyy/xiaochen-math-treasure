"""多学科混卷（一份卷子同时含数学/物理/化学）的契约测试。

背景：抬头那行学科、页脚前缀、LaTeX ``\\subject``、答题卡标题原先都把「数学」写死，
一份卷子只能印数学。改动把学科变成可编辑 + 按学科分部分，代价是两个新风险：

1. **单科卷被顺手改坏** —— 绝大多数现存卷子都是单科，任何一点排版漂移都是回归；
2. **混科卷题号跳号** —— exam-19 模板的题号锚点（单选 1 / 多选 9 / 填空 12 / 解答 15）
   是数学新高考卷结构，混入物化后题号会跳着走。

所以这里钉四层：

* **零回归**：单科卷的 LaTeX 与改动前备份逐字对照（只允许差一行显式页脚设置），
  并把两份都真编译成 PDF、抽出文本逐字对照 —— 静态读源码证明不了「印出来一样」。
* **结构**：混科卷 = 学科 → 题型 两级，学科顺序固定、题号与大题序号全卷连续，
  并真的编译一次混科 PDF 从文本里读回题号。
* **不炸**：抬头留空时必须编译得过（踩过坑：塞 ``\\hphantom`` 占位会让 exam-zh
  的 ``\\subject`` 报 "! Incomplete \\iffalse"，整卷出不来 PDF）。
* **前端**：真跑 ``tests/js/paper_multisubject_check.js``，断言渲染出的卷面结构。
"""

import importlib.machinery
import importlib.util
import re
import shutil
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from mathbank.paper_helper import build_latex_document, compile_tex_to_pdf
from mathbank.word_export_helper import build_word_document
from main import LOCAL_TOKEN

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPER_JS = PROJECT_ROOT / "static" / "js" / "paper.js"
BACKUP_DIR = PROJECT_ROOT / ".paper-multisubject-backup-20260918"
BACKUP_PAPER_HELPER = BACKUP_DIR / "paper_helper.py.bak"
JS_FIXTURE = Path(__file__).resolve().parent / "js" / "paper_multisubject_check.js"

# 改动里唯一允许出现在单科卷 LaTeX 中的新行：显式把页脚设成 exam-zh 的类默认值
# （写死「数学试题」的默认值改成了按抬头走；单科数学时两串一字不差）。
FOOTER_LINE_MATH = "  page/foot-content = {数学试题第;页（共~;页）},"

CHOICES = "（ ）\n\\begin{choices}\n\\item $2$\n\\item $3$\n\\item $4$\n\\item $5$\n\\end{choices}"


def _q(qid, qtype, subject, content, score=5):
    return {
        "question": {
            "id": qid,
            "question_type": qtype,
            "subject": subject,
            "content": content,
            "difficulty": "normal",
        },
        "score": score,
    }


def _math_paper():
    return [
        _q(101, "single_choice", "math", "已知集合 $A=\\{1,2\\}$，则 $A$ 的子集个数为" + CHOICES),
        _q(102, "detailed_answer", "math", "求函数 $f(x)=x^2-2x$ 在 $[0,3]$ 上的最大值与最小值。", 12),
    ]


def _mixed_paper():
    """数学 2 题 / 物理 2 题 / 化学 2 题，六题正好验「题号 1..6 连续」。"""
    return [
        _q(101, "single_choice", "math", "已知集合 $A=\\{1,2\\}$，则 $A$ 的子集个数为" + CHOICES),
        _q(102, "detailed_answer", "math", "求函数 $f(x)=x^2-2x$ 在 $[0,3]$ 上的最大值与最小值。", 12),
        _q(201, "single_choice", "physics", "关于匀变速直线运动，下列说法正确的是" + CHOICES),
        _q(202, "fill_in_blank", "physics", "初速度 $2\\ \\mathrm{m/s}$，加速度 $1\\ \\mathrm{m/s^2}$，则 $3\\ \\mathrm{s}$ 末速度为 $\\fillin$。"),
        _q(301, "single_choice", "chemistry", "下列物质中属于电解质的是" + CHOICES),
        _q(302, "fill_in_blank", "chemistry", "乙醇完全燃烧的化学方程式：$\\fillin$。"),
    ]


@pytest.fixture(scope="module")
def pre_change_paper_helper():
    """改动前的 paper_helper，用于「单科卷零回归」的逐字对照。"""
    if not BACKUP_PAPER_HELPER.exists():
        pytest.skip(f"找不到改动前备份 {BACKUP_PAPER_HELPER}，无法做逐字对照")
    loader = importlib.machinery.SourceFileLoader("paper_helper_pre_change", str(BACKUP_PAPER_HELPER))
    spec = importlib.util.spec_from_loader("paper_helper_pre_change", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _pdf_text(pdf_bytes: bytes) -> str:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _require_xelatex():
    if shutil.which("xelatex") is None:
        pytest.skip("xelatex 不可用，跳过真实编译校验")


# --------------------------------------------------------------------------
# 1. 单科卷零回归
# --------------------------------------------------------------------------

@pytest.mark.parametrize("paper_type", ["exam", "exam_19", "quiz"])
def test_single_subject_latex_is_unchanged(pre_change_paper_helper, paper_type):
    old = pre_change_paper_helper.build_latex_document("成都高一数学周测", "9 月", paper_type, _math_paper())
    new = build_latex_document(
        "成都高一数学周测", "9 月", paper_type, _math_paper(),
        subject_line="数学", exam_duration=120,
    )
    assert new.replace(FOOTER_LINE_MATH + "\n", "") == old, (
        "单科卷的 LaTeX 出现了预期之外的改动；混科支持不该动单科卷的任何一行"
    )
    assert FOOTER_LINE_MATH in new, "页脚那行没了，物理/化学卷又会印成「数学试题」"


def test_single_subject_rendered_pdf_text_is_unchanged(pre_change_paper_helper):
    """逐字对照 LaTeX 还不够：要证明**印出来的东西**一样。"""
    _require_xelatex()
    old_tex = pre_change_paper_helper.build_latex_document("成都高一数学周测", "", "exam_19", _math_paper())
    new_tex = build_latex_document(
        "成都高一数学周测", "", "exam_19", _math_paper(),
        subject_line="数学", exam_duration=120,
    )
    old_pdf, old_log = pre_change_paper_helper.compile_tex_to_pdf(old_tex, [])
    new_pdf, new_log = compile_tex_to_pdf(new_tex, [])
    assert old_pdf is not None, f"改动前的卷子在这台机器上就编译不过：{str(old_log)[-800:]}"
    assert new_pdf is not None, f"单科卷编译失败：{str(new_log)[-800:]}"
    assert _pdf_text(new_pdf) == _pdf_text(old_pdf), "单科卷印出来的内容变了"


# --------------------------------------------------------------------------
# 2. 混科卷结构 + 真实编译
# --------------------------------------------------------------------------

def test_mixed_subject_latex_has_two_level_structure():
    tex = build_latex_document("数理化学科综合测试卷", "", "exam", _mixed_paper(),
                               subject_line="", exam_duration=150)
    parts = re.findall(r"第([一二三])部分\\quad ([^（]+)（共 (\d+) 题，共 (\d+) 分）", tex)
    assert parts == [("一", "数学", "2", "17"), ("二", "物理", "2", "10"), ("三", "化学", "2", "10")], parts

    # 学科层排在各自题型层之前，且题型序号全卷连续
    marks = re.findall(r"第[一二三]部分\\quad|\\section\{", tex)
    assert marks[0].startswith("第"), marks[:4]
    assert tex.count("\\section{") == 6, "六个「学科×题型」小节"
    assert "选择题" in tex and "填空题" in tex and "解答题" in tex


def test_mixed_subject_demotes_exam_19_so_numbers_stay_continuous():
    tex = build_latex_document("数理化学科综合测试卷", "", "exam_19", _mixed_paper(),
                               subject_line="", exam_duration=150)
    assert "g__examzh_question_index_int" not in tex, (
        "混科卷还在下 exam-19 的题号锚点：单选 1 / 多选 9 / 填空 12 / 解答 15 会把题号跳着排"
    )
    # 单科数学卷必须仍然走 exam_19（否则高考卷模板的题号就不再是标准布局）
    single = build_latex_document("数学卷", "", "exam_19", _math_paper(), subject_line="数学")
    assert "g__examzh_question_index_int" in single


def test_mixed_subject_pdf_prints_continuous_numbers():
    """真编译一次混科卷，从 PDF 文本里读回题号与大题序号。"""
    _require_xelatex()
    tex = build_latex_document("数理化学科综合测试卷", "", "exam_19", _mixed_paper(),
                               subject_line="", exam_duration=150)
    pdf, log = compile_tex_to_pdf(tex, [])
    assert pdf is not None, f"混科卷编译失败：{str(log)[-1500:]}"
    text = _pdf_text(pdf)
    # PDF 抽文本会把「第一部分」与「数学（共 2 题…）」拆成两行、还会在数字前后留空格，
    # 所以先压掉换行再用宽容空白匹配。
    flat = text.replace("\n", "")

    assert re.findall(r"第[一二三]部分\s*(数学|物理|化学)（共\s*\d+\s*题，共\s*\d+\s*分）", flat) == \
        ["数学", "物理", "化学"], f"卷面学科顺序不对：{flat[:600]}"
    assert re.findall(r"[一二三四五六]、(?:选择题|填空题|解答题)", flat) == \
        ["一、选择题", "二、解答题", "三、选择题", "四、填空题", "五、选择题", "六、填空题"], \
        "大题序号没有全卷连续"
    # 题号要从「第一部分」之后开始数：前面「注意事项」的 1./2./3. 也是行首数字，
    # 混进来会把题号序列读成 [1,2,3,1,2,...]。解答题那行印的是「2.（12 分）」，
    # 点后面紧跟全角括号，所以不能要求点后必须跟空格。
    body = text[text.find("第一部分"):]
    numbers = [int(n) for n in re.findall(r"(?m)^\s*(\d+)[.．]", body)]
    assert numbers == [1, 2, 3, 4, 5, 6], f"题号不连续：{numbers}\n{body[:400]}"
    assert re.search(r"考试用时\s*150\s*分钟", flat), "考试用时没跟上用户设定"


# --------------------------------------------------------------------------
# 3. 抬头 / 页脚
# --------------------------------------------------------------------------

@pytest.mark.parametrize("placeholder", [r"\hphantom", r"\phantom", r"\mbox", r"\hspace"])
def test_empty_subject_line_never_uses_a_box_placeholder(placeholder):
    """抬头留空时塞盒子占位会让 exam-zh 的 ``\\subject`` 整卷编译失败。

    ``\\subject`` 用 ``\\hbox_set:Nn`` 量参数宽度再做字距展开，``\\hphantom`` 这类盒子
    会撑坏那段展开逻辑，xelatex 报 "! Incomplete \\iffalse" 且一页都不出。
    类里本来就用 ``\\tl_if_blank`` 判空值，传空是安全的。
    """
    tex = build_latex_document("卷", "", "exam", _math_paper(), subject_line="")
    assert placeholder not in tex, f"{placeholder} 又回来了，留空抬头会编译不过"
    assert re.search(r"\\subject\{\}\s*$", tex, re.M), "留空时应当发出空 \\subject"


def test_empty_subject_line_actually_compiles():
    _require_xelatex()
    tex = build_latex_document("卷", "", "exam", _math_paper(), subject_line="")
    pdf, log = compile_tex_to_pdf(tex, [])
    assert pdf is not None, f"抬头留空的卷子编译失败：{str(log)[-1200:]}"


def test_page_footer_follows_the_subject_line():
    math_tex = build_latex_document("卷", "", "exam", _math_paper(), subject_line="数学")
    assert FOOTER_LINE_MATH in math_tex, "单科数学卷的页脚必须与 class 默认一字不差"

    mixed_tex = build_latex_document("卷", "", "exam", _mixed_paper(), subject_line="数理综合")
    assert "  page/foot-content = {数理综合试题第;页（共~;页）}," in mixed_tex

    blank_tex = build_latex_document("卷", "", "exam", _mixed_paper(), subject_line="")
    assert "  page/foot-content = {第;页（共~;页）}," in blank_tex, "留空时页脚不该还带学科词"


def test_footer_subject_escapes_the_placeholder_separator():
    """页脚格式串用 ASCII 分号当页码占位符，学科里再出现分号会把它切开。"""
    tex = build_latex_document("卷", "", "exam", _mixed_paper(), subject_line="数学;物理")
    foot = re.search(r"page/foot-content = \{([^}]*)\}", tex).group(1)
    assert foot.count(";") == 2, f"页脚占位符数量被学科串里的分号破坏了：{foot}"
    assert "数学；物理试题第" in foot


# --------------------------------------------------------------------------
# 4. 考试用时
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [(150, 150), ("150", 150), (0, 120), (-5, 120), (9999, 120), ("abc", 120), (None, 120)])
def test_exam_duration_is_used_and_out_of_range_falls_back(raw, expected):
    tex = build_latex_document("卷", "", "exam", _math_paper(), subject_line="数学", exam_duration=raw)
    assert f"考试用时 {expected} 分钟" in tex


# --------------------------------------------------------------------------
# 5. Word 导出同样分部分
# --------------------------------------------------------------------------

def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return zf.read("word/document.xml").decode("utf-8")


def test_word_export_two_levels_for_mixed_and_flat_for_single():
    mixed_xml = _docx_text(build_word_document(
        "数理化学科综合测试卷", "", "exam_19", _mixed_paper(), subject_line="", exam_duration=150)[0])
    assert "第一部分  数学（共 2 题，共 17 分）" in mixed_xml
    assert "第二部分  物理（共 2 题，共 10 分）" in mixed_xml
    assert "第三部分  化学（共 2 题，共 10 分）" in mixed_xml

    single_xml = _docx_text(build_word_document(
        "数学卷", "", "exam_19", _math_paper(), subject_line="数学")[0])
    assert "第一部分" not in single_xml, "单科卷不该出现学科层标题"


def test_word_subject_line_is_spread_like_the_latex_version():
    """Word 抬头要和 PDF 长一个样：exam-zh 会把学科串撑到两倍宽，Word 侧照做。

    改动前模板里写死的就是展开后的「数  学」，所以单科数学卷的 Word 抬头外观不变。
    """
    spread = _docx_text(build_word_document("测试卷", "", "exam", _math_paper(), subject_line="数学")[0])
    assert "数  学" in spread

    blank = _docx_text(build_word_document("测试卷", "", "exam", _math_paper(), subject_line="")[0])
    assert "数  学" not in blank and "数学" not in blank, "抬头留空时不该还印出学科行（写死的值不能复活）"


# --------------------------------------------------------------------------
# 5. 答题卡：混科卷必须被挡住
# --------------------------------------------------------------------------

def _seed_two_subjects(client):
    """往测试库里塞一道数学题、一道物理题，返回两个 id。"""
    ids = []
    for subject, stem in (("math", "数学题干"), ("physics", "物理题干")):
        res = client.post(
            "/api/questions",
            data={
                "content": f"{stem}（ ）\n\\begin{{choices}}\n\\item 甲\n\\item 乙\n\\item 丙\n\\item 丁\n\\end{{choices}}",
                "question_type": "single_choice",
                "subject": subject,
                "difficulty": "normal",
                "answer_markdown": "答案略。",
            },
            headers={"X-Local-Token": LOCAL_TOKEN},
        )
        assert res.status_code == 200, res.text
        ids.append(res.json()["question"]["id"])
    return ids


def test_paper_is_multi_subject_helper():
    from main import _paper_is_multi_subject

    assert _paper_is_multi_subject(_math_paper()) is False
    assert _paper_is_multi_subject(_mixed_paper()) is True
    assert _paper_is_multi_subject([]) is False
    # 历史数据没有 subject 字段，一律按数学算，不能误判成混科
    assert _paper_is_multi_subject([{"question": {"id": 1}, "score": 5}]) is False


def test_answer_sheet_endpoint_refuses_mixed_paper(client):
    """A3 答题卡写死「数学答题卡」、题块按数学 19 题卷切，混科套不出可用的卡。

    宁可明确拒绝，也不要吐一张对不上题的答题卡 —— 用户会拿它去印。
    """
    headers = {"X-Local-Token": LOCAL_TOKEN}
    math_id, physics_id = _seed_two_subjects(client)

    def export_sheet(ids):
        return client.post(
            "/api/paper/export/pdf",
            json={
                "title": "测试卷",
                "paper_type": "exam_19",
                "target": "sheet",
                "questions": [{"id": i, "score": 5} for i in ids],
            },
            headers=headers,
        )

    mixed = export_sheet([math_id, physics_id])
    assert mixed.status_code == 400, mixed.text
    assert "答题卡" in mixed.json()["message"] and "多个学科" in mixed.json()["message"]

    single = export_sheet([math_id])
    assert "答题卡只适用于数学 19 题卷" not in single.text, "单科卷不该被这条守卫拦下"


def test_tex_bundle_omits_answer_sheet_for_mixed_paper(client):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    math_id, physics_id = _seed_two_subjects(client)

    def names(ids):
        res = client.post(
            "/api/paper/export/tex",
            json={
                "title": "测试卷",
                "paper_type": "exam_19",
                "questions": [{"id": i, "score": 5} for i in ids],
            },
            headers=headers,
        )
        assert res.status_code == 200, res.text
        with zipfile.ZipFile(BytesIO(res.content)) as zf:
            return zf.namelist()

    assert not any("答题卡" in n for n in names([math_id, physics_id])), \
        "混科卷的打包里还塞了数学专用答题卡"
    assert any("答题卡" in n for n in names([math_id])), "单科数学卷的答题卡不该被一起拿掉"


# --------------------------------------------------------------------------
# 6. 前端实跑
# --------------------------------------------------------------------------

def test_paper_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端语法校验")
    result = subprocess.run([node, "--check", str(PAPER_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_multisubject_frontend_behaviour_runtime():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过前端实跑校验")
    result = subprocess.run(
        [node, str(JS_FIXTURE), str(PAPER_JS)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, (
        "混科前端实跑未通过:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "失败 0 项" in result.stdout, result.stdout
