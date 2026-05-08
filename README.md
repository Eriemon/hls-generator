<p align="center">
  <a href="README.md"><strong>English</strong></a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md">中文</a>
</p>

<p align="center">
  <img src="docs/assets/hero.svg" alt="HLS Generator" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-1f6feb"></a>
  <a href="pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2f81f7"></a>
  <img alt="Version" src="https://img.shields.io/badge/version-v0.1.1-7c3aed">
  <a href="SKILL.md"><img alt="Agent Skill" src="https://img.shields.io/badge/agent-skill-16a34a"></a>
  <a href="references/vitis-hls-official-patterns.md"><img alt="Target" src="https://img.shields.io/badge/target-Vitis%20HLS-f59e0b"></a>
</p>

<h1 align="center">HLS Generator</h1>

<p align="center">
  A Codex-ready agent skill for structured AMD/Xilinx Vitis HLS workflows.
</p>

HLS Generator turns an AI coding agent into a more disciplined HLS engineering assistant. It provides trigger metadata, procedural instructions, reference material, deterministic runtime helpers, examples, and validation gates for moving from confirmed hardware intent to Vitis-ready HLS artifacts.

This repository is primarily an **agent skill package**. The Python CLI is included as the deterministic execution layer, but the main interface is the skill surface an agent can load and follow.

## Why It Exists

Hardware generation fails when the agent jumps straight from a vague request to code. HLS Generator inserts the missing engineering steps: requirement confirmation, interface contracts, staged planning, test-vector construction, Python reference checks, HLS artifact extraction, and validation evidence.

Use it when an agent needs to work on:

- Vitis HLS C/C++ kernels, headers, and testbenches.
- AXI memory, AXI4-Stream, native scalar, and custom interface contracts.
- `PIPELINE`, `DATAFLOW`, `ARRAY_PARTITION`, `STREAM`, and related pragma decisions.
- HLS configuration, Tcl rendering, report collection, and toolchain readiness.
- Debugging HLS-generated RTL interfaces by tracing issues back to HLS source, pragmas, configuration, or reports.

## Skill Architecture

```mermaid
%%{init: {"theme": "base", "themeVariables": {"background": "#0b1220", "primaryColor": "#102033", "primaryTextColor": "#e6edf3", "primaryBorderColor": "#38bdf8", "lineColor": "#60a5fa", "secondaryColor": "#132a3e", "tertiaryColor": "#0f172a", "fontFamily": "Inter, Segoe UI, Arial"}}}%%
flowchart TB
    intent["<b>Confirmed Intent</b><br/>interfaces · throughput · verification target"]

    subgraph skill["Agent Skill Layer"]
      direction LR
      trigger["Trigger Metadata<br/><code>agents/openai.yaml</code>"]
      guide["Operating Contract<br/><code>SKILL.md</code>"]
      refs["Progressive Context<br/><code>references/</code>"]
    end

    subgraph runtime["Deterministic Runtime"]
      direction LR
      scaffold["Spec Scaffold"]
      prompt["Prompt Renderer"]
      extract["Artifact Extractor"]
      validate["Validation Gate"]
    end

    artifacts["<b>Vitis HLS Artifact Set</b><br/>C/C++ · headers · testbench · cfg · reports"]
    evidence["<b>Evidence Package</b><br/>static findings · Vitis reports · workflow traces"]

    intent --> trigger --> guide --> refs --> scaffold
    scaffold --> prompt --> extract --> validate --> artifacts --> evidence

    classDef anchor fill:#0f766e,stroke:#5eead4,color:#ffffff,stroke-width:2px;
    classDef layer fill:#111827,stroke:#334155,color:#e5e7eb;
    classDef node fill:#102033,stroke:#38bdf8,color:#e6edf3;
    classDef output fill:#3b2f11,stroke:#f59e0b,color:#fff7ed,stroke-width:2px;
    class intent,evidence anchor;
    class trigger,guide,refs,scaffold,prompt,extract,validate node;
    class artifacts output;
```

## Workflow

