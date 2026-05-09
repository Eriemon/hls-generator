# Runtime Configuration

The runtime configuration lives at `runtime/hls_generator/runtime_config.json`.
Keep this file inside the skill root. To test an alternate config, set
`HLS_GENERATOR_RUNTIME_CONFIG` to another JSON file under this same skill root.

## Path Policy

`paths.generated_roots` lists the top-level directories where facade and CLI
commands may create generated files when run from the skill root. The default
roots are smoke runs and reports.

`paths.protected_roots` and `paths.protected_files` list source and reference
areas that generated artifacts must not overwrite. Keep runtime code,
integration APIs, references, examples, smoke tests, `SKILL.md`, and
`DESIGN_GOALS.md` protected. The `scripts/` directory is also protected because
it contains executable skill support code, not generated output.

`paths.default_workflow_config`, `paths.examples_dir`, `paths.smoke_root`, and
`paths.workflow_state_file` define the workflow defaults file, example spec
directory, smoke output root, and run-state filename. These values must stay
relative to the skill root.

When changing generated roots, update the ignore policy at the same time so
temporary workflow outputs do not become tracked source files.

## Vitis Tool Policy

`vitis.tools` is ordered by preference. Each entry has:

- `name`: stable tool id used in validation reports.
- `which`: executable name used for preflight discovery on `PATH`.
- `label`: human-readable validation label.
- `command`: command template. Use `{tcl}` where the generated Tcl path should
  be substituted.

The default order prefers `vitis-run` and falls back to `vitis_hls`. The default
`vitis-run` template uses `--mode hls --tcl` so the same generated Tcl controls
csim, csynth, and cosim readiness.

`vitis.tcl` controls temporary Tcl/project names and solution naming.
`vitis.timeouts_s` controls per-readiness timeout budgets for compile, execute,
implement, and cosim validation.

## Skill Dependency Policy

`skill_dependencies` is the declarative list of Codex skills this skill expects
to be installed before HLS workflows run. Each dependency entry includes:

- `id`: stable dependency id used by `deps install --ids`.
- `level`: `required` or `recommended`; both levels are blocking for this skill.
- `purpose`: user-facing reason for the dependency.
- `repo_url` and `ref`: Git source used by `deps install`.
- `paths`: repo paths to install. Use `.` for single-skill repository roots.
- `expected_skill_names`, `destination_names`, and `aliases`: installed skill
  names accepted by the scanner.
- `adapter`: dependency family, such as `erie-remote-ssh`, `fpga-agent-skills`,
  `superpowers`, or `context-engineering`.
- `blocking`: must remain `true` for all configured dependencies.

The dependency scanner checks `$CODEX_HOME/skills`, `~/.codex/skills`, and
Codex plugin caches. For tests or controlled hosts, override discovery with
`HLS_GENERATOR_SKILLS_DIRS` and `HLS_GENERATOR_PLUGIN_CACHE_DIRS` using the
platform path separator. When `HLS_GENERATOR_SKILLS_DIRS` is set, `deps
install` also defaults to the first listed skills directory. This lets the
Superpowers plugin satisfy the Superpowers dependency without copying each skill
into the normal skills directory.

Use these commands from the skill root:

```powershell
python -m runtime.hls_generator deps check --json
python -m runtime.hls_generator deps request --out .\reports\skill_dependency_request.json
python -m runtime.hls_generator deps install --all
```

`deps install` does not overwrite existing skill directories. If an installed
dependency is invalid, repair it manually or remove it before reinstalling. A
Codex restart is required after installing new skills so trigger metadata is
loaded. Codex skills do not provide a reliable native post-install hook, so
first-install enforcement is implemented through release validation and the
first-trigger dependency check in `SKILL.md`.

## Remote Validation Policy

`remote_validation` configures remote confidence checks without copying server
credentials into this skill. Server ids and names must come from the
`erie-remote-ssh` server-list JSON at runtime.

- `erie_skill_dir` points to the installed `erie-remote-ssh` skill.
- `erie_settings_path` points to the erie settings JSON used for discovery,
  list, check, workspace-check, exec, and request execution.
- `local_run_root` is the generated local report root for remote validation
  plans, overlays, requests, and reports.
- `remote_tmp_dir` is a relative directory under the selected erie server's
  configured `workdir`.
- `python_env` must force UTF-8 output on Windows callers.
- `vitis_profiles` stores environment setup scripts and expected HLS tools by
  profile name.

Use `scripts/remote_vitis_acceptance.py --mode link --server <erie-server>` for
UC-style SSH helper link checks. This mode is read-only on the remote host and
does not claim Vitis acceptance.

Use `scripts/remote_vitis_acceptance.py --mode vitis --server <erie-server>
--profile vitis_2022 --readiness cosim` only with a server whose erie config is
already validated and whose sourced profile exposes the expected Vitis tool. If
the expected tool is missing, the script reports `blocked_vitis_server`.

Vitis mode runs `erie scan-software` before selecting a remote Vitis install. If
the scan reports multiple versions and `~/.hls-generator/config.json` has no
saved choice for that server, the script reports
`blocked_remote_version_choice` and writes `remote_vitis_version_request.json`.
Rerun with `--vitis-version <version>` to save and use that version. The user
config stores only the selected version, settings script, expected tool,
target part, and timestamp.

For Vitis mode, local HLS artifacts remain under this skill's configured
generated root. The script transfers the small tarball through reviewed erie
`request-command` chunks instead of copying files into the erie skill's own
project root for `request-upload`. Successful Vitis mode runs keep the remote
directory by default and write `remote_dir` to `result.json`; the path is
relative to the selected erie server workdir. Use `--cleanup-remote` only for
explicit cleanup runs.

## Inspection

Print the active config:

```powershell
python -m runtime.hls_generator config
```

Print only the active config path:

```powershell
python -m runtime.hls_generator config --path
```
