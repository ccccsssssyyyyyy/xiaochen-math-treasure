"""Central prompt constants and pure prompt builders for MathBank AI flows."""

import json

from mathbank.source_normalize import build_source_rule_prompt


COMMON_OCR_PROMPT = (
    "请精确识别并提取图像中的所有文字与数学公式（不得遗漏方括号与题目来源），直接输出转录结果，严禁包含任何前言或解释。\n"
    "【排版与 LaTeX 语法规范】:\n"
    "1. 公式级包裹：仅对纯数学符号、方程、集合与代数式使用 LaTeX 包裹（行内 $...$，独立 $$...$$）。严禁整段中文包裹 LaTeX，严禁在公式内部滥用 \\text{...} 包裹大段中文。\n"
    "2. 变量与坐标包裹：几何点符号、单个字母变量及坐标表达式（如 $(1,2)$）必须严格包裹单美元符号 $...$。\n"
    "3. 选择题 choices 环境规范：选项部分统一格式化为 LaTeX 的 `choices` 环境（使用 \\begin{choices} 和 \\end{choices} 包裹，每项以 \\item 开头，剥离原本的 A., B., C., D. 标号前缀）。\n"
    "   **严禁将多个选项写在同一行内联**（如 `A. $a>0>b$ B. $a>b>0$ C. $b>a>0$ D. $b>0>a$` 是错误的），每个选项必须独占一行、以 \\item 开头；系统会自动为每个选项编号 A/B/C/D，因此 \\item 内绝不要再写 A./B. 等显式标号。正确示例：\n"
    "   \\begin{choices}\\n\\item $a>0>b$\\n\\item $a>b>0$\\n\\item $b>a>0$\\n\\item $b>0>a$\\n\\end{choices}\n"
    "4. 空行换行规范：在多行推导、解答步骤或小标号（如 (1), (2), ①, ②）之间，必须使用空行/双回车（\\n\\n）分隔。\n"
    "5. 表格与层级：表格用 `tabular` 保留行列结构，合并格用 `multicolumn`/`multirow`；标题、小问和图号顺序不变。\n"
    "6. 多图/表内图：在各图原位置写 `[插图待补: 图1]`（无图号按阅读顺序编号），表格内占位留在所属单元格；勿描述、猜测或重绘。\n"
    "7. 纯净符号：过滤 \\, 或 \\! 等干扰渲染的薄空格。"
)

ILLUSTRATION_BOX_PROMPT = (
    "\n8. 自动绘图：仅当全图恰有一幅且位于表格外的独立几何/函数图时，在文末追加"
    " `[ILLUSTRATION_BOX: ymin, xmin, ymax, xmax]`（0-100 整数）；多图或无图时绝不输出该标记。"
)


CLASSIFICATION_PRIORITY_RULE = (
    "多模块融合题定位：先识别解题过程中实际参与推导的全部教材模块；若涉及两个或以上候选模块，"
    "必须按上方教材大纲的排列顺序，选择位置最靠后的模块作为最终分类。先比较学段从上到下的顺序，"
    "若属于同一学段，再比较章节从前到后的顺序；不得按考点在题干中的出现先后、篇幅多少或主次印象决定。"
    "例如一道题同时实质考查必修一“三角函数”和必修二“平面向量及其应用”，最终必须定位到必修二的"
    "“平面向量及其应用”。仅作为背景条件被提及、且解题无需使用的内容不计入候选模块。"
)


