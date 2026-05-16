# HLS Memory Burst And Layout Notes

Source note: distilled from imported HLS memory-layout references. Keep this file generic and reusable.

## Burst Discipline

- Enable AXI4 burst support only when the access order is contiguous enough to justify coalescing.
- Confirm `max_burst_len` explicitly and keep the cfg/interface setting aligned with the spec.
- If the burst path depends on loop order, document that loop order as part of the hardware contract.

## Local Layout Choices

- Use local buffers only when they match the reuse pattern: line buffer for stencil/window access, tile buffers for GEMM-like reuse, lane buffers for fixed-width packing.
- Keep each buffer name meaningful and stable so validation can trace comments, pragmas, and report review back to the intended structure.
- Do not mix unrelated memory optimizations onto one weak baseline; prove the access pattern first.

## Lane Packing

- `hls_vector.h` requires an explicit lane width and packing intent.
- Packed-lane logic still needs a scalar cleanup rule for non-multiple lengths or boundary tiles.
- Treat lane packing as a data-layout decision, not a generic speed hint.
