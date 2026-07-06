---
name: erie-hls-generator
description: Use when working on HLS development, HLS design, HLS modification, HLS debug, HLS debugging, Chinese-language HLS requests, high-level synthesis, Vitis HLS, AMD/Xilinx HLS, C/C++ HLS kernels, pragmas/directives, interfaces, DATAFLOW, array partition/reshape, hls_config.cfg, Tcl flow, csim/cosim, HLS reports, or HLS-generated RTL/Verilog interface, export, cosim, or debug issues.
---

# Erie HLS Generator

Use this skill for local AMD-Xilinx/Vitis HLS C/C++ kernel generation. Python implementation is split by function under `scripts/python/cli`, `scripts/python/config`, `scripts/python/generation`, `scripts/python/hls_quality_gate`, `scripts/python/remote`, `scripts/python/task_dispatcher`, `scripts/python/validation`, and `scripts/python/workflow`; the stable local facade is `scripts/python/integration/hls_adapter.py`.

## Workflow

1. On first trigger in a Codex session, run `python -m scripts.python.cli.hls_generator deps check --json` from this skill directory. If it reports `blocked_dependency`, ask the user whether to install the listed required dependencies before continuing. Recommended dependencies warn only; do not treat them as blockers outside the capability path that explicitly requires them.
2. Start from a confirmed HLS JSON spec or create one with the scaffold command.
3. Use the facade for local integrations:
   - `run_hls_workflow(...)` for full staged execution or resume.
   - `render_hls_prompt(...)` when a caller owns the model call.
   - `validate_hls_artifacts(...)` before using generated files downstream.
4. Require a confirmed requirement contract before generation: `pipeline_required`, `streamability`, `interface_family`, `interface_profile`, `confirmed_by_user`, and `confirmation_notes`. When throughput targets, numeric strategy, task parallelism, or device portability are in scope, confirm those constraints before code generation.
5. Run the fixed default HLS pipeline: `requirements -> codegen_plan -> tests -> hls`. Treat `remote_toolchain_request.json` and `remote_vitis_acceptance.py` as explicit follow-up acceptance helpers, not default generation stages.
6. Treat generated HLS C/C++ Chinese comment placement as a hard gate: file headers, blank-line-separated lower blocks, function contracts, type contracts, includes, macros, HLS pragmas, loops, variable declarations/assignments, function calls, return statements, datapath steps, vector hashes, and testbench PASS/FAIL behavior must have a blank line plus an immediate Chinese purpose comment where required. Generic, template-like, English, or misplaced comments block validation.
7. For comment-only HLS rewrites, validate with the AST guard: compare non-comment token fingerprints and normalized AST fingerprints against the baseline artifact tree. Use `--baseline-path` in the CLI or `baseline_path=` in the facade when validating comment-only changes.
8. Keep final hardware-facing artifacts limited to HLS C/C++ headers, sources, C++ testbenches, `.cfg` files, vectors, and reports. This skill no longer generates or validates Python reference models.
9. Validate with AMD-Xilinx tooling. Static-only validation reports `static_only=true` and `vitis_executed=false`; do not claim tool execution unless `vitis-run` or `vitis_hls` actually ran. Missing local tools block run/acceptance paths with a remote-server request so the caller can ask the user to choose an `erie-remote-ssh` server with Vitis available.
10. For Vitis development, simulation, cosim, and debug guidance, follow `runtime_config.json` skill routing: prefer `vitis-developer` when installed, otherwise fall back to `vitis-hls-synthesis`.

## Local Commands

For source-repository validation only, run the bundled smoke validator from the repository root:

```powershell
python .\tests\smoke\run_smoke.py
```

Use the functional CLI from the skill directory or another workspace. Pick an explicit writable output directory when you need generated specs, prompts, or validation JSON:

