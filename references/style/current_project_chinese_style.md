# Current-project Chinese Python style

This style overlay is mandatory when generating Python for the current project or when the user asks to match the supplied readable research code.
It sits on top of the semantic profile (`library`, `scientific`, `script`, `cli`, `notebook`, `test`, or `refactor`).
The machine-readable overlay is stored in `references/style/current_project_style_config.json` and is consumed by `scripts/python/quality_gate/profiles.py` when `--style current-project` is used.

## Design intent gate

Before any Python code design task, state the functional-design default notice from `design_intent.required_notice`.
Treat ordinary write, modify, design, and implement requests as readability-first functional design.
Only switch to performance-optimization mode when the user explicitly asks for speed, memory, throughput, concurrency, async, multiprocessing, multithreading, very large data, or algorithmic complexity work.
Before any performance-oriented rewrite, disclose likely optimization directions, consequences, and refactor risks, then ask for confirmation.
Without confirmation, provide only suggestions, risk notes, and option comparisons.

## Hard writing rules

1. All generated Python comments must be Chinese, except tool pragmas such as `# noqa`, `# type: ignore[...] 原因: ...`, encoding headers, and shebang lines.
2. Public docstrings should be Chinese. Scientific docstrings must still include `shape`、`dtype`、`unit` and numerical risk.
3. When a blank line separates two code blocks, including a blank line after a docstring, the lower block must begin with a standalone Chinese comment explaining the block below.
4. Function calls, assignments whose right side calls a function, `with`, `try`, `assert`, `for`, `if`, `while`, and `return` statements must have a blank line above them. Usually place a Chinese block comment between that blank line and the statement.
5. Variable definitions and assignments, including augmented assignments such as `total += value`, must have one blank line plus a standalone Chinese purpose comment above them, and a right-side Chinese comment on the assignment line.
6. Assignment comments must explain the variable's function, purpose, business meaning, or mathematical meaning. Avoid fixed placeholders such as `# 定义xxx`、`# 保存结果`、`# 计算结果`, generated step comments such as `# 判断当前分支` or `# 执行 xxx 对应的当前步骤`, and noun-only comments such as `# 值` or `# 代码`.
7. Public function docstrings must explain each parameter and return value in context; placeholder lines such as `filepath: 说明 filepath 在当前函数中的输入含义。` or `返回当前函数计算、收集或组装得到的结果。` are not acceptable.
8. Critical comments must be specific to the current variable or lower code block; they must show understanding of why the code exists, not merely restate that code exists.
9. Do not stack long runs of code. Keep at most eight consecutive physical code lines before inserting a blank line and a Chinese explanation comment.
10. Keep explicit intermediate variables. Do not collapse matrix or tensor formulas just to save lines.
11. Comment generation is semantic work, not template rendering: understand the function contract, variable role, branch condition, loop collection, call side effect, and return value before writing the comment. If the code meaning is unclear, leave the comment out or ask/report for context instead of writing a placeholder.
12. Deterministic scripts may only locate comment rewrite targets, preserve special comments, report removable ranges, and provide recheck commands; they must not generate or write `suggested_comment`, `template_comment`, `replacement_text`, or fixed replacement comments.
13. Forbidden template families include “当前函数处理的 xxx 输入值”, “返回当前流程整理好的结构化处理结果”, “承接 xxx 前一阶段并进入后续处理分支”, “准备 xxx 供后续逻辑使用”, “返回 xxx 供上层流程继续使用”, “输出命令行可读的执行结果”, “用于当前检查步骤的判定”, “交由调用方处理”, “执行当前语句要求的外部可见副作用”, “记录当前分支确认的候选项”, “处理当前候选项命中集合的分支”, “根据 xxx 决定是否进入该分支”, “返回[]”, “供 xxx 后续判断使用”, “判断 xxx 是否满足当前规则条件”, “按条件结果选择对应的规则处理路径”, and “说明下方代码承担的规则判断或报告登记职责”.

Right-side Python comments are allowed only for assignment, constant, and multi-line data-structure semantic element lines when they explain the value's real purpose in Chinese. Do not add trailing comments that merely restate the expression, mark an obvious type, or copy a template phrase.

## Preferred block shape

```python
"""线性系统辅助函数。"""

from __future__ import annotations

# 数值库
import numpy as np


# 单步推进函数
def advance_state(matrix_a: np.ndarray, vector_x: np.ndarray, dt: float) -> np.ndarray:
    """
    使用显式欧拉法推进状态。

    :param matrix_a: 系统矩阵，shape=(n, n)，dtype=float64，unit=1/s
    :param vector_x: 状态向量，shape=(n, 1)，dtype=float64，unit=state unit
    :param dt: 时间步长，dtype=float，unit=s
    :return: 下一步状态，shape=(n, 1)，dtype=float64，unit=state unit
    """

    # 校验系统矩阵是否为二维方阵
    if matrix_a.ndim != 2 or matrix_a.shape[0] != matrix_a.shape[1]:

        # 阻止非方阵进入线性状态推进公式
        raise ValueError("matrix_a 必须是方阵")

    # 计算当前状态在系统矩阵作用下的导数
    derivative = matrix_a @ vector_x  # 状态导数，shape=(n, 1)

    # 计算显式欧拉格式得到的下一步状态
    next_state = vector_x + dt * derivative  # 下一步状态，shape=(n, 1)

    # 返回供调用方继续积分或记录的状态向量
    return next_state
```

## Gate command

Use the overlay explicitly during validation:

```bash
python scripts/python/quality_gate/run_quality_gate.py path/to/python --profile scientific --style current-project
```

The overlay detects PG030-PG037. Strict generation can block on the full set, while repository-wide governance keeps layout-only and legacy coverage findings as warnings so existing code is not polluted with mechanical comments. PG031 covers ordinary blank-line code blocks and blank lines after docstrings, but PG032 special statements do not need an added block comment. PG032 covers special statement spacing, PG033 covers right-side assignment comments, PG035 covers blank-line-plus-comment assignment blocks, PG036 catches generic fixed comments, generated step comments, and template families produced by comment-generation workflows. PG037 rejects vague or too-short purpose comments. In strict docstring profiles, PG044 also rejects parameter, return, and exception placeholder sentences.
