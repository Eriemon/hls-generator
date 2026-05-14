# HLS Device Migration Strategy Notes

Source note: distilled from imported HLS design examples that compare the same kernel across target devices. This file records portability and QoR review rules.

## Migration Discipline

- Treat migration as a QoR comparison on a stable kernel, not as a prompt to rewrite interfaces or arithmetic silently.
- Keep top-level interfaces, control behavior, and numeric intent stable while retargeting parts.
- Re-run C simulation and synthesis after each device change before drawing conclusions.

## What To Compare

- Compare interval, latency, achieved clock, timing slack, and resource deltas across the target devices.
- Track DSP, LUT, FF, BRAM, and URAM changes together; a faster interval may still be a poor trade if area growth is unacceptable.
- Record whether the limiting factor changed: arithmetic latency, bandwidth, or scheduling pressure.

## Numeric Strategy And DSP Generations

- Floating-point QoR can change substantially across DSP generations; do not assume the same resource efficiency on every device family.
- Fixed-point retargeting still requires verification of range, rounding, and saturation assumptions after migration.
- If a migration result suggests changing numeric strategy, treat that as a new design decision with new vectors and review gates.

## Migration Review Checklist

- Preserve functional behavior first, then compare QoR.
- Explain every major delta with an architectural reason when possible.
- Keep migration guidance as analysis and validation policy, not as a separate execution flow promise.
