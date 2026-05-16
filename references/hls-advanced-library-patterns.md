# HLS Advanced Library Pattern Notes

Load this file before changing prompt rules, validation policy, or templates for advanced HLS library families.

## hls_task.h

- Use only when task boundaries, restart semantics, channel depth, and channel ownership are explicit.
- Do not describe a task graph as dataflow alone; the task contract must still be reviewable.

## hls_streamofblocks.h

- Use only when block size and block ownership are explicit.
- Treat block streams as a block-level contract, not a scalar FIFO with a different name.

## hls_directio.h

- Use only when free-running behavior and control protocol are explicit.
- Keep `ap_ctrl_none` or equivalent control behavior aligned with comments, reset semantics, and validation.

## hls_fence.h

- Use only when the ordering reason and ordering scope are explicit.
- A fence comment must explain the hazard being prevented; do not use ordering language as decoration.
