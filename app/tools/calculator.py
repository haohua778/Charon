"""Small AST arithmetic interpreter. No eval, exec, names, calls, or exponentiation."""
from __future__ import annotations

import ast
import math
import operator

from app.core.errors import CharonError

MAX_LENGTH = 160
MAX_NODES = 64
MAX_OPERATIONS = 16
MAX_MAGNITUDE = 1_000_000_000_000
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub,
             ast.Mult: operator.mul, ast.Div: operator.truediv}


def calculate(expression: str) -> int | float:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > MAX_LENGTH:
        raise CharonError('invalid_tool_expression')
    try:
        root = ast.parse(expression.strip(), mode='eval')
        if sum(1 for _ in ast.walk(root)) > MAX_NODES:
            raise ValueError('Too many nodes')
        operation_count = 0

        def bounded(value: int | float) -> int | float:
            if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > MAX_MAGNITUDE:
                raise ValueError('Out of bounds')
            return value

        def visit(node: ast.AST) -> int | float:
            nonlocal operation_count
            if isinstance(node, ast.Constant):
                return bounded(node.value)
            if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
                operation_count += 1
                if operation_count > MAX_OPERATIONS:
                    raise ValueError('Too many operations')
                return bounded(OPERATORS[type(node.op)](visit(node.left), visit(node.right)))
            if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
                operation_count += 1
                if operation_count > MAX_OPERATIONS:
                    raise ValueError('Too many operations')
                value = visit(node.operand)
                return bounded(value if isinstance(node.op, ast.UAdd) else -value)
            raise ValueError('Unsupported syntax')

        return visit(root.body)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError) as exc:
        raise CharonError('invalid_tool_expression') from exc
