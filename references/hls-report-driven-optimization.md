# HLS Report-Driven Optimization Notes

Source note: distilled from imported HLS optimization examples and analysis walkthroughs. This file records stable optimization workflow rules.

## Baseline First

- Start from a sequential, readable baseline that already passes C simulation.
- Set a measurable target before touching pragmas: clock, interval, II, latency, or a resource ceiling.
- Do not stack optimizations onto an unverified baseline.

## Report Review Loop

- After every optimization step, inspect the new report before making another change.
- Use target II, achieved II, loop interval, timing slack, issue type, and interface bandwidth to decide the next move.
- Prefer one optimization class per iteration when practical so the effect is attributable.

## II Violation Triage

- If `PIPELINE II=1` fails, classify the blocker before adding directives: timing, recurrence, memory-port pressure, store/load bandwidth, or interface structure.
- Treat inferred inner-loop concurrency as a real architectural consequence of outer-loop pipelining.
- Use schedule-viewer evidence or equivalent report data to identify the exact load/store bottleneck.

## Bandwidth And Storage Decisions

- Use `ARRAY_RESHAPE` when adjacent elements should move together through a wider storage or interface word.
- Use `ARRAY_PARTITION` when the design needs more independent banks or element-level parallel access.
- Choose dimension, factor, and complete/block/cyclic style to match the observed access pattern, not aesthetic preference.
- Remove or revise directives that fail to improve the limiting report metric.
