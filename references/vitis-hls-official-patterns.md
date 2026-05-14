# Vitis HLS Official Pattern Notes

Source note: distilled from AMD/Xilinx Vitis-Tutorials 2025.2 HLS material. Keep this file as a compact skill reference; do not depend on the downloaded tutorial tree at runtime.

## Optimization Discipline

- Start with a clean, sequential HLS implementation and a self-checking C simulation before adding performance pragmas.
- Pick a measurable target first: target clock, function interval, loop II, throughput, resource ceiling, or co-simulation behavior.
- Run synthesis and inspect reports before changing directives. Use loop interval, target II, achieved II, issue type, schedule viewer evidence, timing slack, and resource deltas to choose the next change.
- Add one optimization class at a time when possible, then compare reports against the previous run.

## Pipeline and II

- Apply `PIPELINE II=<n>` to the loop or function scope that directly controls throughput.
- Pipelining an outer loop can force inner-loop concurrency. Treat this as a design architecture choice, because it can expose memory bandwidth and operator availability bottlenecks.
- If `II=1` fails, inspect whether the issue is timing, recurrence, memory port pressure, or interface bandwidth before adding more pragmas.
- Use `rewind` only for loop pipelines where back-to-back loop transactions improve throughput and the state behavior is valid.

## Array Partition and Reshape

- Use `ARRAY_PARTITION` or `ARRAY_RESHAPE` only when the access pattern needs more parallel reads/writes than the current storage or interface can provide.
- `ARRAY_PARTITION` creates banks or elements for parallel access; it can grow registers/BRAM/LUT usage.
- `ARRAY_RESHAPE` widens the storage/interface word while preserving a packed view; it is useful when adjacent elements are consumed together.
- Do not apply both partition and reshape to the same variable in the same solution.
- Match `dim`, `type`, and factor to the loop access dimension that appears in the II or load/store bottleneck.

## Dataflow

- Use `DATAFLOW` after the design has clear producer, compute, and consumer stages.
- Prefer small helper functions or loop regions connected by `hls::stream` FIFOs.
- Assign explicit stream depths when producer and consumer latency can differ.
- Keep dataflow regions free of recursion, hidden global-state coupling, and ambiguous shared array mutation.

## Multi m_axi Bundles

- Give independent memory channels distinct `bundle=` names when the kernel needs concurrent external reads or read/write traffic.
- Keep `depth=` concrete for every `m_axi` argument so C/RTL co-simulation has a bounded memory model.
- Use one bundle for intentionally shared arbitration and separate bundles for independent bandwidth.
- Keep scalar control arguments and return control on `s_axilite` unless the interface profile requires a native control mode.

## Fixed-Point and Floating-Point

- Use floating point when the algorithm requirement genuinely depends on dynamic range or numerical compatibility.
- Use `ap_fixed` when range, integer bits, and quantization behavior are known; document the chosen range and error budget.
- Device generation matters: floating-point QoR can change significantly across DSP architectures.
- Decide explicitly whether `config_compile -unsafe_math_optimizations` is allowed; do not assume it for mathematically sensitive kernels.

## DSP-Oriented Building Blocks

- Treat transform and filter kernels as explicit design intents. Do not introduce FFT, FIR, or DSP intrinsic patterns unless the confirmed requirement actually calls for them.
- When a DSP-oriented block is required, preserve the interface contract and numeric model before applying throughput optimizations.
- Reusable DSP patterns belong in reference guidance and validation heuristics first; they should not become default generation templates for unrelated kernels.

## Report-Driven Checks

- For each optimized example, record the intended pattern and the report signal that justifies it.
- Check loop trip count, interval, achieved II, slack, resource use, interface report, and co-simulation transcript.
- If a directive does not improve the limiting report metric, remove or revise it instead of accumulating directives.
