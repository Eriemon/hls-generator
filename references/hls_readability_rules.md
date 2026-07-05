# HLS Readability Gate Rule Catalog

The HLS readability gate adapts maintainability ideas from readable governance into C/C++ HLS code. HLS artifacts use `HGxxx` so generated reports can distinguish HLS-specific checks.

## Rule list

| Rule | Severity default | Scope | Summary |
|---|---:|---|---|
| HG000 | error | target / parse | Target path missing, no HLS files, empty translation unit, or selected parser reports syntax failure. |
| HG001 | error | comments | HLS comments must be Chinese except recognized tool directives such as `NOLINT`, `clang-format`, `IWYU pragma`, license, or copyright lines. |
| HG002 | error | spacing | A blank line separating code blocks requires a Chinese purpose comment immediately before the lower block. |
| HG003 | error | statements | Special HLS statements require one blank line plus an adjacent Chinese purpose comment above them. Includes, macros, typedefs, function signatures, calls, loops, branches, `return`, `assert`, and `#pragma HLS` are covered. |
| HG004 | error | local state | Local declaration or assignment lines require an above-line Chinese purpose comment describing the state, buffer, datapath, or transaction role. |
| HG005 | error | local state | Single-line local declaration or assignment lines require a right-side Chinese purpose comment. Long template-heavy or multi-line declarations may be exempted by profile, but HG024 must still flag the multi-line construct for contract review. |
| HG006 | error | comments | Generic, template, stale, too-short, or empty comments are forbidden. Examples include “定义变量”, “保存结果”, “处理数据”, “下方代码”, “generated code”, and similar placeholder phrases. |
| HG007 | error | file contract | File header comment is missing, non-Chinese, or too vague to identify source/header/testbench/kernel role. |
| HG008 | error | function contract | Top/helper function contract is missing or vague. Public function comments must explain role, transaction scope, port/parameter meaning, return value, side effects, or hardware intent as applicable. |
| HG009 | error | pragma | `#pragma HLS` comments must explain hardware intent: interface/port/protocol/bundle/control, II/latency/throughput, DATAFLOW stages, stream depth, or array partition/reshape dimension/factor. |
| HG010 | error | loops | Loop comments must include iteration boundary, transaction range, read/write objects, accumulation/comparison purpose, token/sample range, or throughput intent. |
| HG011 | error | testbench | Testbench comments must explain top-call transaction, expected output, PASS/FAIL condition, and vector hash/reference-vector binding when present. |
| HG012 | error | comment-only proof | Comment-only rewrite changed non-comment tokens or normalized AST fingerprint. |
| HG013 | error | comment-only proof | Baseline comparison cannot be proven because baseline file is missing or no AST provider is available. |
| HG014 | error | naming | HLS identifier name is vague or violates profile. The gate rejects names such as `data`, `tmp`, `result`, `value`, `buf`, unless scope-specific context justifies them. Stream, AXIS, buffer, accumulator, address, length, index, and constant names must carry domain meaning. |
| HG015 | error | top-port contract | Top function port contract lacks direction, protocol, depth, shape, unit, bundle, stream depth, or side-effect details. |
| HG016 | warning/error | structure | Function is longer than the profile threshold. |
| HG017 | warning/error | structure | Nesting depth is above the profile threshold. |
| HG018 | warning/error | structure | Branch/loop count is above the profile threshold. |
| HG019 | warning/error | constants | Magic number cluster lacks named constants or an explanatory comment. |
| HG020 | warning/error | comments | Oversized block of commented-out old code remains in the artifact. |
| HG021 | error | synthesis risk | Dynamic allocation, recursion-like function pointer use, thread/process constructs, system calls, exceptions in synthesizable source, or other high-risk non-synthesizable C++ structures are present. |
| HG022 | error | dataflow/stream | DATAFLOW/STREAM channel comments lack FIFO depth, producer-consumer relation, or stage overlap explanation. |
| HG023 | error | pragma/ports | Interface pragma contradicts inferred port role, for example an input pointer assigned an output-only interface comment. |
| HG024 | warning | line/AST bridge | Multi-line declaration or function signature may escape line-based comment checks. Add a nearby contract or keep a single-line declaration if practical. |

## HLS-specific adaptations

HLS code does not mechanically reuse Python typed-prefix or docstring rules. Instead, this catalog maps general readability ideas to HLS concepts:

- Inline purpose comments become HG005 for single-line local C/C++ declarations and assignments. HLS often uses long template types, arrays, and streams, so profiles can exempt long or multi-line declarations from right-side comments. The exemption is not silent: HG024 keeps the construct visible to reviewers.
- Naming discipline becomes HG014 HLS-aware naming. `i/j/k` are allowed as tight loop indexes, but streams should include stream/channel semantics, AXIS packets should include axis/word/token/packet semantics, accumulators should include acc/sum plus the accumulated domain, and arrays/buffers should carry shape or role hints.
- Public contract comments become HG007/HG008/HG015/HG009/HG010/HG011 contracts covering file role, function role, top ports, pragmas, loops, and testbench acceptance.

## AST provider behavior

The provider order is Clang, tree-sitter-cpp, then pycparser. Fake HLS headers cover `ap_int`, `ap_fixed`, `hls::stream`, AXIS words, `hls::task`, common C headers, and basic standard-library shims.

For comment-only baseline comparison, lack of an AST provider is an error because behavior preservation cannot be proven. For general readability checks, line/token rules still run and the report records `ast_provider_unavailable=true`; AST-backed structure and contract precision may degrade in that mode.
