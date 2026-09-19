"""错题本导出：正文内联图与残留占位符（2026-09-15）。

背景：补图的落点从「记录尾部的 ``figure_images`` 数组」改成「正文里的一行 markdown」
（光标处 / 占位符处）。导出侧必须跟着走，否则改动会在最远的一环上翻车：

1. 正文里的 ``![](url)`` 要收进 ``image_paths`` —— 否则 ``clean_content_for_latex``
   把它变成 ``\\includegraphics{...}``，而文件没被拷进编译目录，**整份讲义编译失败**；
2. 同一张图不能既内联、又在题后附图区印一遍（一份图印两次）；
3. 到导出这一刻还留着的 ``[插图待补: 图N]`` 说明老师没打算补 —— 不能印进学生讲义；
4. 「包含图形」关掉时，正文内联的图也得一起摘掉，否则这个开关名不副实。

三种落点用的是**三个不同的宽度参数**，正好拿来当判别证据：
内联 ``[max width=0.85\\linewidth]`` / 题后附图区 ``[width=0.62\\linewidth]`` /
解析数组 ``[width=0.78\\linewidth]``。
"""

from pathlib import Path

from mathbank.mistake_handout import (
    ANSWER_MODE_MANUAL,
    HandoutOptions,
    build_mistake_handout_latex,
)

INLINE_W = r"[max width=0.85\linewidth]"
FIGURE_BLOCK_W = r"[width=0.62\linewidth]"
ANSWER_BLOCK_W = r"[width=0.78\linewidth]"


