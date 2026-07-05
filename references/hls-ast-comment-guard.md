# HLS AST comment guard

Load this reference before changing comment-only rewrite validation, AST provider selection, or parser fallback behavior.

## Goal

A comment-only edit must not change HLS behavior. The guard checks that the generated C/C++ token stream is unchanged after comments are removed and that the normalized AST remains unchanged when an AST provider is available.

## Provider order

1. `clang++` / `clang` with `-Xclang -ast-dump=json` is the primary provider. The guard adds fake HLS headers for common Vitis types such as `ap_uint`, `ap_fixed`, `hls::stream`, `ap_axiu`, and `hls::task`, then parses in C++17 syntax-only mode with unknown pragma warnings disabled.
2. `tree-sitter-cpp` is the optional fallback when Clang is unavailable. It provides a concrete syntax tree and rejects ERROR nodes.
3. `pycparser` is the last fallback for C-like `.c`/`.h` files only; it does not cover normal C++ HLS sources.

If no provider is available, parse-after-only validation reports a warning. Comment-only comparison with a baseline reports an error because the requested proof cannot be produced.

## Normalization

The Clang JSON dump contains source locations, offsets, line/column data, and run-specific declaration pointer ids. These fields are removed or normalized before fingerprinting so blank lines and comments do not create false differences. The code-token fingerprint is still checked separately to catch any non-comment edit before AST comparison.

## Pragmas

Standard C/C++ ASTs do not model every vendor pragma as a semantic AST node. The guard therefore combines AST parsing with textual `#pragma HLS` comment-placement checks in `comment_policy.py`. This keeps comments, whitespace, and pragma intent reviewable even when the compiler treats vendor pragmas as extensions.
