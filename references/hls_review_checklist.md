# HLS Review Checklist

Before accepting generated HLS code, verify:

1. File headers identify source, header, testbench, kernel, or configuration role in Chinese.
2. Blank-line-separated code blocks have Chinese purpose comments before the lower block.
3. Local state and assignments have both above-line and right-side Chinese purpose comments unless a configured HLS multi-line exemption applies.
4. Every `#pragma HLS` comment explains hardware intent, not only the directive name.
5. Top function comments describe each port direction, protocol, bundle, depth/shape, unit, and side effect.
6. Loop comments state iteration boundary, transaction scope, read/write objects, and accumulation/comparison/throughput purpose.
7. Stream/DATAFLOW comments describe FIFO depth and producer-consumer stage relation.
8. Testbenches comment expected values, kernel calls, PASS/FAIL conditions, and vector hashes.
9. Comment-only rewrites pass both non-comment token fingerprint and AST fingerprint checks against the baseline.
10. No Vitis/Vitis HLS result is claimed unless `vitis-run` or `vitis_hls` actually ran.