def _static_dir(tmp_path: Path) -> Path:
    """造一个与真实布局同形的 static/ 目录（``/static/...`` 前缀按 static_dir.parent 解析）。"""

    figures = tmp_path / "static" / "uploads" / "mistakes" / "7" / "figures"
    figures.mkdir(parents=True)
    for name in ("crop_a.png", "crop_b.png", "keep.png", "shot_a.png"):
        # 内容无所谓：判定只用「文件在不在」。
        (figures / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    return tmp_path / "static"


def _url(name: str) -> str:
    return f"/static/uploads/mistakes/7/figures/{name}"


def _record(**overrides) -> dict:
    record = {
        "id": 1,
        "question_no": "1",
        "question_type": "detailed_answer",
        "error_reason": "",
        "content": "",
        "answer_markdown": "",
        "answer_source": "",
        "answer_reviewed": False,
        "figure_images": [],
        "answer_images": [],
    }
    record.update(overrides)
    return record


def _build(records, static_dir: Path, **options):
    return build_mistake_handout_latex(
        title="错题本",
        subject="math",
        student_name="",
        batch_date="2026-09-15",
        records=records,
        options=HandoutOptions(**options),
        static_dir=static_dir,
    )


# ---------------------------------------------------------------- 1) 收内联图


def test_inline_image_is_collected_into_image_paths(tmp_path):
    """正文内联的图必须进 image_paths —— 否则 xelatex 在编译目录里找不到它。"""

    static_dir = _static_dir(tmp_path)
    built = _build(
        [_record(content=f"如图 ![插图]({_url('crop_a.png')}) 所示，求 $x$。")],
        static_dir,
    )

    assert INLINE_W + "{crop_a.png}" in built.tex_content, built.tex_content
    assert str(static_dir / "uploads" / "mistakes" / "7" / "figures" / "crop_a.png") in built.image_paths


def test_broken_inline_reference_is_dropped_instead_of_breaking_the_compile(tmp_path):
    """引用了一个不存在的文件时，宁可少一张图，也不能让整份讲义编译失败。"""

    static_dir = _static_dir(tmp_path)
    built = _build(
        [_record(content=f"如图 ![插图]({_url('gone.png')}) 所示。")],
        static_dir,
    )

    assert "gone.png" not in built.tex_content, built.tex_content
    assert built.image_paths == []
    assert "所示。" in built.tex_content, "只该摘掉那段图片语法，正文其余部分不动"


# ---------------------------------------------------------------- 2) 不重复印


def test_inline_image_is_not_printed_twice(tmp_path):
    """同一张图不能既在正文里、又在题后附图区再印一遍。

    两半都要卡：只断言「出现次数 == 1」的话，把内联那条路径整个弄丢（退化成
    「题后附图区里那张」）也会全绿。
    """

    static_dir = _static_dir(tmp_path)
    url = _url("crop_a.png")

    # 对照组：老数据 —— 图只在数组里，正文没有内联 → 应当走题后附图区。
    legacy = _build([_record(content="如图，求 $x$。", figure_images=[url])], static_dir)
    assert FIGURE_BLOCK_W + "{crop_a.png}" in legacy.tex_content
    assert legacy.tex_content.count("crop_a.png") == 1

    # 新数据：图内联在正文里，数组里那份是历史残留 → 只该印内联的那一次。
    both = _build(
        [_record(content=f"如图 ![插图]({url}) 所示，求 $x$。", figure_images=[url])],
        static_dir,
    )
    assert INLINE_W + "{crop_a.png}" in both.tex_content, both.tex_content
    assert FIGURE_BLOCK_W + "{crop_a.png}" not in both.tex_content, "正文已有这张图，题后不该再印"
    assert both.tex_content.count("crop_a.png") == 1


def test_other_figures_still_print_after_deduplication(tmp_path):
    """去重只摘掉「正文已有」的那一张，别的配图照旧印。"""

    static_dir = _static_dir(tmp_path)
    built = _build(
        [
            _record(
                content=f"如图 ![插图]({_url('crop_a.png')}) 所示。",
                figure_images=[_url("crop_a.png"), _url("keep.png")],
            )
        ],
        static_dir,
    )

    assert FIGURE_BLOCK_W + "{keep.png}" in built.tex_content, built.tex_content
    assert FIGURE_BLOCK_W + "{crop_a.png}" not in built.tex_content
    assert str(static_dir / "uploads" / "mistakes" / "7" / "figures" / "keep.png") in built.image_paths


# ---------------------------------------------------------------- 3) 残留占位符


def test_leftover_placeholder_never_reaches_the_handout(tmp_path):
    """没补的占位符是内部记号，不能印到学生讲义上。"""

    static_dir = _static_dir(tmp_path)
    built = _build(
        [_record(content="如图：\n\n[插图待补: 图1]\n\n求 $x$ 的值。")],
        static_dir,
    )

    assert "插图待补" not in built.tex_content, built.tex_content
    assert "求 $x$ 的值。" in built.tex_content
    # 顺手把摘掉那一行留下的连续空行收拢了，别在讲义里留一片空白
    assert "\n\n\n" not in built.tex_content


def test_placeholder_only_stem_does_not_claim_recognition_failed(tmp_path):
    """题面原本有内容（只有一个占位符）时，不该退化成「本题面尚未识别」的提示。"""

    static_dir = _static_dir(tmp_path)
    built = _build([_record(content="[插图待补: 图1]")], static_dir)

    assert "本题面尚未识别" not in built.tex_content, built.tex_content


def test_truly_empty_stem_still_says_not_recognized(tmp_path):
    """对照组：题面真的什么都没有时，那条提示必须留着。"""

    static_dir = _static_dir(tmp_path)
    built = _build([_record(content="")], static_dir)

    assert "本题面尚未识别" in built.tex_content


# ---------------------------------------------------------------- 4) 开关语义


def test_include_figures_off_also_drops_inline_images(tmp_path):
    """「包含图形」关掉时，正文内联的图也得摘掉 —— 否则这个开关名不副实。"""

    static_dir = _static_dir(tmp_path)
    built = _build(
        [_record(content=f"如图 ![插图]({_url('crop_a.png')}) 所示。")],
        static_dir,
        include_figures=False,
    )

    assert "crop_a.png" not in built.tex_content, built.tex_content
    assert built.image_paths == []
    assert "所示。" in built.tex_content


# ---------------------------------------------------------------- 5) 解析侧


def test_answer_inline_screenshot_is_collected_and_not_duplicated(tmp_path):
    """解析里内联的截图同样要收进 image_paths，数组里那份不重复印。"""

    static_dir = _static_dir(tmp_path)
    url = _url("shot_a.png")
    built = _build(
        [
            _record(
                content="求 $x$。",
                answer_markdown=f"由题意 ![解析图]({url}) 得 $x=1$。",
                answer_source=ANSWER_MODE_MANUAL,
                answer_images=[url],
            )
        ],
        static_dir,
        answer_mode=ANSWER_MODE_MANUAL,
    )

    assert INLINE_W + "{shot_a.png}" in built.tex_content, built.tex_content
    assert ANSWER_BLOCK_W + "{shot_a.png}" not in built.tex_content, "解析正文已有这张截图，不该再居中印一遍"
    assert built.tex_content.count("shot_a.png") == 1
    assert str(static_dir / "uploads" / "mistakes" / "7" / "figures" / "shot_a.png") in built.image_paths
    assert built.answer_count == 1, "只有截图、没敲文字的解析也算「人工解析就绪」"


def test_answer_array_screenshot_still_prints_when_not_inline(tmp_path):
    """对照组：解析截图不在正文里时，照旧在解析下面居中印出来。"""

    static_dir = _static_dir(tmp_path)
    built = _build(
        [
            _record(
                content="求 $x$。",
                answer_markdown="由题意得 $x=1$。",
                answer_source=ANSWER_MODE_MANUAL,
                answer_images=[_url("shot_a.png")],
            )
        ],
        static_dir,
        answer_mode=ANSWER_MODE_MANUAL,
    )

    assert ANSWER_BLOCK_W + "{shot_a.png}" in built.tex_content
    assert built.tex_content.count("shot_a.png") == 1
