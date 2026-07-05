# Readability rule catalog

The AST gate uses deterministic rules to catch code patterns that often make AI-generated Python hard to read or hard to maintain. Severity may be relaxed by profile, but `library`, `scientific`, and `test` keep strict interpretation. The `test` profile is a semantic label for test code, not an exemption from readability, docstring, type, path, print, or current-project style checks.

| Code | Default level | Rule |
|---|---:|---|
| PG000 | BLOCKER | Target missing, no Python files, or syntax error. |
| PG001 | BLOCKER | Mutable default argument. |
| PG002 | BLOCKER | Import-time side effect in library/scientific code. |
| PG003 | BLOCKER | Hardcoded GPU/CUDA/device selection. |
| PG004 | WARNING | `print` in library/scientific code. |
| PG005 | BLOCKER | `plt.show()` in library/scientific code. |
| PG006 | WARNING | Hardcoded path in core code. |
| PG007 | WARNING/BLOCKER | Large comment block or commented-out code. |
| PG008 | BLOCKER | Scientific array function lacks shape/dtype/unit docs. |
| PG009 | WARNING/BLOCKER | Function too long for profile. |
| PG010 | WARNING/BLOCKER | Nesting too deep for profile. |
| PG011 | WARNING/BLOCKER | Too many branches/loops for profile. |
| PG012 | WARNING/BLOCKER | Magic number cluster. |
| PG013 | WARNING/BLOCKER | Script logic mixed into core module. |
| PG014 | BLOCKER | Public API missing type annotations. |
| PG015 | WARNING | Random seed lacks reproducibility comment. |
| PG016 | WARNING | Wildcard import. |
| PG017 | WARNING | Duplicate public definition. |
| PG018 | WARNING | Nested loop variable reuse. |
| PG019 | WARNING | `type: ignore` lacks error code or reason. |
| PG020 | WARNING/BLOCKER | Overlong line. |
| PG021 | BLOCKER | Bare `except:`. |
| PG022 | WARNING | Swallowed broad exception. |
| PG023 | BLOCKER | Bare `assert` outside notebook exploration; generated tests should use framework assertions with clear expected behavior. |
| PG024 | WARNING/BLOCKER | Too many function parameters. |
| PG025 | WARNING | Public function/class lacks docstring in strict profiles. |
| PG026 | BLOCKER | `matplotlib.use(...)` at import time. |
| PG027 | WARNING | Direct comparison to `True` or `False`. |
| PG028 | WARNING | Direct `type(x) == SomeType` comparison instead of `isinstance`. |
| PG029 | WARNING | Config-like class should usually be a dataclass. |
| PG030 | BLOCKER with `--style current-project` | Comment or public docstring is not Chinese. |
| PG031 | BLOCKER with `--style current-project` | Blank-line-separated ordinary code block, including a break after a docstring, lacks a Chinese comment before the lower block. Special statements handled by PG032 do not require an extra block comment. |
| PG032 | BLOCKER with `--style current-project` | Special statement lacks required spacing above it: function call, assignment-with-call, `with`, `try`, `assert`, `for`, `if`, `while`, or `return`. |
| PG033 | BLOCKER with `--style current-project` | Normal, annotated, or augmented assignment lacks a right-side Chinese comment. |
| PG034 | BLOCKER with `--style current-project` | Dense code run exceeds the current-project maximum without spacing/comments. |
| PG035 | BLOCKER with `--style current-project` | Assignment lacks one blank line plus a standalone Chinese purpose comment above it. |
| PG036 | BLOCKER with `--style current-project` | Comment is a generic fixed placeholder, generated step sentence, reworded template, or script-produced comment family such as “供后续逻辑使用”, “交由调用方处理”, “用于当前检查步骤的判定”, “执行当前语句要求的外部可见副作用”, “按条件结果选择对应的规则处理路径”, or “说明下方代码承担的规则判断或报告登记职责” instead of a specific purpose explanation. |
| PG037 | BLOCKER with `--style current-project` | Assignment and blank-line block comments are too vague, too short, or noun-only to explain purpose. |
| PG038 | BLOCKER | Variable or assignment target does not use `snake_case`; module-level uppercase constants are exempt. |
| PG039 | BLOCKER | Statically inferred local variable type lacks its configured type prefix, including derived prefixes for explicit custom annotations. |
| PG040 | BLOCKER | Variable name uses vague standalone words such as `data`, `info`, `temp`, `result`, `value`, or `obj`. |
| PG041 | NOTE | Variable type cannot be statically determined. |
| PG042 | WARNING | Rename suggestion exists but falls outside the safe automatic rename subset. |
| PG043 | BLOCKER in strict docstring profiles | Module lacks a top-level docstring; empty `__init__.py` files are exempt. |
| PG044 | BLOCKER in strict docstring profiles | Public function docstring lacks required parameter, return, or exception explanation, or uses placeholder sentences such as “当前函数处理的 xxx 输入值” instead of real semantic detail. |
| PG045 | BLOCKER | Function, class, or module constant naming violates `snake_case`, `PascalCase`, or uppercase `SNAKE_CASE`. |

