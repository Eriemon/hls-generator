# Contributing

Thank you for improving `readable-hls-generator`. Contributions should preserve the skill's HLS-only boundary and its evidence-first release workflow.

## Before changing files

1. Read `SKILL.md` and the relevant document under `references/` before changing a routing rule, workflow contract, configuration policy, HLS template, or validation rule.
2. Confirm the affected surface: HLS C/C++ artifacts, HLS configuration, generated reports, public documentation, or internal Python machinery. Do not use the HLS quality gate as a Python governance substitute.
3. For Python changes, load `readable-python-generator` and `readable-script-generator`, classify the task, and satisfy both readability gates while editing. For shell, PowerShell, batch, or Tcl changes, use the same shared route and keep the target language responsible for the final gate.
4. Keep changes inside this skill directory unless repository governance explicitly approves a required validation or report path.

## Development checks

Use a writable output directory for generated material and keep release directories versioned:

```powershell
python -m scripts.python.cli.readable_hls_generator selfcheck --json
python -m scripts.python.cli.readable_hls_generator deps check --json
python -B .\scripts\python\validation\quick_validate.py .
python -B .\scripts\python\validation\run_compileall_no_cache.py .\scripts\python
```

The repository owner runs pytest on the configured remote validation server. Do not claim a remote or Vitis result from a local-only check. A comment-only HLS rewrite must retain its baseline and pass the token/AST guard.

## Pull requests and releases

- Explain the behavior contract, affected files, and evidence used to accept the change.
- Add or update focused tests when the contract changes; do not bulk-rename existing tests.
- Update `README.md` and `README-CN.md` together when public behavior or commands change.
- Build a new `dist/readable-hls-generator-vX.Y.Z/` directory rather than rewriting an older release.
- Run the release receipt, manifest, install, and local mirror checks before requesting publication.
- The local `github/readable-hls-generator/` checkout is a mirror target. It is not a parent-repository commit and must not be pushed without explicit publication authorization.

## Commit hygiene

Keep commits focused and factual. Do not commit credentials, private remote configuration, generated caches, `.codebase-memory/`, or the local `github/` mirror checkout.