```powershell
python -m scripts.python.cli.hls_generator config --path
python -m scripts.python.cli.hls_generator selfcheck --json
python -m scripts.python.cli.hls_generator deps check --json
python -m scripts.python.cli.hls_generator deps request --out <output-dir>\skill_dependency_request.json
python -m scripts.python.cli.hls_generator scaffold --target hls --name vector_scale --out <output-dir>\hls\spec.json
python -m scripts.python.cli.hls_generator prompt --target hls --spec <output-dir>\hls\spec.json --out <output-dir>\hls\prompt.md --confirm-requirements --confirmation-notes "<user-confirmed HLS contract>"
python -m scripts.python.cli.hls_generator validate --target hls --spec <output-dir>\hls\spec.json --path <output-dir>\hls\generated --readiness static --no-external
python -m scripts.python.cli.hls_generator validate --target hls --spec <output-dir>\hls\spec.json --path <output-dir>\hls\commented --baseline-path <output-dir>\hls\baseline --readiness static --no-external
python -m scripts.python.cli.hls_generator readability-gate --target hls --path <output-dir>\hls\generated --profile kernel --style current-project --json
python -m scripts.python.cli.hls_generator comment-plan --target hls --path <output-dir>\hls\commented --baseline-path <output-dir>\hls\baseline --out <output-dir>\hls\reports\hls_comment_rewrite_plan.json
```

When local `vitis-run`/`vitis_hls` is missing, inspect the workflow's `remote_toolchain_request.json`, ask the user to choose a configured `erie-remote-ssh` build server and, when needed, a separate validation server, then use the remote acceptance helper:

```powershell
python .\scripts\python\remote\remote_vitis_acceptance.py --mode link --server <erie-server>
python .\scripts\python\remote\remote_vitis_acceptance.py --mode vitis --server <erie-server> --profile <configured-profile> --readiness <execute|implement|cosim>
python .\scripts\python\remote\remote_vitis_acceptance.py --mode vitis --build-server <erie-build-server> --validate-server <erie-validate-server> --vitis-version <shared-version> --readiness <execute|implement|cosim>
python .\scripts\python\remote\remote_vitis_acceptance.py --mode board --server <erie-server> --platform-name <platform-name> --remote-platform-root <remote-platform-root> --remote-xpfm <remote-xpfm> --example-spec <board-runnable-example> --comment-language zh --json
python .\scripts\python\validation\confidence_loop.py --server <erie-server> --vitis-version <shared-version> --readiness cosim --remote-parallelism 3 --json-out ..\..\reports\confidence-loop\latest-remote.json
```

`confidence_loop.py` defaults remote review to a single canonical smoke spec. Only use `--remote-coverage tier1` when you intentionally want the broader representative/high-risk matrix; otherwise do not turn routine template validation into a full remote sweep.

Remote Vitis acceptance refreshes erie software scan data. If multiple Vitis
versions are detected and no version has been saved for that server in
`~/.hls-generator/config.json`, ask the user to choose a version and rerun with
`--vitis-version <version>`.

If no remote Vitis profile has been configured and no previously saved remote
selection provides the required tool path, expected tool, and target part,
stop and ask the user to configure those values before continuing. Do not guess
or fall back to a package default path.

If the user chooses a split build/validate topology, keep the server choice in
runtime arguments or user-local configuration only. Do not encode real server
ids, hostnames, usernames, ports, or board-specific server defaults into the
skill package.

Vitis remote acceptance keeps the remote validation directory by default and reports
`remote_dir` relative to the selected erie server workdir. Pass
`--cleanup-remote` only when the user explicitly wants that remote project
deleted after a successful run.

## Reference Loading