## Severity interpretation

- **BLOCKER** means the generated code should not be delivered unless the user explicitly accepts the exception.
- **WARNING** means the code may run, but the report must preserve the maintenance risk.
- **NOTE** is reserved for profile-specific advisory checks.

## Repository governance overlay

When the gate is used to govern the existing `erie-hls-generator` repository, `rule_runner.py` applies a compatibility overlay after the profile and style checks run. The overlay keeps semantic safety failures as blockers, including PG000 syntax/target failures, mutable defaults, hardcoded accelerator selection, bare `except:`, and directly targeted bad fixtures. It reports large legacy cleanup classes as warnings instead of blocking the whole repository: import-time layout debt, long functions, nesting, magic constants, broad naming cleanup, bare asserts, missing Chinese comments, missing docstrings, and similar readability backlog.

This distinction is intentional. New generated Python should still follow the stricter writing rules below, and repository-wide strict cleanup must use semantic edits rather than template comments or unsafe mechanical renames. Reports must preserve all WARNING and NOTE findings until a focused cleanup removes them with behavior-preserving changes.

## Project-specific emphasis

For the current scientific-code style, the most important rules are PG001, PG002, PG003, PG008, PG013, PG014, PG023, PG026, PG029, and PG030-PG045. They map directly to common failure modes in solver and experiment scripts: mutable defaults, import-time backend/device changes, missing array shape/unit documentation, mixed experiment configuration, hand-written parameter containers, non-Chinese comments, missing block comments after blank lines, squeezed special statements, missing right-side assignment comments, missing assignment purpose comments, generic fixed comments, generated template comments such as `声明 xxx 供当前模块复用`, `判断当前分支`, `准备 xxx 供后续逻辑使用`, `返回 xxx 供上层流程继续使用`, `记录当前分支确认的候选项`, `按条件结果选择对应的规则处理路径`, or `说明下方代码承担的规则判断或报告登记职责`, vague purpose comments, dense code blocks, unclear or placeholder docstrings, weak naming structure, and unsafe variable rename boundaries.

## Current-project style overlay

Run this overlay with:

```bash
python scripts/python/quality_gate/run_quality_gate.py path/to/python --profile scientific --style current-project
```

The overlay detects PG030-PG037 current-project comment issues. Strict generation paths may treat these as blockers, while repository governance normalizes layout-only and legacy-comment coverage gaps to warnings so the existing codebase can be migrated without fake comments or unsafe churn. PG036 and PG037 remain the hard line for generic, noun-only, too-short, or template-based comments. PG031 deliberately excludes PG032 special statements so agents do not add fake block comments merely to satisfy blank-line spacing. Strict docstring profiles also detect docstring placeholder sentences such as `说明 filepath 在当前函数中的输入含义`, `当前函数处理的 filepath 输入值`, and `返回当前流程整理好的结构化处理结果` as PG044 findings. Comment rewrite workflows may only report targets and must not emit replacement comment text for scripts to copy into source files.
