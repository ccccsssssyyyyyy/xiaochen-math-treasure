"""生成选项归一化的 Python 端基准输出，供 tools/choice_normalize_js_test.js 做前后端一致性比对。

用例必须与 JS 测试中的 parityCases 保持完全一致，否则比对无意义。
用法：
    cd <项目根> && venv/bin/python tools/_choice_parity_py_baseline.py
输出：/tmp/_choice_parity_py.json
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mathbank.latex_normalize import normalize_choice_options_to_latex  # noqa: E402

CASES = [
    'A. $a>0>b$ B. $a>b>0$ C. $b>a>0$ D. $b>0>a$',
    '\\begin{choices}\\item A. $x>0$\\item B. $x<0$\\end{choices}',
    '（A）$a>0$（B）$a<b$（C）$a=b$（D）无法比较',
    '解：(1) 当 x>0 时，f(x)=x^2；(2) 当 x<0 时，f(x)=-x。',
    '令 f(A)=x，则 g(B)=y，其中 A、B 为集合。',
    '已知 x>0，A. $x>1$ B. $x>2$ C. $x>3$ D. $x>4$ 答案：B',
    '则（ ）（若随机变量 $Z$ 服从正态分布 $N(\\mu,\\sigma^2)$） A. $P(X>2)>0.2$ B. $P(X>2)<0.5$ C. $P(Y>2)>0.5$ D. $P(Y>2)<0.8$',
    '已知集合 $A=\\{1,2\\}$，$B=\\{2,3\\}$，则 $A\\cap B=$（　　）A. $\\{2\\}$ B. $\\{1,2\\}$ C. $\\{2,3\\}$ D. $\\{1,2,3\\}$',
    'A、$1$ B、$2$ C、$3$ D、$4$',
    '(A) $x>0$ (B) $x<0$',
    '',
    '纯文本内容没有选项',
    r'$\chi^{2}=\frac{n(ad-bc)^{2}}{(a+b)(c+d)(a+c)(b+d)}$，其中 $n=a+b+c+d$。',
]

if __name__ == "__main__":
    out = [normalize_choice_options_to_latex(c) for c in CASES]
    with open('/tmp/_choice_parity_py.json', 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False)
    print('已生成基准：%d 例 -> /tmp/_choice_parity_py.json' % len(out))