```mermaid
%%{init: {"theme": "base", "themeVariables": {"background": "#0b1220", "actorBkg": "#102033", "actorBorder": "#38bdf8", "actorTextColor": "#e6edf3", "signalColor": "#93c5fd", "signalTextColor": "#dbeafe", "noteBkgColor": "#132a3e", "noteTextColor": "#e6edf3", "fontFamily": "Inter, Segoe UI, Arial"}}}%%
sequenceDiagram
    autonumber
    participant User
    participant Agent
    participant Skill as Agent Skill
    participant Runtime
    participant Toolchain as Vitis HLS

    User->>Agent: Describe kernel intent
    Skill-->>Agent: Load HLS rules and boundaries
    Agent->>User: Confirm interface, pipeline, and validation contract
    Agent->>Runtime: Scaffold spec and render staged prompts
    Runtime->>Runtime: Build plan, vectors, Python oracle, and HLS files
    opt External readiness requested
      Runtime->>Toolchain: Run Vitis HLS validation
      Toolchain-->>Runtime: Reports and diagnostics
    end
    Runtime-->>Agent: Artifacts, trace, and validation evidence
```

## Repository Map

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Agent-facing routing, workflow, constraints, and tool usage rules. |
| `agents/openai.yaml` | UI metadata for skill lists and invocation chips. |
| `runtime/hls_generator/` | Deterministic scaffolding, prompt rendering, extraction, validation, reports, and workflow state. |
| `integration/hls_adapter.py` | Stable host-facing facade for workflow, prompt, and validation calls. |
| `assets/examples/` | Reusable structured HLS specs for stream, memory, dataflow, partition, reshape, fixed-point, and multi-`m_axi` cases. |
| `references/` | Vitis HLS policies, configuration rules, workflow contracts, integration notes, and comment style guidance. |

## Quick Start

Place this repository in a Codex skill search path to use it as an agent skill. For runtime development and local checks:

```powershell
python -m runtime.hls_generator --version
python -m runtime.hls_generator config --path
python -m runtime.hls_generator scaffold --target hls --name vector_scale --out .\reports\hls\spec.json
python -m runtime.hls_generator prompt --target hls --spec .\reports\hls\spec.json --out .\reports\hls\prompt.md --comment-language en
```

Static validation without external AMD/Xilinx tools:

```powershell
python -m runtime.hls_generator validate --target hls --spec .\reports\hls\spec.json --path .\reports\hls\generated --readiness static --no-external
```

External validation requires a real Vitis HLS installation. This project does not claim Vitis acceptance unless `vitis-run` or `vitis_hls` actually runs.

## Integration API

```python
from integration.hls_adapter import (
    render_hls_prompt,
    run_hls_workflow,
    validate_hls_artifacts,
)
```

- `run_hls_workflow(...)`: run or resume the staged HLS workflow.
- `render_hls_prompt(...)`: render prompts when a host owns the model call.
- `validate_hls_artifacts(...)`: validate generated artifacts before downstream use.

## Scope

HLS Generator is intentionally narrow:

- It generates Vitis HLS C/C++ artifacts, not handwritten RTL.
- Python models and vectors are validation intermediates, not hardware deliverables.
- HLS-generated RTL issues are in scope only when they trace back to HLS code, pragmas, configuration, or reports.
- Local secrets, proprietary hardware designs, generated caches, and private remote-server details should stay out of the repository.

## Contact

For questions, collaboration, or academic use, contact: [erie@seu.edu.cn](mailto:erie@seu.edu.cn).

## Citation

If this skill helps your research, teaching, or engineering workflow, please cite it:

```bibtex
@software{hls_generator_skill,
  title        = {HLS Generator: An Agent Skill for Vitis HLS Workflows},
  author       = {Jiyuan Liu},
  year         = {2026},
  license      = {Apache-2.0},
  contact      = {erie@seu.edu.cn}
}
```

GitHub citation metadata is also available in [CITATION.cff](CITATION.cff).

## License

Apache License 2.0. See [LICENSE](LICENSE).
