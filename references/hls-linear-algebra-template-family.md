# HLS Linear Algebra Template Family

This note records reusable linear-algebra HLS guidance for matrix, sparse, and scan families.

## Scope

- `Template 29-33` cover dense matrix multiply and matrix-vector structures.
- `Template 34` and `Template 36` cover CSR-based SpMV baseline and multi-PE expansion.
- `Template 37-38` cover basic and blocked prefix scan.

## Reusable Design Facts

- Confirm `tile shape`, `layout`, `accumulator type`, and `memory schedule` before promoting a dense matmul structure.
- Treat `blocked` and `dataflow load-compute-store` as distinct schedule choices, not cosmetic pragma variants.
- `Prefix scan` needs explicit `scan mode`, `block size`, `offset propagation`, and `boundary policy` because the recurrence structure is part of correctness.
- `SpMV CSR` remains reference-first: row pointers, nonzero balance, and PE scheduling need richer mock and validation support before becoming a first-class pattern family.

## Assetization Map

- `template-ready`: matmul blocked, matmul partitioned, matmul dataflow, prefix basic, prefix blocked.
- `example-ready`: SpMV CSR reference spec.
- `reference-only`: matrix-vector multiply and multi-PE sparse scheduling.
