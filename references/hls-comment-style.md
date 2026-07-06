# HLS C/C++ 中文注释与留白规范

Load this reference before changing prompt rules, mock HLS output, vector-driven testbench output, or validation policy for generated comments.

## Default language

- Generated HLS C/C++ comments must be Chinese. Identifiers, Vitis/HLS tool names, protocol names, pragma keywords, bundle names, and case ids may remain in canonical English.
- `auto` resolves to `zh` by default. The runtime may retain the legacy `en` option for compatibility, but generated prompts, mock output, and validators enforce Chinese comments.

## Required spacing and placement

Generated HLS C/C++ must satisfy strict Chinese comment placement. The validator checks the structure being commented, not just the presence of `//`.

- File header: every `.h`, `.hpp`, `.cpp`, `.cc`, and `.cxx` file starts with a Chinese comment describing the concrete file role: interface declaration, kernel implementation, testbench, or similar.
- Blank-line blocks: whenever a blank line separates two code blocks, the lower code block starts with a standalone Chinese comment explaining the purpose of the code below.
- Special statements: function calls, assignments whose right side calls a function, `with`, `try`, `assert`, `for`, `if`, `while`, `return`, `#pragma`, includes, macros, type definitions, and function declarations/definitions must have a blank line above. In HLS code this normally means one blank line plus a standalone Chinese comment immediately above the statement.
- Variables: every local variable declaration or assignment has one blank line plus a standalone Chinese purpose comment above it. The comment explains the variable's function in the current datapath, protocol, test case, or math step; it must not say merely “定义变量”.
- Functions and methods: put the contract comment on the immediately preceding comment-only line. Explain the hardware boundary, top role, interface summary, helper-stage responsibility, or testbench entrypoint.
- `#pragma HLS`: put a standalone Chinese comment immediately above the pragma, not only a same-line comment. `INTERFACE` comments explain port/protocol/bundle/control intent. `PIPELINE` comments explain II, loop, latency, cycle, or throughput intent. `DATAFLOW` and `STREAM` comments explain stages, channels, FIFO depth, and producer/consumer buffering. `ARRAY_PARTITION` and `ARRAY_RESHAPE` comments explain dimension, factor, banking, and parallel access purpose.
- Loops: explain iteration bounds, transaction length, read/write object, token/sample range, accumulation, comparison, or check purpose. “遍历循环” alone is not sufficient.
- Testbenches: comment `main()`, vector hash, case setup, expected values, kernel calls, observed outputs, and PASS/FAIL reporting.
- Trivial lines: do not force comments onto pure braces and ordinary closing lines. Simple `return` is not trivial under this project style; it still needs spacing and a Chinese purpose comment.

## Comment quality

Comments must show understanding of the current code. They may be short, but they must be specific.

Blocking examples include fixed/template comments such as `定义xxx`, `定义变量`, `保存结果`, `初始化变量`, `计算结果`, `下方代码`, `说明下方代码`, `执行函数`, `调用函数`, `返回结果`, `判断当前分支`, `供后续逻辑使用`, `模板`, and `占位`.

Avoid vague noun-only labels such as `值`, `代码`, `结果`, `数据`, `逻辑`, `步骤`, `变量`, or `函数`. Prefer comments that mention the actual buffer, interface, loop bound, stream channel, sideband field, test case, or datapath responsibility.

## Comment-only rewrites

When a change only adds or rewrites comments, validation must prove that the executable HLS code did not change:

1. Strip comments while preserving string/character literals and compare non-comment token fingerprints.
2. Parse the baseline and commented files with the available open-source AST provider and compare normalized AST fingerprints.
3. Reject the rewrite if either token fingerprints or AST fingerprints differ.

The runtime implements this in `scripts/python/hls_quality_gate/hls_ast_guard.py`; see `references/hls-ast-comment-guard.md` for provider selection and fallback behavior.
