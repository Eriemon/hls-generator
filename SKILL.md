---
name: erie-hls-generator
description: Use when working on HLS development, HLS design, HLS modification, HLS debug, HLS 调试, 高层次综合, Vitis HLS, AMD/Xilinx HLS, C/C++ HLS kernels, pragmas/directives, interfaces, DATAFLOW, array partition/reshape, hls_config.cfg, Tcl flow, csim/cosim, HLS reports, or HLS-generated RTL/Verilog interface, export, cosim, or debug issues.
---

# Erie HLS Generator

Use this skill for local AMD-Xilinx/Vitis HLS C/C++ kernel generation. The bundled runtime lives in `runtime/hls_generator`, and the stable local facade is `integration/hls_adapter.py`.

## Workflow

1. On first trigger in a Codex session, run `python -m runtime.hls_generator deps check --json` from this skill directory. If it reports `blocked_dependency`, ask the user whether to install the listed dependencies before continuing; do not degrade or continue past missing required or recommended dependencies.
2. Start from a confirmed HLS JSON spec or create one with the scaffold command.
3. Use the facade for local integrations:
   - `run_hls_workflow(...)` for full staged execution or resume.
   - `render_hls_prompt(...)` when a caller owns the model call.
   - `validate_hls_artifacts(...)` before using generated files downstream.
4. Require a confirmed requirement contract before generation: `pipeline_required`, `streamability`, `interface_family`, `interface_profile`, `confirmed_by_user`, and `confirmation_notes`.
5. Run the fixed HLS pipeline: `requirements -> codegen_plan -> tests -> python -> hls`.
6. Keep final hardware-facing artifacts limited to HLS C/C++ headers, sources, C++ testbenches, `.cfg` files, and reports. Python models and vectors are validation intermediates.
7. Validate with AMD-Xilinx tooling. The validator prefers `vitis-run` and falls back to `vitis_hls`; missing local tools block with a remote-server request so the caller can ask the user to choose an `erie-remote-ssh` server with Vitis available.

## Local Commands

Run the bundled smoke validator from this skill directory:

```powershell
python .\smoke\run_smoke.py
```

Use the runtime CLI:

```powershell
python -m runtime.hls_generator config --path
python -m runtime.hls_generator deps check --json
python -m runtime.hls_generator deps request --out .\reports\skill_dependency_request.json
python -m runtime.hls_generator scaffold --target hls --name vector_scale --out .\reports\hls\spec.json
python -m runtime.hls_generator prompt --target hls --spec .\reports\hls\spec.json --out .\reports\hls\prompt.md
python -m runtime.hls_generator validate --target hls --spec .\reports\hls\spec.json --path .\reports\hls\generated --readiness static --no-external
```

When local `vitis-run`/`vitis_hls` is missing, inspect the workflow's `remote_toolchain_request.json`, ask the user to choose a configured `erie-remote-ssh` server, then use the remote acceptance helper:

```powershell
python .\scripts\remote_vitis_acceptance.py --mode link --server <erie-server>
python .\scripts\remote_vitis_acceptance.py --mode vitis --server <erie-server> --readiness <execute|implement|cosim>
```

Remote Vitis acceptance refreshes erie software scan data. If multiple Vitis
versions are detected and no version has been saved for that server in
`~/.hls-generator/config.json`, ask the user to choose a version and rerun with
`--vitis-version <version>`.

Vitis remote acceptance keeps the remote validation directory by default and reports
`remote_dir` relative to the selected erie server workdir. Pass
`--cleanup-remote` only when the user explicitly wants that remote project
deleted after a successful run.

## Reference Loading

- Load `references/integration.md` when wiring the local facade into another script.
- Load `references/workflow-contracts.md` when handling run directories, statuses, resume behavior, or traces.
- Load `references/configuration.md` before changing generated roots, protected paths, Vitis tool commands, or timeouts.
- Load `references/vitis-hls-2024-2-script-guide.md` before changing Vitis HLS `.cfg` parsing, Tcl rendering, pragma rules, report handling, or compatibility checks. The temporary import folder used to create this reference must not remain in the final skill.
- Load `references/vitis-hls-official-patterns.md` before changing optimization examples, prompt pragma policy, report-driven tuning rules, or reusable HLS pattern guidance.
- Load `references/hls-library-policy.md` before changing HLS include choices, advanced HLS library usage, or generated library examples.
- Load `references/hls-comment-style.md` before changing generated C/C++ comment language, comment coverage, or comment validation rules.
- Use `assets/examples/` for minimal HLS memory, stream, partition, dataflow, multi-`m_axi`, and numeric-strategy specs.

## Boundaries

- Do not generate handwritten Verilog or SystemVerilog.
- HLS-generated RTL/Verilog interface, export, cosim, and debug issues are in scope when they trace back to Vitis HLS code, pragmas, configuration, or reports.
- Pure handwritten Verilog/SystemVerilog debug is not led by this skill; use vivado-debug, vivado-sim, vivado-analysis, or RTL-focused skills for those tasks.
- Do not use local non-HLS hardware tools as validation substitutes.
- Do not modify files outside this skill directory.
- Keep path and Vitis-tool policy in `runtime/hls_generator/runtime_config.json`; update `references/configuration.md` when the policy changes.
- Keep skill dependencies in `runtime/hls_generator/runtime_config.json`; missing required or recommended dependencies are blocking. Install only after the user confirms, then restart Codex so new skill metadata is loaded.
- Use `erie-remote-ssh` for remote SSH checks; do not copy server-list details into this skill.
- If local Vitis tools are unavailable, prefer requesting a remote erie server over weakening validation or substituting non-HLS tools. Discover and present erie server choices before connecting.
- When comment language is `auto`, use the user's `~/.hls-generator/config.json`; if it has no saved language, ask the user to choose English (`en`) or Chinese (`zh`) before generation.
- Do not claim Vitis validation passed unless `vitis-run` or `vitis_hls` actually ran.