def build_curriculum_text(curriculum: dict) -> str:
    """Render the active curriculum as a compact prompt fragment."""

    lines = []
    for book, chapters in curriculum.items():
        lines.append(f"- {book}: {list(chapters.keys())}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_mistake_curriculum_text(curriculum: dict) -> str:
    """错题识别的教材范围片段：书 → 章节 → 小节，三级全列。

    比 ``build_curriculum_text`` 多一层小节。错题识别要求模型**逐字**给出小节名，
    只给到章节它就只能拿 knowledge_tags 自由发挥（「加速度」这类标签跟
    「2.2 匀变速直线运动速度与时间的关系」永远匹配不上），小节字段必然长期为空。
    """

    lines = []
    for book, chapters in (curriculum or {}).items():
        lines.append(f"- {book}")
        for chapter, sections in (chapters or {}).items():
            lines.append(f"    - {chapter}: {list(sections or [])}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_mistake_block_system_prompt(
    subject: str = "math", curriculum: dict | None = None
) -> str:
    """错题工作台的「单题块识别」系统提示词。

    与拆卷提示词（``build_pdf_parse_system_prompt``）的三点关键差异：

    1. **教材定位照学科传树**：物理（教科版）/ 化学（人教版）的教材树随三科错题库
       一起落地了，所以这里由调用方把**该学科的树**传进来，让模型逐字给出
       学段 / 章节 / 小节，再由 ``normalize_category_fields`` 做受控归一。
       传空树才退回「不判教材定位」—— 绝不拿数学树去套物理题，那会把物理题
       打上数学章节（调用方见 ``main.get_subject_curriculum_tree``）。
    2. **不输出解答与答案**：错题本的解析页另有来源（人工截图或单独的 AI 生成
       步骤），识别阶段混入解答只会污染题面。
    3. **规则刻意精简**：输入图已由上游工具完成去手写，这里不需要再堆
       「忽略一切手写痕迹」这类反向约束 —— 实践中「严禁 X」与「必须 X」并列
       会互相稀释，正是历史上选择题选项被整组剥离的诱因。
    """

    subject_label = {
        "math": "数学",
        "physics": "物理",
        "chemistry": "化学",
    }.get(str(subject or "").strip().lower(), "理科")

    has_tree = isinstance(curriculum, dict) and bool(curriculum)
    if has_tree:
        books = "、".join(str(book) for book in curriculum.keys())
        locate_block = (
            "【教材范围 —— 本题必须落在其中，三级名称逐字照抄】\n"
            f"{build_mistake_curriculum_text(curriculum)}"
            f"【学段 compulsory 受控取值】: {books}\n"
        )
        locate_rule = (
            "6. 顺带做教材定位，三档一起给：`compulsory` 只能填上方受控取值里的完整书名"
            "（如「必修一」）；`chapter` 填该学段下的精确章节名、`category_knowledge` "
            "填该章节下的精确小节名，两者都要**逐字**取自上方教材范围（含编号前缀与空格）。"
            "严禁自造、缩写、改写或增删序号字词，也严禁填教辅自拟的专题名。"
            "某一档确实拿不准就填空字符串 —— 宁缺勿错，留空的系统会归入「未分类」由人工补。\n"
        )
    else:
        locate_block = ""
        locate_rule = "6. 不判定教材章节、学段或知识点来源。\n"

    category_json = ""
    if has_tree:
        category_json = (
            '  "compulsory": "学段（受控取值里的完整书名，拿不准填空字符串）",\n'
            '  "chapter": "章节（教材范围里的精确章节名，拿不准填空字符串）",\n'
            '  "category_knowledge": "小节（教材范围里的精确小节名，拿不准填空字符串）",\n'
        )

    return (
        f"你是一个把扫描题块转写为可编辑文本的助手。用户给你的是一张已经裁好的"
        f"{subject_label}题块图片，图上是印刷体题干。请把它转写成干净的 LaTeX 文本。\n"
        + locate_block +
        "【转写规则】\n"
        "1. 原样转写题干文字与数学符号，不增删、不改写、不做同义替换。题号照抄原文"
        "（如「7」「12(2)」）；块内看不到题号就留空字符串。\n"
        "2. 数学符号、公式、方程、集合、坐标一律用 LaTeX 包裹（行内 $...$，独立 $$...$$）；"
        "中文用普通文本，不要整段裹进 LaTeX。\n"
        "3. 选择题的选项统一写成 LaTeX 的 choices 环境：每项以 \\item 独占一行，"
        "不要写 A./B./C./D. 标号（系统会自动编号）。示例：\n"
        "   \\begin{choices}\n   \\item $a>0>b$\n   \\item $b>0>a$\n   \\end{choices}\n"
        "4. 题块里的几何图、受力图、图象、表格一律**留位不画**：在它原来的位置另起一行、"
        "只写一个占位标记（如 `[插图待补: 图1]`，按阅读顺序编号），并把 has_figure 置为 true。"
        "这一行前后不要包任何 LaTeX 环境或图形命令 —— `tikzpicture`、"
        "`\\includegraphics`、`\\begin{center}`、`\\begin{figure}`、`\\caption` 系统全都不渲染，"
        "写进去只会变成一行乱码；也不要描述图形内容、不要画 ASCII 示意图。"
        "`content` 字段里不得出现 `tikzpicture` 或 `includegraphics` 字样。\n"
        "5. 推导步骤、小问（如 (1)、(2)、①②）之间用空行分隔。\n"
        + locate_rule +
        "7. 不输出答案、解析或解题过程。\n"
        "8. 只输出一个合法 JSON 对象，不要任何 Markdown 代码块标记、前言或解释文字：\n"
        "{\n"
        '  "question_no": "原卷题号，无则空字符串",\n'
        '  "content": "题干（LaTeX + markdown）",\n'
        '  "question_type": "single_choice / multi_choice / fill_in_blank / detailed_answer",\n'
        '  "difficulty": "easy_error / normal / challenge / qiangji",\n'
        '  "knowledge_tags": ["知识点1", "知识点2"],\n'
        '  "solve_method": ["方法1"],\n'
        + category_json +
        '  "has_figure": false,\n'
        '  "figure_count": 0\n'
        "}\n"
        "question_type 判据：含 \\begin{choices} 且题干要求选一个为 single_choice，"
        "要求选多个（题干含「多选」「至少」「所有」等）为 multi_choice；含 \\fillin 或下划线填空为 "
        "fill_in_blank；其余为 detailed_answer。无法判断时给 detailed_answer。\n"
        "difficulty 无法判断时给 normal。knowledge_tags 与 solve_method 用简短中文标签，"
        f"没有把握就给空数组 —— 不要编造{subject_label}教材里不存在的小节名，"
        "也不要编造教材范围里不存在的章节名或小节名。content 里再次确认：只写占位标记 "
        "`[插图待补: 图N]`，一个字都不要写 TikZ 或 includegraphics。"
    )


def build_classification_system_prompt(curriculum: dict) -> str:
    curriculum_text = build_curriculum_text(curriculum)
    return (
        "你是一个专门为教材分类的 AI 专家。请分析以下输入的题目，将其归入特定的教材体系中。\n"
        "【可选教材范围及各章名称】:\n"
        f"{curriculum_text}\n"
        f"【学段 compulsory 受控取值 — 严格从此列表中选取，不得自创、改写或填章节/小节名】: {'、'.join(list(curriculum.keys())) if isinstance(curriculum, dict) else '（见上方教材范围顶层书名）'}\n"
        "【分类规则】:\n"
        "1. 仔细阅读并推导题目考点。\n"
        "2. 必须在上面的可选教材范围中为本题挑选最合适的一个【学段】（例如：必修一）和一个【所属章节】（例如：5. 三角函数，必须是可选章节中的精确字符串）。\n"
        f"3. {CLASSIFICATION_PRIORITY_RULE}\n"
        "4. 判定细粒度题型 `question_type`，取值只能是如下之一：单选题为 `single_choice`，多选题为 `multi_choice`，填空题为 `fill_in_blank`，解答题为 `detailed_answer`。通过题干判断单选/多选（如题干含\"多选题\"、\"(多选)\"、要求选出多个选项等）。题干出现 `\\fillin` 时判为 `fill_in_blank`，出现 `\\begin{choices}` 时判为选择题；无法可靠判断时默认为 `single_choice`。\n"
        "5. 判定难度 `difficulty`：易错题为 `easy_error`，常规题为 `normal`，挑战题为 `challenge`，强基题为 `qiangji`；无法判断时默认为 `normal`（常规题）。\n"
        "6. 从题干开头剥离出处信息填入 `source`。题干里形如「（2023·全国甲卷）」的括注即为出处；无出处则填空字符串。\n"
        f"{build_source_rule_prompt(with_examples=True)}\n"
        "7. `compulsory` 必须是上方「学段受控取值」中的某一个**完整书名**（例如「必修一」），绝不可填章节名、小节名或自造词；`chapter` 必须是该学段下**精确的章节名**（须与上方教材范围完全一致，不得增删序号与字词）；`category_knowledge` 必须是可选小节中的精确字符串，不存在则给最接近的章节名。\n"
        "8. `knowledge_list` 与 `solve_method` 均为字符串数组：knowledge_list 为本题知识点文本标签（如 [\"函数单调性\",\"导数应用\"]），solve_method 为本题解题方法文本标签（如 [\"导数法\",\"分类讨论\"]）。\n"
        "9. 综合题/融合题常跨越多个章节。除主分类（compulsory/chapter/category_knowledge）外，若本题确实还涉及教材范围内其他章节，请在 `related_chapters` 中以数组给出这些【额外】章节，每个元素为 {\"compulsory\": \"学段\", \"chapter\": \"章节\", \"knowledge\": \"小节\"}（小节可省略或给最接近章节名，必须是上面教材范围内的精确字符串）。若本题仅属于单一章节，则 `related_chapters` 给空数组 []。\n"
        "10. 你的输出必须是一个合法 JSON 字符串，包含且仅包含以下 key，不要有任何多余的 Markdown 标记、代码块或解释文字：\n"
        "{\n"
        '  "question_type": "single_choice / multi_choice / fill_in_blank / detailed_answer",\n'
        '  "difficulty": "easy_error / normal / challenge / qiangji",\n'
        '  "source": "清洗后的来源字符串",\n'
        '  "compulsory": "学段名称",\n'
        '  "chapter": "具体章节名称",\n'
        '  "category_knowledge": "小节名称",\n'
        '  "knowledge_list": ["知识点1", "知识点2"],\n'
        '  "solve_method": ["方法1", "方法2"],\n'
        '  "related_chapters": [{"compulsory": "学段", "chapter": "额外章节", "knowledge": "小节"}]\n'
        "}\n"
        "不要包含 ```json ``` 标记，只输出最干净的 JSON。"
    )


def build_ai_solve_prompts(
    question_type: str,
    content: str,
    ocr_result: str = "",
    custom_prompt: str = "",
) -> tuple[str, str]:
    """Build the system/user prompt pair for the single-question solver."""

    type_mapping = {
        "single_choice": "单选题",
        "multi_choice": "多选题",
        "fill_in_blank": "填空题",
        "detailed_answer": "解答题",
    }
    type_str = type_mapping.get(question_type, "数学题")

    if question_type == "single_choice":
        first_block_header = "\\\\textbf{【参考答案】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【参考答案】}：最开头第一行直接醒目输出该单选题的唯一正确选项字母（如 A、B、C 或 D），严禁包含长篇推导。\n"
            "2. \\\\textbf{【解析过程】}：详细写出选项推导、排除理由与分析步骤。\n"
            "3. \\\\textbf{【解析思路】}：概括解题核心突破口与思考脉络。\n"
            "4. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )
    elif question_type == "multi_choice":
        first_block_header = "\\\\textbf{【参考答案】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【参考答案】}：最开头第一行直接醒目输出该多选题的所有正确选项字母（如 AB、ACD），严禁包含长篇推导。\n"
            "2. \\\\textbf{【解析过程】}：详细写出各个选项的逐一推导、证明与分析步骤。\n"
            "3. \\\\textbf{【解析思路】}：概括解题核心突破口与思考脉络。\n"
            "4. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )
    elif question_type == "fill_in_blank":
        first_block_header = "\\\\textbf{【参考答案】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【参考答案】}：最开头第一行直接醒目输出该填空题最终正确答案（如具体数值、公式、集合或区间），严禁包含长篇推导。\n"
            "2. \\\\textbf{【解析过程】}：详细写出求解推导与分析步骤。\n"
            "3. \\\\textbf{【解析思路】}：概括解题核心突破口与思考脉络。\n"
            "4. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )
    else:
        first_block_header = "\\\\textbf{【规范解答】}"
        format_rules = (
            "【必须包含的结构化板块 (使用 LaTeX 粗体 `\\\\textbf{...}`)】:\n"
            "1. \\\\textbf{【规范解答】}：符合高考卷面得分规范的标准解答步骤，写齐必要推理逻辑与定理依据，无冗余废话。\n"
            "2. \\\\textbf{【解析思路】}：若有多个小问（如 (1)、(2)），按小问分点概括破题脉络与定理转化（可一句话点拨易错陷阱或另解）。\n"
            "3. \\\\textbf{【核心知识点】}：列出关键公式、定理或思想方法。"
        )

    system_instructions = (
        "你是一位极其严谨的资深高中数学教研专家。请解答用户输入的高中数学题目。\n"
        "【解题纪律与超纲禁令】\n"
        "1. 严禁超纲：解题思路与技巧必须完全限制在中国普通高中数学大纲范围内（严禁使用微积分、洛必达法则、泰勒展开等大学高等数学方法）。\n"
        "2. 逻辑严密与极简凝练：推导过程必须逻辑完备、因果严谨（写明公理定理前提，不盲目跳步），但语言精练直奔得分点，拒绝任何多余的口水话或过度解释。\n"
        f"3. 零多余前言尾注：回答必须干净地从结构化板块开始，第一个字符必须是“{first_block_header}”，严禁包含任何问候语、前言、导语或尾注总结。\n"
        "【LaTeX 与段落换行规范】\n"
        "1. 必须使用标准 LaTeX 语法书写公式（行内 $...$，行间 $$\\n...\\n$$）。绝对禁止使用 Markdown 的 ** 双星号加粗语法，也严禁用单个 `*` 把几何顶点/变量做成 Markdown 斜体（一律用 `$...$` 包裹），标题必须使用 LaTeX 粗体 `\\\\textbf{...}`。\n"
        "2. 物理换行与空行规范 (极重要)：在分步推导、证明步骤（如“解：”、“(1) 证明：”、“因为”、“所以”、“故”）、不同小问以及标题与段落之间，必须使用空行/双回车（\\n\\n）分隔！单回车无法实现物理换行。\n"
        f"{format_rules}\n"
        "请直接输出上述结构化板块。"
    )

    user_prompt = f"题目类型: {type_str}\n"
    if ocr_result.strip():
        user_prompt += (
            "已有的 OCR 识别解析/草稿内容如下，请在此基础上进行润色、修正、细化或简化，并生成最终解答步骤：\n"
            f"{ocr_result}\n\n"
        )
    if custom_prompt:
        user_prompt += f"补充引导指令: {custom_prompt}\n"
    user_prompt += f"题干内容:\n{content}"
    return system_instructions, user_prompt


def build_paper_selection_prompts(
    teacher_prompt: str,
    limit: int,
    candidates: list[dict],
    is_review_intent: bool,
) -> tuple[str, str]:
    """Build prompts for pedagogical AI paper selection."""

    review_hint = (
        "\n特别注意：教师明确要求提取【复习/考过/做过的题目】，请优先从候选池中遴选 usage_count > 0 的试题！\n"
        if is_review_intent
        else ""
    )
    system_prompt = (
        "你是一位极其资深的高中数学教研组长和智能命题专家。\n"
        "请根据教师输入的自然语言组卷需求，从给出的候选试题池中遴选出最符合教学意图、涵盖关键结构情形、具备错解检验能力的题目。"
        f"{review_hint}\n"
        "【输出格式规范】\n"
        "必须且只能返回一个可解析的合法 JSON 对象，绝对禁止包含 Markdown 格式标记代码块（例如不要写 ```json ... ```）：\n"
        "{\n"
        '  "selected_ids": [12, 45, 89],\n'
        '  "ai_analysis": "【教研组卷分析与情形覆盖】\\n1. 考察重点：...\\n2. 难度与结构情形：涵盖保底情形与易错辨析...\\n3. 教学建议：..."\n'
        "}"
    )
    user_content = (
        f"【教师组卷需求】: {teacher_prompt}\n"
        f"【需要挑选的题目数量】: {limit} 道\n"
        f"【候选试题池】:\n{json.dumps(candidates, ensure_ascii=False)}"
    )
    return system_prompt, user_content


def build_answer_rule(generate_answers_bool: bool) -> str:
    """答案提取/生成规则（完整提示与多块解析的精简提示**必须共用同一份措辞**）。

    早期实现里精简提示完全不带答案规则，导致第 2..N 块的模型按默认人设把解析
    全部写出来，落库前又被「未勾选自动生成 → 清空无 [EXTRACTED_ORIGINAL] 标记的
    解析」整段抹掉——既白烧输出 token，又抬高撞上 max_output_tokens 截断而漏题的概率。

    generate_answers_bool=False 时额外明确「标记 → 答案 → 解析」的顺序，是为了消除
    与第 6 条「客观题必须在 answer_markdown 第一行输出答案」的冲突：模型若只输出
    答案字母而漏掉标记，原卷自带的客观题答案同样会被静默清空。
    """
    if generate_answers_bool:
        return (
            "【答案提取与生成规则】:\n"
            "- 若原试卷自带答案，请完整提取并在 `answer_markdown` 开头标明 `[EXTRACTED_ORIGINAL]`。\n"
            "- 若原试卷缺答案，请自动推导生成标准解答步骤填入 `answer_markdown`。"
        )
    return (
        "【答案提取规则（严禁主动生成，违反即整段作废）】:\n"
        "- 仅提取原试卷中明确自带的原版参考答案与解析，并在 `answer_markdown` 开头标明 `[EXTRACTED_ORIGINAL]`。\n"
        "- 若原试卷无答案，必须将 `answer_markdown` 设为空字符串 \"\"，绝对不要现场推导或编造答案！\n"
        "- 自行推导的解析会在落库前被系统整段清空：写出来只会白白消耗输出长度，"
        "还可能挤掉后面的题目，等于既浪费 token 又丢题。\n"
        "- 顺序固定为「`[EXTRACTED_ORIGINAL]` 标记 → 答案（客观题写选项字母或数值）→ 解析」，"
        "标记必须在最前面。原卷自带的客观题答案同样属于「原卷自带答案」，照常提取并加标记，不得丢弃。"
    )


def build_pdf_parse_system_prompt(curriculum: dict, generate_answers_bool: bool, separated_mode: bool = False, formula_lock: bool = True, paper_title: str = "") -> str:
    curriculum_text = build_curriculum_text(curriculum)
    answer_rule = build_answer_rule(generate_answers_bool)

    # 公式协议分支：
    #  - DOCX / TeX 路径在调用 parse 前已用 lock_visible_math 将公式锁定为
    #    <mathbank-math id="M1"> 标记，模型须输出 [[M1]] 占位符，服务端再还原；
    #  - PDF 路径不锁定公式（PDF 提取无锁定步骤）。若仍要求输出 [[M1]] 占位符，
    #    模型会把未锁定的公式 $ 误删导致公式丢失，故 PDF 须改为「主动用 $...$ 包裹」。
    if formula_lock:
        formula_lock_section = (
            "5. 公式锁定：输入中 `<mathbank-math id=\"M1\">公式</mathbank-math>` 是只读公式标记，"
            "输出时必须把每个标记替换为对应 `[[Mn]]`（M1→[[M1]]），严禁输出公式本体 LaTeX、删除/改名标记、或把一个 id 用两次。"
            "示例：`全集 <mathbank-math id=\"M1\">$U=\\{1,2\\}$</mathbank-math>` → 输出 `全集 [[M1]]`。\n"
        )
    else:
        formula_lock_section = (
            "5. 公式包裹：本卷公式未锁定，输出时必须把每个数学公式/符号用 `$...$`（行内）或 `$$...$$`（独立）完整包裹，"
            "严禁丢 `$`、严禁写成纯中文、严禁用 `[[Mn]]` 占位。示例：`全集 U={1,2}` → 输出 `全集 $U=\\{1,2\\}$`。\n"
        )

    system_instructions = (
        "你是资深高中数学教研专家，把试卷源码切分为题目列表 JSON。\n\n"
        f"【学段受控取值（compulsory 只能填这些完整书名）】: {'、'.join(list(curriculum.keys())) if isinstance(curriculum, dict) else '（见教材范围顶层书名）'}\n"
        f"【教材范围（chapter 只能填以下章节名）】:\n{curriculum_text}\n"
        "【拆题规范】\n"
        "1. 字段：compulsory=学段受控取值中的完整书名；chapter=教材范围内精确章节名；question_type=single_choice/multi_choice/fill_in_blank/detailed_answer；difficulty=easy_error/normal/challenge/qiangji；source=题目来源，必须严格遵循下方【题目来源（source）命名规约】，无法确定填空字符串（严禁 LaTeX/TikZ 片段）。\n"
        "2. 题干与选项：content 仅去掉开头题号（如 `1.`、`2.` 或 `(1)` 这类编号本身），**严禁遗漏**紧跟其后的题源括注（如 `(2025·天津卷·★)`、`(2024·四川遂宁模拟·★★)`）——必须从 content 中抽到 `source` 字段并从 content 内删除，原卷若只有卷名（如「天津卷」「全国甲卷」「四川遂宁模拟」）则按下方规约补 `·高考真题` 等后缀。判断依据：题源外层是「(…)」或「（…）」，紧跟题号；其它括号（如 `$(\\{1,2\\})$」）属于正文，禁止动。选择题 A./B./C./D. 选项必须**全部原样**保留在 content，用 `\\begin{choices}\\item…\\end{choices}`，每项独立一行，严禁删选项或搬进 answer_markdown。\n"
        "2.1 【样例-剥离题源】原文 `1.（2025·天津卷·★）设 $x\\in\\mathbf{R}$…`，输出 content `设 $x\\in\\mathbf{R}$…`，source `2025·天津卷·高考真题`（题号 `1.` 也要脱掉）；题源不是正文绝不是文案！\n"
        "2.2 【样例-答案匹配】原文末尾有 `7. D\\n解析：若能将…` 这种答案区时，对应 7 题的 answer_markdown 必须为 `[EXTRACTED_ORIGINAL]\\nD\\n若能将…`（首行答案、后接解析），绝对不要把答案留空也不要自行推导；没找到原卷答案才是留空。\n"
        "3. 标签：knowledge_list（知识点数组）、solve_method（核心解题方法一个）、tags（主题标签数组）、related_chapters（关联章节数组，单章题 []）。\n"
        f"3.1 {CLASSIFICATION_PRIORITY_RULE}\n"
        "3.2 【样例-教材定位】compulsory 与 chapter 必须**逐字**取自上方受控列表：一道「基本不等式」题 → compulsory `必修一`、chapter `2. 一元二次函数、方程和不等式`（含编号前缀）。**严禁**输出列表外写法（如「必修第一册」「选择性必修一」「专题三」或教辅自拟专题名）；确实无法判断时才留空字符串。\n"
        "4. 忠实保留：100% 保留题干汉字与「（如图）」等指代；图片链接原样保留并记入 referenced_images；`[公式待核对]` 等标记原样保留，不得猜测补写。\n"
        + formula_lock_section +
        "6. 排版：填空题下划线用 `\\fillin`；加粗用 `\\textbf{}`（禁 `**`）；几何顶点/变量用 `$...$`（禁单个 `*` 斜体）；不同小问/步骤间空行分隔。\n"
        "7. 符号与公式规范：仅对含义明确的 Unicode 数学字符与结构（如 √、∈、α、β 以及分子/分母边界清晰的分式）规范化为等价 LaTeX 语法（如 `\\sqrt{...}`、`\\frac{...}{...}`、`\\in`、`\\alpha`）。不得将普通字母 `j`、`p` 等按语境猜成希腊字母或分式；不得根据题意自行重建原文中已损坏、缺失或标记待核对的公式。\n"
        "8. PDF 跨页协议：`<!-- MATHBANK_PDF_PAGE:N -->` 仅表示后续原文来自 PDF 第 N 页，用于来源追踪，不是题目边界，也不得出现在输出题干中。若一道题的题干、公式、表格、选项或解析跨越页标，必须按上下文合并为同一道完整题目，禁止按页拆成两题。\n"
        "9. 客观题答案：answer_markdown 第一行先给最终答案（选项字母或数值）再给解析；题干夹带答案擦除为纯净占位。\n"
        f"{answer_rule}\n\n"
    )

    # 题源规约：由 source_normalize 的单一事实源动态拼装，杜绝「代码里一套规约、
    # 提示词里另一套」的漂移（历史上提示词曾要求模型删除分隔符，与规约相反）。
    # 传入 paper_title 时明确要求各分段基于同一标题推导：分批拆解每 8 题一段，
    # 每段各自猜测会让同一份试卷裂成多个来源。
    system_instructions = system_instructions + "\n" + build_source_rule_prompt() + "\n"
    if paper_title and str(paper_title).strip():
        system_instructions += (
            f"\n【本卷来源唯一依据】本次拆解的试卷标题是「{str(paper_title).strip()}」。\n"
            "所有题目的 source 都必须**由这个标题推导**，且**每一题输出完全相同的值**："
            "本卷会被切成多段分别处理，各段自行猜测会让同一份试卷裂成多个来源。\n"
        )

    if separated_mode:
        separated_rule = (
            "【分离式文档（题目前、解析后）】: 前半是纯题干、后半是按题号排列的解析。先提取题干题号序列，"
            "再把每段解析按题号绑定到对应题，填入 answer_belongs_to（题号字符串）；找不到对应段的题 answer_markdown 设为空、answer_belongs_to 为 null 并在 source 末尾加「[缺解析]」。\n"
        )
        system_instructions = system_instructions + separated_rule + "\n"

    system_instructions = system_instructions + (
        "【全量输出（防漏题）】无论分几段，必须把本段全部题目完整输出，禁止只输出前几题。\n\n"
        "【输出】只输出严格合法 JSON（不要 ```json 代码块）；字符串内部换行必须输出 JSON 转义序列 `\\n`（反斜杠+n），LaTeX 命令的反斜杠必须按 JSON 规范转义为双反斜杠 `\\\\`。字段：\n"
        '{"questions":[{"content":"","answer_markdown":"","question_type":"","compulsory":"","chapter":"","difficulty":"","source":"","knowledge_list":[],"solve_method":"","related_chapters":[],"tags":[],"referenced_images":[],"answer_belongs_to":null}]}\n'
    )
    return system_instructions


def build_import_parse_system_prompt(
    curriculum: dict,
    generate_answers_bool: bool = False,
    separated_mode: bool = False,
    paper_title: str = "",
) -> str:
    """Prompt for pasted and uploaded single-file TeX paper parsing.

    generate_answers_bool 必须由前端「自动生成 AI 解答」开关传入：此前这里硬编码
    False，导致该路径下勾选开关也完全不生效（模型从没被要求生成解析）。
    separated_mode 由后端 detect_separated_mode 自动判定（题目与解析分离结构）。
    """
    return build_pdf_parse_system_prompt(
        curriculum,
        generate_answers_bool=generate_answers_bool,
        separated_mode=separated_mode,
        paper_title=paper_title,
    ) + (
        "\n【单文件 TeX 源码专项规则】:\n"
        "1. 输入已由本地预处理器提取 document 正文并清除普通注释；不得把 documentclass、usepackage、页眉页脚或宏定义上下文当成题目。\n"
        "2. 识别 question/problem/exercise/enumerate/item、parts/subparts/part、choices/choice/CorrectChoice、tasks/task 等常见结构。每个顶层题目只输出一次，小问必须保留在所属大题内。\n"
        "3. `[MATHBANK_ORIGINAL_CORRECT]` 只表示原卷标注的正确选项：将其转换为带 `[EXTRACTED_ORIGINAL]` 的答案字母，题干 choices 中不得保留该标记。\n"
        "4. solution/answer/analysis/proof 等原卷答案环境必须关联到前一道题并标记 `[EXTRACTED_ORIGINAL]`，不得把答案误拆成新题。\n"
        "5. `[MATHBANK_TEX_MACRO_CONTEXT_BEGIN/END]` 之间仅是未展开宏的定义上下文。应将正文中的自定义宏转换为等价标准 LaTeX，不得把上下文或未定义宏原样写进题干。\n"
        "6. 完整保留 tabular/array/matrix/cases/aligned 等数学和表格结构；TikZ 源码不得擅自改写，若无法安全转为题干插图则原样保留并提示人工核对。\n"
        "7. includegraphics 的原始文件名必须同时放入 referenced_images；不要虚构、改名或丢弃图片引用。\n"
        "8. input/include 指向的外部子文件在单文件模式下不可读取，不得根据文件名猜测缺失题目。\n"
    )


def build_latex_error_explanation_prompts(diagnostic: dict) -> tuple[str, str]:
    """Build a privacy-minimized prompt for explaining one compile error."""
    system_prompt = (
        "你是一名高中数学试卷 LaTeX 排版故障解释助手。"
        "请把编译错误解释成普通教师能看懂的中文，并给出可操作的修复方法。"
        "只能依据提供的错误和局部源码判断，不得编造不存在的题目内容、宏包或文件。"
        "不要重写整份试卷，不要改变数学含义。"
        "必须只返回严格 JSON 对象，字段为 summary、cause、location、fixes、package、command；"
        "fixes 必须是 1 至 4 条短句组成的数组。"
    )
    user_prompt = (
        "请解释下面这一个 XeLaTeX 编译错误：\n"
        f"本地初步判断：{diagnostic.get('summary', '')}\n"
        f"技术错误：{diagnostic.get('technical_error', '')}\n"
        f"疑似命令：{diagnostic.get('command', '')}\n"
        f"疑似宏包：{diagnostic.get('package', '')}\n"
        f"位置：{diagnostic.get('location', '')}\n"
        "出错位置附近的最小源码片段：\n"
        f"{diagnostic.get('source_context', '')}"
    )
    return system_prompt, user_prompt


def build_tikz_draw_prompt(latex_content: str = "", multimodal: bool = True) -> str:
    """Build the prompt used to reconstruct a question illustration as TikZ."""

    stem = latex_content or "暂无题干"
    if multimodal:
        return (
            "你是一个 LaTeX/TikZ 几何绘图专家。下面第一张图是从试卷题目中分割裁剪出来的几何插图局部。\n"
            "另外，这道数学几何题目的完整题干文本如下，请务必作为绘图逻辑参考：\n"
            f"```latex\n{stem}\n```\n"
            "请使用标准的 LaTeX TikZ 几何绘图语言，将这幅几何图形高精度重新绘制一遍。\n"
            "【绘图重要规范与自愈提示】：\n"
            "1. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分）。请确保不输出任何与代码无关的闲聊、问候或说明文字。\n"
            "2. 结合题干文本（如提及线线垂直、线面平行以及几何点的真实名称）来理解和校正剪切图中可能缺失、磨损或由于裁剪漏掉的字母。例如，如果题干提到 PA 垂直于面 ABC，但插图顶部顶点上面没有字母，请根据题意在顶部顶点标注为 'P'（绝对不要随意编造非题干中提及的字母，如 D 等）。\n"
            "3. 仔细识别并使用 `\\node` 或 `label` 标在对应的物理位置。重要被遮挡线条请使用 `dashed` 虚线绘制。不要在大片空白处留多余线头。"
        )

    return (
        "你是一个 LaTeX/TikZ 几何绘图专家。已知有一道数学几何题目，其文字描述和公式如下：\n"
        f"```latex\n{stem}\n```\n"
        "请仔细分析该题目中各几何元素之间的逻辑关系（如线面垂直、平行、坐标位置、夹角等），"
        "编写出一段最精确、美观的 LaTeX TikZ 代码来绘制这道题目的示意插图。\n"
        "【绘图重要规范】：\n"
        "1. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分）。请确保不输出任何与代码无关的闲聊或说明文字。\n"
        "2. 绘图比例和字母标注位置要协调美观，重要被遮挡线条请使用 `dashed` 虚线绘制。"
    )


def build_tikz_correction_prompt(
    tikz_code: str,
    user_guidance: str = "",
    compile_error_log: str = "",
    rendered_comparison: bool = False,
) -> str:
    """Build either the visual-comparison or compile-error TikZ repair prompt."""

    if rendered_comparison:
        prompt = (
            "你是一个 TikZ 几何绘图专家。对比 Image A（原题目插图）与 Image B（TikZ 当前渲染图）：\n"
            "```latex\n"
            f"{tikz_code}\n"
            "```\n"
            "任务：对比拓扑与细节差异（点位置、实虚线、字母、箭头等），修改 TikZ 代码使其 100% 还原 Image A。\n"
            "必须使用 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture}...\\end{tikzpicture}），严禁输出闲聊文字。"
        )
        if user_guidance.strip():
            prompt += f"\n\n【人工修改意见】：请务必优先遵循：{user_guidance.strip()}"
        return prompt

    prompt = (
        "你是一个 LaTeX/TikZ 几何绘图专家。下面第一张图是原始题目的正确几何插图。\n"
        "我试图用 TikZ 绘制它，但我的代码在编译时报错了。\n"
        "这是我当前编写的代码：\n"
        "```latex\n"
        f"{tikz_code}\n"
        "```\n"
        f"编译器的具体报错日志如下：\n```text\n{compile_error_log}\n```\n"
        "请完成以下任务：\n"
        "1. 结合原始图片以及报错日志，找出代码中的语法错误或逻辑死循环。\n"
        "2. 修正这些语法错误，使其能通过 LaTeX 编译，并精准绘制出原始图中的几何图形。\n"
        "3. 你的回答必须以 ```latex ... ``` 代码块包裹修正后的完整 TikZ 代码（只输出 \\begin{tikzpicture} 和 \\end{tikzpicture} 之间的部分，或者包含它们）。请确保不输出任何与代码无关的开场白或闲聊文字。"
    )
    if user_guidance.strip():
        prompt += (
            "\n\n【人工修改和纠错指导意见】：\n用户指出了当前图形的以下具体错误或修改意见，请你在生成修正代码时务必优先且绝对遵循这一意见：\n"
            f"{user_guidance.strip()}"
        )
    return prompt