- Load `references/hls-template-catalog.md` before changing curated template-corpus coverage, assetization status, errata tracking, or source-gap handling.
- Load `references/integration.md` when wiring the local facade into another script.
- Load `references/workflow-contracts.md` when handling run directories, statuses, resume behavior, or traces.
- Load `references/configuration.md` before changing generated roots, protected paths, Vitis tool commands, or timeouts.
- Load `references/vitis-hls-2024-2-script-guide.md` before changing Vitis HLS `.cfg` parsing, Tcl rendering, pragma rules, report handling, or compatibility checks.
- Load `references/hls-optimization-patterns.md` before changing optimization examples, prompt pragma policy, report-driven tuning rules, or reusable HLS pattern guidance.
- Load `references/hls-report-driven-optimization.md` before changing performance-goal framing, synthesis-report interpretation, or optimization-step sequencing.
- Load `references/hls-modeling-strategy.md` before changing loop-bound handling, numeric-type guidance, pointer modeling, template/vector usage, or conditional pragma policy.
- Load `references/hls-memory-burst-and-layout.md` before changing AXI4 burst policy, local memory layout, lane packing, or reusable buffer guidance.
- Load `references/hls-task-parallel-strategy.md` before changing task-level parallelism guidance, channel semantics, restart behavior, or stream/dataflow positioning.
- Load `references/hls-stencil-reduction-gemm-patterns.md` before changing stencil/window, reduction-tree, or tiled-GEMM guidance and templates.
- Load `references/hls-advanced-library-patterns.md` before changing hls_task, hls_streamofblocks, hls_directio, or hls_fence guidance and validation.
- Load `references/hls-fir-template-family.md` before changing FIR pipeline, symmetric, AXIS, dataflow, or specialized FIR-family guidance and assets.
- Load `references/hls-fft-cordic-template-family.md` before changing FFT/DFT scaling, twiddle, power-spectrum, CORDIC, or transform-family guidance and assets.
- Load `references/hls-stream-codec-template-family.md` before changing RLE AXIS, stream-codec framing, TLAST policy, or reference-first compression guidance.
- Load `references/hls-linear-algebra-template-family.md` before changing matmul, prefix scan, SpMV reference guidance, or linear-algebra family assets.
- Load `references/hls-project-structure-patterns.md` before changing project structure patterns such as minimal Vitis kernel flow, host-kernel-package staging, kernel variant trees, or hotspot-file organization rules.
- Load `references/hls-device-migration-strategy.md` before changing target-part migration guidance, QoR comparison rules, or floating-point/fixed-point portability advice.
- Load `references/hls-library-policy.md` before changing HLS include choices, advanced HLS library usage, or generated library examples.
- Load `references/hls-comment-style.md` before changing generated C/C++ or workflow Python comment language, spacing, coverage, or validation rules.
- Load `references/hls-ast-comment-guard.md` before changing comment-only rewrite validation, AST provider selection, or parser fallback behavior.
- Load `references/hls_readability_rules.md` before changing `HGxxx` rule semantics, severity defaults, profile thresholds, HLS naming rules, or comment-plan behavior.
- Load `references/hls_dispatcher.md` before changing generate/modify/explain routing or comment-only rewrite policy.
- Load `references/hls_readability_gate.md` before changing local readability acceptance commands or report section names.
- Load `references/remote-board-platform-upload.md` before handling uploaded remote U55C platform/xpfm payloads or when board validation is blocked on a missing platform package.
- Load `references/hls-tutorial-derived-templates.md` before changing family-to-template mapping policy, 2D block-transform skeletons, or report-driven optimization cues distilled from the curated reference corpus.
- Use `assets/examples/` for minimal HLS memory, burst, stencil, reduction, tiled-GEMM, lane-packed, task-graph, stream-of-blocks, free-running, fence-ordering, stream, partition, dataflow, multi-`m_axi`, and numeric-strategy specs.
- Use `assets/templates/` for reusable HLS JSON skeletons that already include `design_requirements`, `interface_profile`, `performance`, `hls_profile`, and confirmation notes.

## Boundaries

- Do not generate handwritten Verilog or SystemVerilog.
- HLS-generated RTL/Verilog interface, export, cosim, and debug issues are in scope when they trace back to Vitis HLS code, pragmas, configuration, or reports.
- Pure handwritten Verilog/SystemVerilog debug is not led by this skill; use vivado-debug, vivado-sim, vivado-analysis, or RTL-focused skills for those tasks.
- Do not use local non-HLS hardware tools as validation substitutes.
- Do not modify files outside this repository, except for the governed source-repository validation directories `tests/` (including `tests/smoke/`) and `reports/`.
- Keep path and Vitis-tool policy in `scripts/python/config/runtime_config.json`; update `references/configuration.md` when the policy changes.
- Keep skill dependencies in `scripts/python/config/runtime_config.json`; missing required dependencies block only their matching capability path, while missing recommended dependencies remain warnings. Install only after the user confirms, then restart Codex so new skill metadata is loaded.
- If `vitis-developer` is installed, dependency installation must not install `vitis-hls-synthesis` from FPGA-Agent-Skills; the remaining Vivado skills are still required.
- Use `erie-remote-ssh` for remote SSH checks; do not copy server-list details into this skill.
- If local Vitis tools are unavailable, prefer requesting a remote erie server over weakening validation or substituting non-HLS tools. Discover and present erie server choices before connecting.
- When comment language is `auto`, use Chinese (`zh`) by default. Do not block generation on a language-choice prompt; this project requires Chinese comments.
- Do not claim Vitis validation passed unless `vitis-run` or `vitis_hls` actually ran.
