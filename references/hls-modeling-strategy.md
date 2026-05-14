# HLS Modeling Strategy Notes

Source note: distilled from imported starter HLS examples and converted into stable skill guidance. Keep this file concise and reusable.

## Variable-Bound Loops

- Treat loops with runtime bounds as a modeling risk first and an optimization target second.
- Require an explicit statement of the maximum safe bound before suggesting aggressive unroll or complete partition strategies.
- Use `loop_tripcount` or equivalent report guidance only to improve synthesis visibility; do not pretend it changes functional semantics.
- When the bound is data-dependent, prefer a pipeline-friendly sequential structure over speculative parallel expansion.

## Precision And Numeric Intent

- Prefer `ap_int`, `ap_uint`, and `ap_fixed` only when the range, saturation, and rounding intent are understood and justified.
- Keep floating-point use explicit. If the algorithm needs floating-point compatibility, say so and preserve it rather than silently replacing it with fixed-point.
- For fixed-point kernels, document the integer bits, fractional bits, and failure mode if range assumptions are violated.
- Treat mixed numeric domains as a verification hotspot; require vectors that exercise rounding and saturation edges.

## Pointers, Templates, And Vectors

- Pointer-based kernels must make aliasing expectations explicit. If the design assumes independent memory channels, the spec and interface profile should say so.
- Template use is acceptable only when it clarifies reusable hardware structure, not when it hides critical interface or width decisions.
- Vector-style code is useful when adjacent-lane intent is stable and maps cleanly to a packed datapath; do not introduce it as decoration.
- Avoid modeling patterns that depend on host-only conveniences such as dynamic allocation, exceptions, or unsupported STL containers.

## Conditional Pragmas And Structural Choice

- Conditional optimization controls are a maintenance tool, not a shortcut around understanding the access pattern.
- If a pragma is conditional, the reason for each branch must be tied to an observable hardware tradeoff such as II, memory banking, or latency.
- Do not stack multiple optimization classes onto a weak baseline. First prove the sequential model is correct and self-checking.

## Modeling Checklist

- Confirm loop bounds, numeric range, and pointer alias assumptions before generation.
- Keep the baseline code readable enough that a future maintainer can still understand the unoptimized data path.
- Add performance directives only after the modeling assumptions are stable and test vectors cover the corner cases they rely on.
