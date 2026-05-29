# Contributing

Thank you for improving HLS Generator. This repository is an agent skill first: changes should help an AI coding agent perform Vitis HLS work more reliably, not only add standalone Python behavior.

## Contribution Principles

- Keep `SKILL.md` concise and operational.
- Move detailed background, tool behavior, schemas, and long examples into `references/`.
- Keep deterministic workflow logic in `runtime/` and stable host-facing APIs in `integration/`.
- Do not claim Vitis validation passed unless `vitis-run` or `vitis_hls` actually ran.
- Keep generated outputs, temporary reports, local credentials, and machine-specific paths out of commits.
- Preserve the HLS-only boundary: this skill should not become a handwritten RTL generator.

## Suggested Workflow

1. Open an issue describing the agent behavior, workflow gap, validation problem, or documentation improvement.
2. Make a focused change with a clear before/after behavior.
3. Run the relevant static validation and, when present, the private local validation assets under `tmp/validation/hls-generator/`.
4. Include command output or validation evidence in the pull request.

## Validation

Useful local commands:

```powershell
python -m runtime.hls_generator --version
python -m runtime.hls_generator scaffold --target hls --name vector_scale --out .\reports\hls\spec.json
python -m runtime.hls_generator validate --target hls --spec .\reports\hls\spec.json --path .\reports\hls\generated --readiness static --no-external
python ..\..\tmp\validation\hls-generator\smoke\run_smoke.py
```

External AMD/Xilinx tooling is optional for many changes, but required before claiming hardware-tool acceptance.

## Documentation Expectations

- Keep the default `README.md` in English.
- Put Chinese user-facing documentation in `README-CN.md`.
- Keep examples short, reproducible, and aligned with the skill's HLS-only scope.
