# Workflow Contracts

## Table of Contents

- [Run directory](#run-directory)
- [Fixed stages](#fixed-stages)
- [Stable statuses](#stable-statuses)
- [Validation levels](#validation-levels)
- [Resume behavior](#resume-behavior)
- [Trace semantics](#trace-semantics)

## Run directory

Every `run_hls_workflow(...)` execution writes a self-contained run directory
with stable top-level artifacts:

- `plan.json`
- `workflow_config.json`
- `workflow_result.json`
- `workflow-state.json`
- `trace.jsonl`

The adapter also materializes preflight inputs under `_adapter_inputs/`:

- `spec.json`
- `requirements.json`
- `codegen_plan.json`
- optional `evidence.json`
- optional `decision.json`

Each attempt lives under `attempt-001/`, `attempt-002/`, and so on. Stage
artifacts are kept separate:

- `requirements/artifacts/plan/<name>_requirements.json`
- `codegen_plan/artifacts/plan/<name>_codegen_plan.json`
- `tests/artifacts/plan/<name>_test_vectors.json`
- `python/artifacts/model/<name>_model.py`
- `python/artifacts/model/<name>_vectors.json`
- `hls/artifacts/...`
- `validation.json`
- `intervention.json` when blocked on a human decision
- `remote_toolchain_request.json` when local Vitis tools are missing and
  remote erie validation is the next recommended path
- `comment_language_request.json` when `comment_language=auto` and no user
  comment language preference is saved

## Fixed stages

The workflow is HLS-only and uses this fixed stage order:

```text
requirements -> codegen_plan -> tests -> python -> hls
```

The `requirements` and `codegen_plan` stages are structured JSON preflight
contracts. The workflow does not enter prompt-driven HLS generation when the
codegen plan has unresolved open questions or `ready_for_generation=false`.

## Stable statuses

`workflow_result.json` only uses these terminal statuses:

- `passed`
- `failed`
- `blocked_human`
- `blocked_toolchain`
- `max_attempts`
- `invalid_response`

Non-HLS targets are rejected as input errors before a workflow status is
recorded.

When `blocked_toolchain` is caused by missing local `vitis-run`/`vitis_hls`,
`workflow_result.json` and the attempt record include
`remote_toolchain_request`. That JSON asks the caller to use
`erie-remote-ssh` discovery and choices first, present the user with enabled
server options, and only then run the HLS remote acceptance helper against the
selected server. The helper refreshes erie software scan data and blocks with
`remote_vitis_version_request.json` when multiple remote Vitis versions require
a user choice.

When `blocked_human` is caused by an unconfigured comment language,
`workflow_result.json` includes `comment_language_request`. The request offers
only `en` and `zh`; the selected value should be saved in
`~/.hls-generator/config.json` before rerunning generation.

Remote Vitis acceptance stages work under the governed project root relative to
the selected erie server workdir. After required verification passes, the
helper archives the run into `backups/<run-id>` and records `remote_dir`,
`remote_run_dir`, and `remote_backup_dir` in its local `result.json`.

## Validation levels

Readiness levels are ordered:

```text
static -> compile -> execute -> implement -> cosim
```

Static checks validate manifests, expected outputs, HLS-only file types,
interface pragmas, top function declarations, `hls_config.cfg`, testbench
`main()`, PASS/FAIL behavior, vector hashes, and Python/HLS interface
contracts.

External validation uses AMD-Xilinx tooling only. Tool order and command
templates come from `runtime/hls_generator/runtime_config.json`; the default
policy prefers `vitis-run` and falls back to `vitis_hls`. The runtime creates a
temporary run-local Tcl script, executes `csim`, `csynth`, and `cosim` as the
requested readiness level requires, and removes the temporary files after
execution.

## Resume behavior

When the workflow stops at `blocked_human`, it writes `intervention.json`. A
host can resume by supplying `decision.json` through the facade or CLI. Resume
appends a new attempt directory and preserves previous traces.

## Trace semantics

`trace.jsonl` is append-only and records the staged lifecycle:

- prompt rendering
- model generation
- extraction
- validation
- interface and reference audits
- verifier gates
- human intervention markers

Host tasks can parse `trace.jsonl` directly or consume
`workflow_result.json` and `workflow-state.json`.
