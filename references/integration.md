# Integration Guide

## Table of Contents

- [Local package boundary](#local-package-boundary)
- [Stable facade](#stable-facade)
- [Confirmed HLS inputs](#confirmed-hls-inputs)
- [Provider mapping](#provider-mapping)
- [HLS-only validation](#hls-only-validation)
- [Customization boundary](#customization-boundary)

## Local package boundary

This skill is a local, HLS-only Codex skill. Keep the repository root on
`PYTHONPATH`, or run commands from the skill directory before importing the
facade:

- `runtime/hls_generator/`
- `integration/`
- `assets/examples/`
- `references/`

Do not rely on compatibility imports from the old mixed generator. The public
integration surface is intentionally renamed.

## Stable facade

Prefer the facade instead of reaching into the runtime package directly:

```python
from integration.hls_adapter import (
    load_default_workflow_config,
    load_workflow_result,
    render_hls_prompt,
    run_hls_workflow,
    validate_hls_artifacts,
)
```

Use `run_hls_workflow(...)` for full staged execution and resume,
`render_hls_prompt(...)` when a host owns the model call, and
`validate_hls_artifacts(...)` before consuming generated HLS files.

The facade accepts file paths or in-memory dictionaries for specs, workflow
configuration, evidence, decisions, HLS profiles, and reference contracts. When
dict inputs need to become workflow files, they are materialized under
`<out_dir>/_adapter_inputs/`.

Generated output roots, protected source paths, default workflow config path,
example spec directory, Vitis command templates, and tool timeouts are loaded
from `runtime/hls_generator/runtime_config.json`. See
`references/configuration.md` before changing those values.

The facade checks configured skill dependencies before rendering prompts,
running workflows, or validating artifacts. If dependencies are missing or
invalid, it raises `SkillDependencyError` with an install-request payload. Hosts
must ask the user before running `python -m runtime.hls_generator deps install
--all`; after installation, ask the user to restart Codex.

Remote SSH confidence checks must use `scripts/python/remote/remote_vitis_acceptance.py`,
which delegates all SSH discovery, checks, exec, and request execution to the
configured `erie-remote-ssh` helper. Keep real server details in the erie
server-list JSON, not in this skill.

## Confirmed HLS inputs

Generation requires a confirmed requirement contract:

- `target = "hls"`
- `pipeline_required`
- `streamability`
- `interface_family`
- `interface_profile`
- `confirmed_by_user = true`
- `confirmation_notes`

Streamable tasks must explicitly confirm the interface family. AXI-Stream
profiles require `keep_ready`, `keep_last`, and `data_width`. AXI4 profiles
require variant, role, read/write mode, data/address widths, burst policy, and
`id_width` when the variant is AXI4 full.

## Provider mapping

The runtime supports three local provider modes:

- `mock`: deterministic smoke and unit-style tests
- `manual`: read a prepared response file
- `command`: call a host-provided model command

For production integration, use `command` and bridge to the host model runner.
The runtime does not add a cloud SDK dependency.

## HLS-only validation

Final hardware-facing artifacts must be HLS C/C++ headers, HLS C/C++ sources,
C++ HLS testbenches, `.cfg` files, vectors, or Vitis reports. This workflow no
longer generates Python reference models.

Validation selects AMD-Xilinx tooling in the order configured by
`vitis.tools`. The default order is:

1. `vitis-run`
2. `vitis_hls`

If neither executable is visible on `PATH` and external readiness is requested,
validation fails with an actionable toolchain preflight error. Full workflows
also write `remote_toolchain_request.json`; hosts should ask the user to choose
an `erie-remote-ssh` server, run erie discovery/choices/check/workspace-check,
run erie `scan-software`, and then call
`scripts/python/remote/remote_vitis_acceptance.py --mode vitis --server <erie-server>`. If
multiple Vitis versions are detected, hosts must ask the user to choose one and
rerun with `--vitis-version <version>`. Static validation can still run with
`run_external=False` and `readiness="static"`.

Remote Vitis acceptance keeps results under a governed remote project root. The
JSON result includes `remote_project_root`, `remote_conda_prefix`,
`remote_run_dir`, `remote_backup_dir`, and `remote_dir`, all relative to the
selected erie server workdir. Verified runs are archived into `backups/` by
default instead of being left in the active `runs/` area.

Comment language defaults to `auto`: resolve it from
`~/.hls-generator/config.json`, or ask the user to choose `en` or `zh` before
generation. Explicit `comment_language="en"` or `"zh"` overrides the user
config.

## Customization boundary

Keep host-specific glue in `integration/`. Runtime edits should be limited to
shared HLS generator behavior, validation, prompt contracts, or CLI changes.
All generated run artifacts should live outside the skill source tree. In the
source repository, the governed validation output root is `reports/`.
