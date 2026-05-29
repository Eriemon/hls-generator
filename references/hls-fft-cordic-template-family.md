# HLS FFT And CORDIC Template Family

This note records reusable `DFT / FFT / CORDIC` HLS guidance for the skill.
It keeps only durable HLS facts and records the one known errata explicitly.

## Scope

- `Template 11-19` cover DFT pipeline/full-unroll, radix-2 FFT, per-stage scaling, AXIS FFT framing, block floating point, real-input Hann window power spectrum, and peak-detection extensions.
- `Template 23-28` cover `hls::stream_of_blocks` FFT and the CORDIC rotation, vectoring, sin/cos, and AXIS variants.
- The first asset wave promotes `FFT fixed-point baseline`, `FFT per-stage scaling`, `FFT power spectrum`, `CORDIC rotation`, `CORDIC vectoring`, `CORDIC sin/cos`, and `CORDIC AXIS`.

## Known Errata

- `Template-2` contains a twiddle-table correction in the multi-frame FFT lineage.
- Curated knowledge keeps only the corrected twiddle facts; raw pre-correction text must not be copied into reusable guidance.

## Reusable Design Facts

- Confirm `point count`, `scaling strategy`, and `twiddle representation` before generating FFT or DFT code.
- `Complex data mode` is part of the interface contract: packed IQ, split arrays, or real-input transforms lead to different helper structures and testbench expectations.
- `Error tolerance` must be explicit for both FFT and CORDIC assets; the testbench must carry a tolerance-aware comparison instead of exact-bit equality by default.
- `CORDIC mode`, `iteration count`, `angle range`, and `fixed-point format` jointly define latency and error behavior.
- AXIS transform variants remain more constrained than memory-backed variants because packet boundaries and rate matching must be explicit.

## Assetization Map

- `template-ready`: FFT fixed-point baseline, FFT scaled, FFT power spectrum, CORDIC rotation, CORDIC vectoring, CORDIC sin/cos, CORDIC AXIS.
- `reference-only`: full-unroll DFT, AXIS frame FFT, block floating point FFT, peak-detection extensions, `hls::stream_of_blocks` FFT, heavily unrolled CORDIC.
- `validator-ready`: first-batch tolerance and AXIS-static checks only.
