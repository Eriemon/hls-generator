<p align="center">
  <a href="README.md">English</a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md"><strong>中文</strong></a>
</p>

<p align="center">
  <img src="assets/readme/hero-cn.png" alt="Readable HLS Generator：从 HLS 契约到可验证 kernel" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-1f6feb"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2f81f7">
  <img alt="Version" src="https://img.shields.io/badge/version-v0.5.1-7c3aed">
  <a href="SKILL.md"><img alt="Agent Skill" src="https://img.shields.io/badge/agent--skill-16a34a"></a>
  <a href="references/vitis-hls-2024-2-script-guide.md"><img alt="Target" src="https://img.shields.io/badge/target-Vitis%20HLS-f59e0b"></a>
</p>

# Readable HLS Generator

本版本发布于 2026-08-14，面向 AMD/Xilinx Vitis HLS 提供可读代码生成、复核和验证能力。

## 它能做什么

Readable HLS Generator 将已确认的行为契约转换为可读的 HLS C/C++ kernel、接口感知的 testbench 和可用于验证的输出。它在工作流中保持 HLS 代码、pragma、接口、配置和报告之间的关联。手写 Verilog/SystemVerilog 生成不属于本技能范围。

工作流覆盖契约驱动的创建、定向修改、复核、中文语义注释、静态验证，以及在具备所需 Vitis HLS 环境时执行工具验证。

## 安装

让 AI 安装 https://github.com/Eriemon/hls-generator 中的技能。安装完成后，让 AI 在 AMD/Xilinx Vitis HLS 工作中使用 Readable HLS Generator。

## 需要准备什么

请准备已确认的 HLS 契约，写明流水行为、可流化性、接口族、接口画像和确认说明。如果吞吐目标、数值策略、任务级并行、器件约束或工具版本会影响设计，也请一并说明。

如果验证需要执行 Vitis HLS，请准备所需工具链或经批准的远程执行路径。静态就绪状态和工具执行结果会分别呈现。

## 如何使用

向 AI 说明 kernel 目标，并提供已确认的契约。根据任务选择对应路径：

| 路径 | 用途 |
| --- | --- |
| Create | 建立规格并生成可读 kernel。 |
| Write | 修改 C/C++、pragma、接口、DATAFLOW 或配置。 |
| Review | 检查源码、接口、报告和生成设计事实。 |
| Annotate | 在保持行为不变的前提下添加中文语义注释。 |
| Validate | 运行静态检查，并在需要时执行 Vitis HLS 或远程验证。 |

仅进行注释修改时，请提供 baseline，以便在接受前比较非注释 token 和标准化结构。

## 预览

在接受结果前，请查看契约、接口选择、生成源码和验证路径。

### 契约与接口选择

<p align="center">
  <img src="assets/readme/project-facts-cn.png" alt="需求契约、接口决策与 HLS 验证路径" width="100%">
</p>

### 生成画像

<p align="center">
  <img src="assets/readme/design-profile-cn.png" alt="包含策略、问题和输出契约的生成画像" width="100%">
</p>

### 可读输出规则

<p align="center">
  <img src="assets/readme/rule-rendering-cn.png" alt="可读 HLS 规则与输出呈现" width="100%">
</p>

### 双语契约视图

<p align="center">
  <img src="assets/readme/project-facts.png" alt="HLS 契约与接口选择的双语视图" width="100%">
</p>

## 最终得到什么

你将得到可读的 HLS 源码树、结构化规格和生成计划、接口感知的 testbench 材料、语义注释，以及能够区分静态就绪和真实工具执行的验证结果。整个流程让设计意图从初始契约一直保持到最终生成工件。

## 作者与引用

Readable HLS Generator 由 Jiyuan Liu 和 He Li 创作，来自东南大学（Southeast University），并与 Heterogeneous Intelligence and Quantum Computing Laboratory（HIQC）合作。

引用信息请见 [`CITATION.cff`](CITATION.cff)。项目采用 Apache License 2.0，详见 [`LICENSE`](LICENSE)。
