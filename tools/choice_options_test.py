"""单元测试：normalize_choice_options_to_latex

覆盖：内联选项包裹、已有 choices 去显式标号、全角括号、非选择题不改、
幂等性、以及"答案/解析"标记截断。
"""

from mathbank.latex_normalize import normalize_choice_options_to_latex


def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name)
    if not cond:
        raise SystemExit(f"测试失败: {name}")


# 1. 内联选项（用户截图中的典型形态）包裹为 choices 环境，且 \item 内无显式标号
inline = r"A. $a>0>b$ B. $a>b>0$ C. $b>a>0$ D. $b>0>a$"
out1 = normalize_choice_options_to_latex(inline)
check("内联选项包裹为 \\begin{choices}", out1.strip().startswith(r"\begin{choices}") and out1.strip().endswith(r"\end{choices}"))
check("内联选项生成 4 个 \\item", out1.count(r"\item") == 4)
check("内联选项 \\item 内不含显式 A./B. 标号", "A. " not in out1 and "B. " not in out1 and "D. " not in out1)
check("内联选项保留数学正文", "$a>0>b$" in out1 and "$b>0>a$" in out1)

# 2. 已用 choices 但 \\item 内残留显式标号 -> 去标号（避免双重编号）
with_label = r"\begin{choices}\item A. $x>0$\item B. $x<0$\end{choices}"
out2 = normalize_choice_options_to_latex(with_label)
check("choices 内去显式标号", "A. " not in out2 and out2.count(r"\item") == 2)

# 3. 全角括号选项 （A）（B）... 包裹
fullwidth = "（A）$a>0$（B）$a<b$（C）$a=b$（D）无法比较"
out3 = normalize_choice_options_to_latex(fullwidth)
check("全角括号选项包裹为 choices", out3.strip().startswith(r"\begin{choices}") and out3.count(r"\item") == 4)
check("全角选项去 （A） 标号", "（A）" not in out3)

# 4. 非选择题（解答题含 (1)(2) 与小问）不被误伤
solve = "解：(1) 当 x>0 时，f(x)=x^2；(2) 当 x<0 时，f(x)=-x。"
out4 = normalize_choice_options_to_latex(solve)
check("解答题内容保持不变", out4 == solve)

# 5. f(A) 等数学内部括号不被误判为选项
mathonly = r"令 f(A)=x，则 g(B)=y，其中 A、B 为集合。"
out5 = normalize_choice_options_to_latex(mathonly)
check("数学内部 (A) 不被误判", out5 == mathonly)

# 6. 选项后紧跟"答案：A"时被正确截断，不吞入答案
inline_ans = r"已知 x>0，A. $x>1$ B. $x>2$ C. $x>3$ D. $x>4$ 答案：B"
out6 = normalize_choice_options_to_latex(inline_ans)
check("选项后答案标记被截断", "答案：B" not in out6 and out6.count(r"\item") == 4)

# 7. 幂等性：两次归一化结果一致
out7a = normalize_choice_options_to_latex(out1)
out7b = normalize_choice_options_to_latex(out7a)
check("幂等性", out7a == out7b)

# 8. 空 / 非字符串安全
check("空字符串", normalize_choice_options_to_latex("") == "")
check("None 安全", normalize_choice_options_to_latex(None) == "")

# 9. 公式分母 (a+b)(c+d)(a+c)(b+d) 不被误判为选项（数学模式保护）
formula = r"$\chi^{2}=\frac{n(ad-bc)^{2}}{(a+b)(c+d)(a+c)(b+d)}$，其中 $n=a+b+c+d$。"
out9 = normalize_choice_options_to_latex(formula)
check("公式内 (a+b) 等不被误判为选项", out9 == formula)
check("公式不出现 \\begin{choices}", "\\begin{choices}" not in out9)

print("\nALL PASS: normalize_choice_options_to_latex")
