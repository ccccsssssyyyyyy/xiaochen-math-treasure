"""回归防护：模块级与类体注解引用的名字必须已绑定。

背景：Python <=3.13 会在 import 时**立即求值**模块级与类体的注解，
所以「注解里用了没导入的名字」会让整个模块 import 失败。Python 3.14
引入 PEP 649 后改为惰性求值，同一份代码在 3.14 上安然无恙——
于是本地（3.14）全绿、CI（3.10）在测试收集阶段就 11 个文件全炸。

真实案例：main.py 的 ``ChunkParseResult`` / ``DocParseResult`` 用了
``List[Any]`` 却没有 ``from typing import Any``，本地跑了几个月都没暴露。

本用例用 AST 静态分析跨版本拦住同类问题——不依赖运行哪个 Python。
"""

import ast
import builtins
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [REPO_ROOT / "main.py"] + sorted((REPO_ROOT / "mathbank").glob("*.py"))


def _bound_names(tree: ast.Module) -> set[str]:
    """收集模块内可用的顶层名字（导入 + 定义 + 赋值），近似但不保守。"""
    bound = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        bound.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return bound


def _unbound_in_annotation(annotation, bound) -> list[str]:
    if annotation is None:
        return []
    return [
        n.id for n in ast.walk(annotation) if isinstance(n, ast.Name) and n.id not in bound
    ]


@pytest.mark.parametrize("path", TARGETS, ids=lambda p: p.name)
def test_import_time_annotations_are_bound(path):
    """模块级注解与类体注解在 import 时求值，引用的名字必须已绑定。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound = _bound_names(tree)
    offenders: list[str] = []

    def record(annotation, where: str, lineno: int) -> None:
        for name in _unbound_in_annotation(annotation, bound):
            offenders.append(f"{path.name}:{lineno} [{where}] 未绑定的名字: {name}")

    # 模块级注解（含类定义本身，但类体要单独下钻）
    for stmt in tree.body:
        if isinstance(stmt, ast.AnnAssign):
            record(stmt.annotation, "module-level", stmt.lineno)

    # 所有类体的注解（含嵌套类）
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign):
                    record(stmt.annotation, f"class {node.name}", stmt.lineno)

    assert not offenders, (
        "以下注解在 Python <=3.13 上会导致 import 时 NameError"
        "（本地 3.14 因 PEP 649 惰性求值不会暴露）：\n  " + "\n  ".join(offenders)
    )


def test_guard_covers_expected_targets():
    """护栏覆盖面自检：至少要覆盖 main.py 与 mathbank 包。"""
    names = {p.name for p in TARGETS}
    assert "main.py" in names
    assert len([p for p in TARGETS if p.parent.name == "mathbank"]) >= 10
