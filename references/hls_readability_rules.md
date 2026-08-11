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
| HG025 | error | typed-prefix | Top ports, ordinary parameters, local declarations, and assignment targets must use a typed-prefix derived from inferred type, payload family, or storage form. `ref_` is forbidden, and `alias_` may only appear as a secondary semantic token after the real typed-prefix. |
| HG026 | note | typed-prefix | The gate cannot reliably infer a typed-prefix family from the available type context, so it emits a manual-review note instead of inventing a rename. |
| HG027 | error | typed-prefix boundary | The required typed-prefix rename touches a public parameter, assignment target, or custom-type boundary that must be reviewed manually instead of auto-renamed blindly. |
| HG028 | error | print boundary | Human-facing HLS transcript output must use `> INFO: [HLS] ...`, `> WARNING: [HLS] ...`, or `> ERR: [HLS] ...`. Naked `PASS/FAIL`, raw `printf`, `fprintf(stdout/stderr, ...)`, `puts`, `std::cout`, `std::cerr`, and `std::clog` output are blocked. |
| HG029 | error | comment similarity | Generic/vague comments are still blocked by HG006, and repeated or highly similar comment text is additionally blocked when the file contains exact duplicates, template-marker near duplicates, or function-local high-similarity comment reuse. |
| HG030 | error | comment syntax | HLS comments must use `//` single-line comments or contiguous `//` blocks only. `/* ... */`, `/** ... */`, and block-comment continuation lines are not legal comment syntax in current-project style. |
| HG031 | error | formatting | Short control headers, ordinary local declarations, and ordinary assignments must stay on one line when the merged statement fits the configured line limit. Function signatures, long template types, initializer lists, and genuinely over-limit statements are exempt. |

## HLS-specific adaptations

HLS code now reuses the Python-side typed-prefix and comment-dedup intent at the policy level, while still mapping them onto HLS concepts:

- Inline purpose comments become HG005 for single-line local C/C++ declarations and assignments. HLS often uses long template types, arrays, and streams, so profiles can exempt long or multi-line declarations from right-side comments. The exemption is not silent: HG024 keeps the construct visible to reviewers.
- Naming discipline splits into HG014 plus HG025/HG026/HG027. HG014 still rejects vague names such as `tmp` or `value`, while the typed-prefix rules require prefixes such as `bool_`, `int_`, `uint_`, `ptr_`, `arr_`, `stream_`, `axis_`, or an explicit custom typedef prefix. `ref_` is banned; if alias semantics must be exposed, use `typeprefix_alias_*`.
- Public contract comments become HG007/HG008/HG015/HG009/HG010/HG011 contracts covering file role, function role, top ports, pragmas, loops, and testbench acceptance. HG007 now requires the file head to be a contiguous `//` contract block, and HG008 requires every checked function contract to contain `职责：`, `参数：`, `返回：`, and `副作用：`.
- Comment quality now has a second line of defense after HG006. HG029 blocks exact duplicate and near-duplicate Chinese comment reuse, and HG030 forbids block-comment syntax so contract text stays line-oriented and reviewable.
- HG031 closes the line-based detection gap for avoidable formatting splits. It reports the first physical line, the merged statement excerpt, and a direct single-line repair instruction while leaving genuinely long C++ constructs to HG024 review.

## AST provider behavior

The provider order is Clang, tree-sitter-cpp, then pycparser. Fake HLS headers cover `ap_int`, `ap_fixed`, `hls::stream`, AXIS words, `hls::task`, common C headers, and basic standard-library shims.

For comment-only baseline comparison, lack of an AST provider is an error because behavior preservation cannot be proven. For general readability checks, line/token rules still run and the report records `ast_provider_unavailable=true`; AST-backed structure and contract precision may degrade in that mode.
