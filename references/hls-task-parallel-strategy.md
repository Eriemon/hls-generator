# HLS Task Parallel Strategy Notes

Source note: distilled from AMD official task-level and interface example material. This file records reusable rules, not example-directory names or one-off scripts.

## Control-Driven Versus Data-Driven Parallelism

- Use control-driven orchestration when stage order and explicit start/stop semantics are part of the contract.
- Use data-driven task decomposition only when producer/consumer boundaries are clean and the stream protocol is the real coordination mechanism.
- Do not blur the two styles in the prompt. The generator should know whether the design is primarily scheduled by control flow or by channel flow.

## Streams, Channels, And Restart Behavior

- `hls::stream`-based designs need explicit channel ownership and depth assumptions when stage rates can differ.
- Automatic restart or continuously running behavior must be treated as a first-class requirement, not inferred from a single pragma.
- If the design depends on persistent channels, document whether state is expected to survive across transactions.
- Distinguish packet-like streams with side-channel metadata from plain scalar streaming; the interface contract should name the difference.

## Task Regions And Dataflow Boundaries

- `DATAFLOW` is appropriate only when the task graph is already understandable as producer, transform, and consumer regions.
- Unique task regions should not share hidden mutable arrays or ambiguous ownership of control signals.
- If multiple memory masters or direct I/O handshakes appear inside a task graph, treat arbitration and backpressure as explicit design concerns.
- Do not claim task-level parallelism from syntax alone; verify that the chosen structure matches the throughput goal and interface semantics.

## Migration And Execution Surface

- Tcl, Python, and unified CLI flows are reference surfaces that explain how AMD tools can be driven, but the stable generated path for this skill remains the existing Tcl plus `.cfg` execution flow.
- Migration-oriented patterns should inform compatibility checks and documentation, not silently replace the currently supported execution mode.
- If a future implementation adds a new execution surface, it must come with dedicated smoke coverage and an explicit runtime switch.

## Parallelism Checklist

- Confirm whether the design is control-driven or data-driven before choosing pragmas.
- Make restart semantics, channel depth, and side-channel expectations explicit in the spec.
- Treat dataflow and task pragmas as architecture decisions backed by reports, not as generic speed-up hints.
