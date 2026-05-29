# HLS FIR Template Family

This note records durable FIR-family HLS guidance for the skill.
It keeps algorithm-family facts while discarding temporary source-note scaffolding.

## Scope

- `Template 1-10` cover basic FIR pipeline structure, loop unroll, complete partition, symmetric coefficients, transposed structure, AXIS streaming, DATAFLOW staging, double buffering, constant-coefficient shift-add, and packed complex I/Q handling.
- In the first asset wave, the promoted first-class variants are `pipeline`, `symmetric`, `AXIS`, and `dataflow`.
- `transposed`, `double-buffer`, `shift-add`, and `complex I/Q` stay reference-only until the validator and mock assets grow enough to model them honestly.

## Reusable Design Facts

- Confirm FIR `tap count` before choosing unroll, complete partition, or direct-form versus transposed structure.
- Treat `coefficient symmetry` as a structural choice: symmetric filters justify pairwise sample reuse and fewer multipliers.
- Treat the `structure style` as a first-class decision. Direct-form, transposed, and staged DATAFLOW FIR kernels have different local-buffer and II behavior.
- `AXIS` FIR variants need an explicit packet or sample-boundary story; do not infer TLAST behavior from a generic stream surface.
- Constant-coefficient shift-add and complex I/Q FIR structures are optimization or specialization branches, not default codegen baselines.

## Assetization Map

- `template-ready`: FIR pipeline, FIR symmetric, FIR AXIS, FIR dataflow.
- `reference-only`: FIR transposed, FIR PIPO double-buffer, FIR shift-add, FIR complex I/Q.
- `validator-ready`: none beyond the first-batch static profile and testbench checks.
