# ref/Opt Catalog

This catalog converts the local `ref/Opt` corpus into durable HLS-only reference facts.
It records what exists in the source notes, which items have errata, which numbers are missing, and which topics are reference-only versus assetized.

## Curation Rules

- Keep only durable HLS facts: algorithm family, interface shape, pragma intent, numeric strategy, and validation expectations.
- Strip dialogue scaffolding from the source corpus. Phrases such as follow-up confirmations, incremental generation prompts, or copy/paste warnings do not belong in skill knowledge.
- Record source gaps honestly. Missing template numbers stay `source missing`; they are not reconstructed.
- Record known errata explicitly. `Template-2.md` contains a twiddle-table correction for the multi-frame FFT family; only the corrected twiddle facts should flow into curated references.
- Assetization states use: `reference-only`, `example-ready`, `template-ready`, `validator-ready`.

## Template Index

| Number | Family | Title | Core Pragmas / Structures | Interface Shape | Numeric / Data Strategy | Status | Notes |
|---|---|---|---|---|---|---|---|
| 1 | FIR | Basic FIR pipeline | `PIPELINE II=1` | array / memory | integer baseline | reference-only | FIR baseline facts only. |
| 2 | FIR | FIR unroll factor=4 | `UNROLL`, partitioned taps | dual `m_axi` + control | fixed-point MAC | reference-only | Unroll pattern evidence. |
| 3 | FIR | FIR complete partition + full unroll | complete partition, adder tree | dual `m_axi` + control | fixed-point MAC | reference-only | High-parallel FIR guidance. |
| 4 | FIR | FIR symmetric coefficients | symmetry reduction, zero-tap pruning | dual `m_axi` + control | symmetric coefficient reuse | template-ready | Promoted to family assets. |
| 5 | FIR | FIR transposed structure | transposed datapath, pipeline | memory-backed | fixed-point MAC | reference-only | Structure guidance only. |
| 6 | FIR | FIR AXI-Stream | AXIS, streaming FIR | AXI-Stream | sample stream | template-ready | Promoted to family assets. |
| 7 | FIR | FIR dataflow load-compute-store | `DATAFLOW`, staged streams | memory-backed | staged FIR | template-ready | Promoted to family assets. |
| 8 | FIR | FIR PIPO double buffer | PIPO / double-buffer | memory-backed | staged FIR | reference-only | Dataflow buffering guidance. |
| 9 | FIR | FIR shift-add constants | shift-add constant mult | memory-backed | constant-coefficient optimization | reference-only | Optimization note only. |
| 10 | FIR | FIR complex I/Q | packed IQ, dual path | memory-backed | packed complex fixed-point | reference-only | Specialization note only. |
| 11 | DFT/FFT | DFT basic pipeline | inner-loop pipeline | memory-backed | packed complex fixed-point | reference-only | DFT baseline reference. |
| 12 | DFT/FFT | DFT full unroll | full unroll, adder tree | memory-backed | packed complex fixed-point | reference-only | Contrast to FFT structure. |
| 13 | DFT/FFT | FFT radix-2 basic | staged butterflies | memory-backed | packed complex fixed-point | template-ready | Promoted to FFT baseline assets. |
| 14 | DFT/FFT | FFT per-stage scaling | stage scaling | memory-backed | overflow-managed fixed-point | template-ready | Promoted to FFT scaled assets. |
| 15 | DFT/FFT | AXIS frame FFT | AXIS, per-stage scaling | AXI-Stream | frame FFT | reference-only | Guidance only in first batch. |
| 16 | DFT/FFT | FFT block floating point | BFP scaling | memory-backed | block floating point | reference-only | Future validator work. |
| 17 | DFT/FFT | Hann FFT power spectrum | window + FFT + power | memory-backed | real-input fixed-point | template-ready | Promoted to power-spectrum assets. |
| 18 | DFT/FFT | Hann FFT peak detection | power + peak detect | memory-backed | real-input fixed-point | reference-only | Folded into family guidance. |
| 19 | DFT/FFT | Multi-frame Hann FFT average peak | averaging + peak detect | memory-backed | real-input fixed-point | reference-only | Includes corrected twiddle lineage. |
| 20 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 21 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 22 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 23 | DFT/FFT | FFT `hls::stream_of_blocks` | block streaming | AXI-Stream / block stream | block FFT | reference-only | Advanced-library guidance only. |
| 24 | CORDIC | CORDIC rotation | iterative shift-add | scalar / memory | fixed-point angle rotation | template-ready | Promoted to CORDIC assets. |
| 25 | CORDIC | CORDIC pipelined/unrolled | unrolled CORDIC | scalar / memory | fixed-point angle rotation | reference-only | Optimization note only. |
| 26 | CORDIC | CORDIC vectoring | vectoring mode | scalar / memory | magnitude / phase | template-ready | Promoted to CORDIC assets. |
| 27 | CORDIC | CORDIC sin/cos | iterative sincos | scalar / memory | fixed-point trig | template-ready | Promoted to CORDIC assets. |
| 28 | CORDIC | CORDIC AXIS | AXIS sin/cos | AXI-Stream | fixed-point trig stream | template-ready | Promoted to CORDIC assets. |
| 29 | Matmul | Matrix multiply basic | triple loop, pipeline | memory-backed | integer MAC | reference-only | Baseline only. |
| 30 | Matmul | Matrix multiply blocked | local tiles | multi-`m_axi` | tiled integer MAC | template-ready | Promoted to matmul blocked assets. |
| 31 | Matmul | Matrix multiply partitioned | partition + unroll | multi-`m_axi` | tiled integer MAC | template-ready | Promoted to matmul partitioned assets. |
| 32 | Matmul | Matrix multiply dataflow | load-compute-store | multi-`m_axi` | tiled integer MAC | template-ready | Promoted to matmul dataflow assets. |
| 33 | Matmul | Matrix-vector multiply | pipelined reduction | memory-backed | integer MAC | reference-only | Folded into linear-algebra notes. |
| 34 | SpMV | SpMV CSR basic | CSR scan | memory-backed | sparse integer MAC | example-ready | Tier 2 reference-first example only. |
| 35 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 36 | SpMV | SpMV multi-PE | PE unroll, partition | memory-backed | sparse integer MAC | reference-only | Future Tier 2 upgrade target. |
| 37 | Prefix | Prefix sum basic | pipelined chain | memory-backed | integer accumulation | template-ready | Promoted to prefix baseline assets. |
| 38 | Prefix | Prefix sum blocked | blocked scan, offsets | memory-backed | integer accumulation | template-ready | Promoted to blocked prefix assets. |
| 39 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 40 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 41 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 42 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 43 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 44 | unknown | missing in source | n/a | n/a | n/a | reference-only | source missing |
| 45 | Stream Codec | RLE AXIS encode | AXIS, TLAST, pending output | AXI-Stream | byte/pair stream | template-ready | Promoted to stream-codec assets. |
| 46 | Stream Codec | RLE AXIS decode | AXIS, TLAST expansion | AXI-Stream | byte/pair stream | template-ready | Promoted to stream-codec assets. |
| 47 | Stream Codec | LZ77 basic | sliding window | memory-backed | token stream | example-ready | Tier 2 reference-first example only. |

## Coverage Snapshot

- `FIR`, `FFT`, `CORDIC`, `Matmul`, `Prefix`, and `RLE-AXIS` are the Tier 1 families promoted into curated references and first-batch assets.
- `SpMV CSR` and `LZ77` stay `example-ready` / `reference-only` in this first implementation wave.
- Missing numbers remain `source missing` until a real source document exists.
