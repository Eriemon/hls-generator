# Erie HLS Generator Design Goals

## Why HLS-only

This skill exists to help Codex generate AMD-Xilinx/Vitis HLS C/C++ kernels with supporting local workflow automation. The previous implementation mixed HLS and Verilog RTL paths, which made the skill harder to trigger correctly, harder to validate, and easier for agents to drift into direct RTL generation. This project intentionally narrows the skill to HLS so every prompt, example, validation rule, and command path reinforces the same target.

The final hardware-facing artifacts must be Vitis HLS source, headers, C++ testbenches, configuration files, vectors, and HLS reports. The workflow is now HLS-only and does not generate or validate Python reference models.

## Non-goals

- Do not generate Verilog, SystemVerilog, or handwritten RTL.
- Do not provide ResearchAssistant or GUI Code Design host integration.
- Do not support local RTL tools such as `iverilog`, `vvp`, or `yosys`.
- Do not add broad user-facing documentation outside the standard Skill files, except this root design-goal record requested for engineering alignment.
- Do not modify files outside this repository, except that source-repo validation assets are expected to live under the repository-root `smoke/`, `tests/`, and `reports/` directories.

## AMD-Xilinx Target

Target Vitis HLS workflows for C/C++ kernels, including:

- `ap_int`, `ap_uint`, `ap_fixed`, and `hls::stream`-oriented code.
- `#pragma HLS INTERFACE`, `PIPELINE`, `DATAFLOW`, `ARRAY_PARTITION`, and `STREAM` guidance.
- AXI memory, AXI4-Stream, native scalar, and custom interface contracts when confirmed by the user or calling spec.
- Local validation through AMD-Xilinx HLS tooling.

The validator must prefer the first configured Vitis tool and then fall back through the configured tool list. The default policy prefers `vitis-run` and falls back to `vitis_hls`. If no configured command is available on PATH, Vitis validation must fail with a clear toolchain preflight error.

## Skill Design Pattern

This skill follows the standard Skill structure: `SKILL.md` for concise routing and workflow instructions, `agents/openai.yaml` for UI metadata, `references/` for details loaded on demand, `assets/` for examples, and `scripts/python/` for deterministic implementation code. Python implementation is split by function into `cli/`, `config/`, `generation/`, `hls_quality_gate/`, `integration/`, `remote/`, `task_dispatcher/`, `validation/`, and `workflow/`; `scripts/python/integration/hls_adapter.py` remains the stable local API facade. Source-repository validation lives at the repository root in `smoke/`, `tests/`, and `reports/`.

The design uses the local Skill-pattern reference reviewed during planning:

- Tool Wrapper: wrap Vitis HLS command execution and report parsing behind deterministic functional helpers.
- Generator: produce a fixed manifest plus HLS files from a structured spec.
- Reviewer: validate generated HLS artifacts with static checks, interface-contract checks, testbench checks, and Vitis report checks.
- Inversion: require confirmed requirements and interface choices before generation.
- Pipeline: enforce `requirements -> codegen_plan -> tests -> hls` instead of letting the agent skip stages.

## Workflow Stages

1. Normalize confirmed requirements and interface profile.
2. Build a code generation plan with open-question gating.
3. Generate semantic test vectors.
4. Generate HLS C/C++ and configuration artifacts from the confirmed vectors and interface contract.
5. Validate statically and then through Vitis tooling.

## Version Control And Locality

All development must stay inside the current repository directory; the formal Skill root is `skills/erie-hls-generator/` so the folder name still matches `name: erie-hls-generator` while following the canonical `skills/<skill-name>/` layout. Git is the source of change tracking for this directory. Use commit-sized changes, keep generated caches ignored, and never modify sibling or external folders while implementing this skill.

Runtime path policy, generated-output roots, protected source areas, Vitis tool command templates, and validation timeouts are centralized in `scripts/python/config/runtime_config.json` and described in `references/configuration.md`. Avoid adding new hard-coded machine paths to scripts or Skill instructions.

Remote validation must go through the configured `erie-remote-ssh` helper and its server-list JSON. UC-style link checks prove SSH helper connectivity only; they do not count as Vitis acceptance unless the remote profile exposes the expected AMD-Xilinx HLS tool and the HLS readiness run completes.
