# HLS Stencil, Reduction, And GEMM Pattern Notes

Source note: distilled from imported compute-structure examples. This file records stable architecture rules, not tutorial names.

## Stencil And Window Patterns

- Confirm the window shape and border policy before choosing partitioning or buffering pragmas.
- A line buffer should explain which neighbors it preserves and why the chosen reuse strategy is safe.
- Never apply both `ARRAY_PARTITION` and `ARRAY_RESHAPE` to the same stencil line buffer.

## Reduction Trees

- Reduction trees need an explicit reduction operator, accumulator type, and tree shape.
- Unrolling a reduction without an accumulator policy hides overflow and timing risk.
- Keep the reduction comments tied to the actual accumulation structure, not just the final scalar result.

## Tiled GEMM

- Confirm tile shape, data layout, and accumulator type before adding local tile pragmas.
- Tile buffers should map to a visible reuse story: what is loaded, what is reused, and where accumulation happens.
- Treat GEMM-style tiling as a memory-structure decision plus an accumulation decision, not just loop syntax.
