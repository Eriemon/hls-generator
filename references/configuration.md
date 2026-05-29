# Runtime Configuration

## Table of Contents

- [Path Policy](#path-policy)
- [Vitis Tool Policy](#vitis-tool-policy)
- [Skill Dependency Policy](#skill-dependency-policy)
- [Remote Validation Policy](#remote-validation-policy)
- [Inspection](#inspection)

The runtime configuration lives at `runtime/hls_generator/runtime_config.json`.
Keep this file inside the skill root. To test an alternate config, set
`HLS_GENERATOR_RUNTIME_CONFIG` to another JSON file under this same skill root.

## Path Policy

`paths.generated_roots` lists the top-level directories where facade and CLI
commands may create generated files when run from a governed repository
workspace. The default generated root is the repository-root `reports/`
directory.

`paths.protected_roots` and `paths.protected_files` list source and reference
areas that generated artifacts must not overwrite. Keep runtime code,
integration APIs, references, examples, eval assets, `SKILL.md`, and
`DESIGN_GOALS.md` protected. The `scripts/` directory is also protected because
it contains executable skill support code, not generated output.

`paths.default_workflow_config`, `paths.examples_dir`, `paths.smoke_root`, and
`paths.workflow_state_file` define the workflow defaults file, example spec
directory, smoke output root, and run-state filename. `default_workflow_config`
and `examples_dir` stay inside the skill root; `smoke_root` is a governed
repository-workspace-relative output path and defaults to `reports/_smoke_runs`.

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

`vitis.skill_routing` controls which Codex skill should guide Vitis work around
simulation, development, HLS components, cosim, and debug:

- `preferred_skill`: the first skill to use when installed. The default is
  `vitis-developer`.
- `fallback_skills`: ordered fallback skills. The default is
  `vitis-hls-synthesis`.

This routing is advisory for Codex skill use. Runtime validation still executes
the configured local `vitis-run` or `vitis_hls` command, or the remote Vitis
acceptance helper when local tools are missing.

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
- `alternative_providers`: optional installed skills that can satisfy one
  expected skill without installing it. The default configuration lets
  `vitis-developer` satisfy `vitis-hls-synthesis` only.
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

When `vitis-developer` is already installed, `deps install --all` skips
`vitis-hls-synthesis` from FPGA-Agent-Skills and reports the skip in
`install_skipped`. The seven `vivado-*` skills remain required and are not
satisfied by `vitis-developer`.

Use these commands from the skill root or another workspace. Choose an
explicit writable output directory for generated request/spec artifacts:

```powershell
python -m runtime.hls_generator deps check --json
python -m runtime.hls_generator deps request --out <output-dir>\skill_dependency_request.json
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
  list, check, workspace-check, exec, and request execution. The current
  preferred layout is `${erie_skill_dir}/assets/defaults.json`; legacy
  `${erie_skill_dir}/config/defaults.json` remains supported for compatibility.
- `local_run_root` is the generated local report root for remote validation
  plans, overlays, requests, and reports.
- `directory_contract.project_root_dirname` is the governed remote project root
  relative to the selected erie server workdir. The default is
  `erie-hls-generator`.
- `directory_contract.conda_prefix_path` is the project-local prefix conda
  environment path relative to that governed project root. The default is
  `.conda/hls-generator`.
- `directory_contract.platform_root_path_template` is the governed remote root
  for user-supplied board platform payloads. The default is
  `platforms/alveo/<platform-name>`.
- `directory_contract.active_run_path_template` and
  `directory_contract.backup_run_path_template` define the active and archived
  run layout. The defaults are `runs/<run-id>` and `backups/<run-id>`.
- `directory_contract.archive_trigger` records when a verified run must move
  from the active run area into `backups/`.
- `python_env` must force UTF-8 output on Windows callers.
- `vitis_profiles` optionally stores user-configured remote Vitis profiles by
  name. The shipped skill may leave this object empty; in that case remote
  acceptance must stop and ask the user to configure the missing values.

Use `scripts/python/remote/remote_vitis_acceptance.py --mode link --server <erie-server>` for
UC-style SSH helper link checks. This mode is read-only on the remote host and
does not claim Vitis acceptance.

Use `scripts/python/remote/remote_vitis_acceptance.py --mode vitis --server <erie-server>
--profile <configured-profile> --readiness cosim` only with a server whose erie
config is already validated and whose configured or previously saved profile
exposes the expected Vitis tool. If no complete profile is available, the
script reports `blocked_remote_profile_config`. If the expected tool is
missing, the script reports `blocked_vitis_server`.

Vitis mode runs `erie scan-software` before selecting a remote Vitis install. If
the scan reports multiple versions and `~/.hls-generator/config.json` has no
saved choice for that server, the script reports
`blocked_remote_version_choice` and writes `remote_vitis_version_request.json`.
Rerun with `--vitis-version <version>` to save and use that version. The user
config stores only the selected version, settings script, expected tool,
target part, and timestamp.

Board mode also uses `~/.hls-generator/config.json` when the user provides an
explicit uploaded platform payload. The user config stores
`board_platform_selection.<server>.platform_name`,
`board_platform_selection.<server>.remote_platform_root`,
`board_platform_selection.<server>.remote_xpfm`, and
`board_platform_selection.<server>.source`. When board mode is rerun without
explicit board platform arguments, the saved board platform selection is used
before governed remote-path discovery and before system-level platform scans.

For Vitis mode, local HLS artifacts remain under this skill's configured
generated root. The script transfers the small tarball through reviewed erie
`request-command` chunks instead of copying files into the erie skill's own
project root for `request-upload`. Successful Vitis mode runs must stage work
under the governed project root, keep the project-local conda prefix under
that root, and archive verified run directories into `backups/<run-id>`. The
JSON result records both active and archived relative paths plus the retained
`remote_dir`.

For board mode, a user-provided U55C platform payload must live under the
governed remote platform root and not under `/tools/Xilinx` by default. The
preferred shape is an uploaded extracted directory such as
`erie-hls-generator/platforms/alveo/xilinx_u55c_gen3x16_xdma_3_202210_1/`
containing the matching `.xpfm`.

## Inspection

Print the active config:

```powershell
python -m runtime.hls_generator config
```

Print only the active config path:

```powershell
python -m runtime.hls_generator config --path
```
