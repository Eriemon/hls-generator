<p align="center">
  <a href="README.md"><strong>English</strong></a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md">中文</a>
</p>

<p align="center">
  <img src="assets/readme/hero.png" alt="Readable HLS Generator: from an HLS contract to a validated kernel" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-1f6feb"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2f81f7">
  <img alt="Version" src="https://img.shields.io/badge/version-v0.5.1-7c3aed">
  <a href="SKILL.md"><img alt="Agent Skill" src="https://img.shields.io/badge/agent--skill-16a34a"></a>
  <a href="references/vitis-hls-2024-2-script-guide.md"><img alt="Target" src="https://img.shields.io/badge/target-Vitis%20HLS-f59e0b"></a>
</p>

# Readable HLS Generator

Released on 2026-08-14, this Codex skill supports readable AMD/Xilinx Vitis HLS generation, review, and validation.

## What it does

Readable HLS Generator turns a confirmed behavior contract into readable HLS C/C++ kernels, interface-aware testbenches, and validation-ready outputs. It keeps HLS code, pragmas, interfaces, configuration, and reports connected throughout the workflow. Handwritten Verilog/SystemVerilog generation is outside the skill's scope.

The workflow covers contract-driven creation, focused edits, review, Chinese semantic annotation, static validation, and tool-assisted validation when the required Vitis HLS environment is available.

## Install

Ask your AI assistant to install the skill from https://github.com/Eriemon/hls-generator. After installation, ask the assistant to use Readable HLS Generator for AMD/Xilinx Vitis HLS work.

## Before you start

Prepare a confirmed HLS contract with the intended pipeline behavior, streamability, interface family, interface profile, and confirmation notes. Also state any throughput target, numeric strategy, task-level parallelism, device constraint, or tool-version requirement that affects the design.

If validation needs Vitis HLS execution, make the required toolchain or an approved remote execution route available. Static readiness and tool execution are reported as different outcomes.

## How to use

Describe the kernel goal and provide the confirmed contract to your AI assistant. Choose the route that matches the work:

| Route | Use it for |
| --- | --- |
| Create | Build a spec and generate a readable kernel. |
| Write | Change C/C++, pragmas, interfaces, DATAFLOW, or configuration. |
| Review | Inspect source, interfaces, reports, and generated design facts. |
| Annotate | Add Chinese semantic comments while preserving behavior. |
| Validate | Run static checks and, when requested, Vitis HLS or remote validation. |

For comment-only changes, provide a baseline so non-comment tokens and normalized structure can be compared before acceptance.

## Preview

Review the contract, interface choices, generated source, and validation route before accepting the result.

### Contract and interface choices

<p align="center">
  <img src="assets/readme/project-facts.png" alt="Requirement contract, interface choices, and HLS validation path" width="100%">
</p>

### Generation profile

<p align="center">
  <img src="assets/readme/design-profile.png" alt="Generation profile with policy, questions, and output contract" width="100%">
</p>

### Readable output rules

<p align="center">
  <img src="assets/readme/rule-rendering.png" alt="Readable HLS rules and output rendering" width="100%">
</p>

### Bilingual contract view

<p align="center">
  <img src="assets/readme/project-facts-cn.png" alt="Bilingual view of the HLS contract and interface choices" width="100%">
</p>

## What you get

The skill produces a readable HLS source tree, structured specifications and generation plans, interface-aware testbench material, semantic comments, and validation results that distinguish static readiness from actual tool execution. The resulting workflow keeps the design intent visible from the first contract through the final generated artifact.

## Authors and citation

Readable HLS Generator is created by Jiyuan Liu and He Li at Southeast University（东南大学）, with the Heterogeneous Intelligence and Quantum Computing Laboratory (HIQC).

For citation details, see [`CITATION.cff`](CITATION.cff). The project is distributed under the Apache License 2.0; see [`LICENSE`](LICENSE).
