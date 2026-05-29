# HLS Stream Codec Template Family

This note records reusable stream-codec HLS guidance with an AXIS-oriented focus.
It keeps the RLE assets first-class and keeps LZ77 reference-first.

## Scope

- `Template 45-46` describe AXI-Stream RLE encode and decode kernels with explicit `TLAST`, `TKEEP`, and `TSTRB` handling.
- `Template 47` introduces a basic LZ77 sliding-window encoder as a reference tokenization core.

## Reusable Design Facts

- For AXIS codecs, `frame boundary mode` and `TLAST policy` are part of the algorithm contract, not a transport afterthought.
- Explicitly confirm the `run-length limit` and how long runs split across output pairs.
- `Empty frame policy` must be explicit because raw AXIS alone does not represent every empty-frame convention cleanly.
- Keep `TKEEP` and `TSTRB` initialization explicit when using `ap_axiu` payloads.
- LZ77 remains a reference-only family until token semantics, window bounds, and validator coverage are richer.

## Assetization Map

- `template-ready`: RLE AXIS encode, RLE AXIS decode.
- `example-ready`: LZ77 basic reference spec.
- `reference-only`: deeper compression families and multi-stage dictionary codecs.
