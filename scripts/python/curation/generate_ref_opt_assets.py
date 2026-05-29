#!/usr/bin/env python3
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def skill_root() -> Path:
    return Path(__file__).resolve().parents[3]


def references_dir() -> Path:
    return skill_root() / "references"


def examples_dir() -> Path:
    return skill_root() / "assets" / "examples"


def templates_dir() -> Path:
    return skill_root() / "assets" / "templates"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def outputs(top: str) -> list[dict[str, str]]:
    return [
        {"path": f"src/{top}.h", "kind": "header", "language": "cpp"},
        {"path": f"src/{top}.cpp", "kind": "source", "language": "cpp"},
        {"path": f"tb/{top}_tb.cpp", "kind": "testbench", "language": "cpp"},
        {"path": "hls_config.cfg", "kind": "config", "language": "ini"},
    ]


def board_stub(reason: str) -> dict[str, Any]:
    return {"board_acceptance": {"profile": "not_board_runnable", "reason": reason}}


def axi4_profile() -> dict[str, Any]:
    return {
        "axi4_variant": "axi4_full",
        "role": "master",
        "read_write_mode": "read_write",
        "data_width": 32,
        "addr_width": 32,
        "id_width": 1,
        "burst_support": False,
        "clock_reset_domain": {"clock": "ap_clk", "reset": "ap_rst_n"},
    }


def axis_profile() -> dict[str, Any]:
    return {
        "keep_ready": True,
        "keep_last": True,
        "data_width": 32,
        "clock_reset_domain": {"clock": "ap_clk", "reset": "ap_rst_n"},
    }


def make_spec(
    *,
    name: str,
    top: str,
    pattern: str,
    description: str,
    interface_family: str,
    arguments: list[dict[str, Any]],
    metadata: dict[str, Any],
    confirmation_notes: str,
    behavior: list[str],
    constraints: list[str],
    headers: list[str],
    pragmas: list[str],
    cfg_entries: list[str] | None = None,
    performance: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = axis_profile() if interface_family == "axi_stream" else axi4_profile()
    return {
        "name": name,
        "target": "hls",
        "design_requirements": {
            "target": "hls",
            "pipeline_required": True,
            "streamability": "streamable",
            "interface_family": interface_family,
            "interface_profile": profile,
            "confirmed_by_user": True,
            "confirmation_notes": confirmation_notes,
        },
        "streamability": "streamable",
        "interface_family": interface_family,
        "interface_profile": profile,
        "pipeline_required": True,
        "codegen_plan_required": True,
        "description": description,
        "interfaces": {"top_function": top, "arguments": arguments, "control": "s_axilite"},
        "behavior": behavior,
        "clock": {"period_ns": 8.0, "uncertainty_ns": 0.8},
        "reset": {"strategy": "tool_default"},
        "constraints": constraints,
        "outputs": outputs(top),
        "notes": [],
        "subfunctions": [],
        "workflow": workflow or board_stub("Curated template stays validation-focused and does not assume a board host harness."),
        "performance": performance or {"target_ii": 1},
        "hls_profile": {
            "example_pattern": pattern,
            "allowed_libraries": headers,
            "required_headers": headers,
            "required_pragmas": pragmas,
            "required_metadata_fields": list(metadata.keys()),
            "metadata": metadata,
            "forbidden_combinations": [],
            "required_cfg_entries": cfg_entries or ["clock=8.0"],
        },
    }


def array_vectors() -> list[dict[str, Any]]:
    return [
        {"id": "case_nominal", "inputs": {"input": [1, 2, 3, 4], "length": 4}, "expected_outputs": {"output": [2, 3, 4, 5]}},
        {"id": "case_boundary", "inputs": {"input": [0, 7], "length": 2}, "expected_outputs": {"output": [1, 8]}},
    ]


def axis_vectors() -> list[dict[str, Any]]:
    return [
        {"id": "case_nominal", "inputs": {"in_stream": [1, 1, 2], "length": 3}, "expected_outputs": {"out_stream": [2, 2, 3]}},
        {"id": "case_boundary", "inputs": {"in_stream": [0, 15], "length": 2}, "expected_outputs": {"out_stream": [1, 16]}},
    ]


def binary_vectors() -> list[dict[str, Any]]:
    return [
        {"id": "case_nominal", "inputs": {"input_a": [1, 2, 3], "input_b": [4, 5, 6], "length": 3}, "expected_outputs": {"output": [5, 7, 9]}},
        {"id": "case_boundary", "inputs": {"input_a": [9, 0], "input_b": [1, 7], "length": 2}, "expected_outputs": {"output": [10, 7]}},
    ]


def template_payloads() -> dict[str, dict[str, Any]]:
    axi_mem = [
        {"name": "input", "type": "const ap_uint<32> *", "direction": "input", "interface": "m_axi", "bundle": "gmem0", "depth": 256},
        {"name": "output", "type": "ap_uint<32> *", "direction": "output", "interface": "m_axi", "bundle": "gmem1", "depth": 256},
        {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
    ]
    axis_mem = [
        {"name": "in_stream", "type": "hls::stream<ap_uint<32> >&", "direction": "input", "interface": "axis"},
        {"name": "out_stream", "type": "hls::stream<ap_uint<32> >&", "direction": "output", "interface": "axis"},
        {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
    ]
    binary_mem = [
        {"name": "input_a", "type": "const ap_uint<32> *", "direction": "input", "interface": "m_axi", "bundle": "gmem_a", "depth": 256},
        {"name": "input_b", "type": "const ap_uint<32> *", "direction": "input", "interface": "m_axi", "bundle": "gmem_b", "depth": 256},
        {"name": "output", "type": "ap_uint<32> *", "direction": "output", "interface": "m_axi", "bundle": "gmem_out", "depth": 256},
        {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
    ]

    return {
        "hls_fir_pipeline_template.json": make_spec(
            name="fir_pipeline_template",
            top="fir_pipeline_kernel",
            pattern="fir",
            description="Template for a memory-backed FIR sample pipeline kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"tap_count": 16, "coefficient_symmetry": "none", "structure_style": "direct_form", "interface_style": "memory_mapped", "ii_target": 1},
            confirmation_notes="Tap count, FIR structure, interface style, and II target have been confirmed.",
            behavior=["Replace the placeholder datapath with the confirmed FIR convolution semantics.", "Keep the FIR sample loop reviewable against the II target."],
            constraints=["Do not change FIR structure style without updated metadata.", "Keep local delay-line comments aligned with the confirmed tap count."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_fir_symmetric_template.json": make_spec(
            name="fir_symmetric_template",
            top="fir_symmetric_kernel",
            pattern="fir",
            description="Template for a symmetric-coefficient FIR kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"tap_count": 16, "coefficient_symmetry": "symmetric", "structure_style": "direct_form", "interface_style": "memory_mapped", "ii_target": 1},
            confirmation_notes="Symmetric FIR coefficient reuse has been confirmed.",
            behavior=["Preserve the symmetric coefficient reuse story in comments and helper structure."],
            constraints=["Do not fall back to unconstrained FIR comments when symmetry is explicit."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_fir_axis_template.json": make_spec(
            name="fir_axis_template",
            top="fir_axis_kernel",
            pattern="fir",
            description="Template for an AXI-Stream FIR kernel.",
            interface_family="axi_stream",
            arguments=deepcopy(axis_mem),
            metadata={"tap_count": 16, "coefficient_symmetry": "none", "structure_style": "streaming_direct_form", "interface_style": "axi_stream", "ii_target": 1},
            confirmation_notes="AXI-Stream FIR surface and sample-rate target have been confirmed.",
            behavior=["Preserve one output token contract per confirmed sample semantics."],
            constraints=["Keep stream framing comments aligned with the interface style metadata."],
            headers=["ap_int.h", "hls_stream.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_fir_dataflow_template.json": make_spec(
            name="fir_dataflow_template",
            top="fir_dataflow_kernel",
            pattern="fir",
            description="Template for a staged FIR dataflow kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"tap_count": 16, "coefficient_symmetry": "none", "structure_style": "dataflow_staged", "interface_style": "memory_mapped", "ii_target": 1},
            confirmation_notes="DATAFLOW FIR staging has been confirmed.",
            behavior=["Keep read, FIR compute, and write stages explicit."],
            constraints=["Do not claim DATAFLOW correctness without explicit stage-boundary comments."],
            headers=["ap_int.h", "hls_stream.h"],
            pragmas=["#pragma HLS DATAFLOW", "#pragma HLS PIPELINE II=1"],
        ),
        "hls_fft_fixed_point_template.json": make_spec(
            name="fft_fixed_point_template",
            top="fft_fixed_point_kernel",
            pattern="fft",
            description="Template for a fixed-point FFT or DFT-style transform kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"point_count": 64, "scaling_strategy": "none", "twiddle_representation": "q1_15_table", "complex_data_mode": "packed_iq", "error_tolerance": "1 lsb"},
            confirmation_notes="FFT point count, twiddle table, and fixed-point tolerance have been confirmed.",
            behavior=["Keep the placeholder transform comments aligned with the confirmed point count and twiddle policy.", "Document the error tolerance in the future testbench comparison."],
            constraints=["Do not silently change the scaling strategy.", "Keep packed-IQ assumptions explicit."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1", "#pragma HLS ARRAY_PARTITION variable=twiddle complete dim=1"],
        ),
        "hls_fft_scaled_template.json": make_spec(
            name="fft_scaled_template",
            top="fft_scaled_kernel",
            pattern="fft",
            description="Template for a per-stage scaled FFT kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"point_count": 64, "scaling_strategy": "per_stage_scale", "twiddle_representation": "q1_15_table", "complex_data_mode": "packed_iq", "error_tolerance": "1 lsb"},
            confirmation_notes="Per-stage FFT scaling has been confirmed.",
            behavior=["Preserve the per-stage scaling story and tolerance-aware verification intent."],
            constraints=["Do not mix per-stage scaling and block-floating assumptions."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_fft_power_spectrum_template.json": make_spec(
            name="fft_power_spectrum_template",
            top="fft_power_spectrum_kernel",
            pattern="fft",
            description="Template for a power-spectrum FFT kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"point_count": 64, "scaling_strategy": "window_then_power", "twiddle_representation": "q1_15_table", "complex_data_mode": "real_input_hann", "error_tolerance": "0.004"},
            confirmation_notes="FFT power-spectrum flow and tolerance have been confirmed.",
            behavior=["Keep the window-power-spectrum intent explicit in comments and validation notes."],
            constraints=["Do not erase the real-input or window assumptions from the asset."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_cordic_rotation_template.json": make_spec(
            name="cordic_rotation_template",
            top="cordic_rotation_kernel",
            pattern="cordic",
            description="Template for a CORDIC rotation-mode kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"cordic_mode": "rotation", "iteration_count": 16, "angle_range": "[-pi, pi]", "fixed_point_format": "ap_fixed<24,4>", "error_tolerance": "0.004"},
            confirmation_notes="CORDIC rotation mode, angle range, and fixed-point format have been confirmed.",
            behavior=["Keep the tolerance-aware rotation contract visible in comments and future testbench text."],
            constraints=["Do not hide quadrant normalization assumptions."],
            headers=["ap_int.h", "ap_fixed.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_cordic_vectoring_template.json": make_spec(
            name="cordic_vectoring_template",
            top="cordic_vectoring_kernel",
            pattern="cordic",
            description="Template for a CORDIC vectoring-mode kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"cordic_mode": "vectoring", "iteration_count": 16, "angle_range": "[-pi, pi]", "fixed_point_format": "ap_fixed<24,4>", "error_tolerance": "0.004"},
            confirmation_notes="CORDIC vectoring mode has been confirmed.",
            behavior=["Preserve magnitude-phase interpretation comments and tolerance-aware validation intent."],
            constraints=["Keep vectoring mode explicit instead of generic trig wording."],
            headers=["ap_int.h", "ap_fixed.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_cordic_sincos_template.json": make_spec(
            name="cordic_sincos_template",
            top="cordic_sincos_kernel",
            pattern="cordic",
            description="Template for a CORDIC sin/cos generator.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"cordic_mode": "sincos", "iteration_count": 16, "angle_range": "[-pi, pi]", "fixed_point_format": "ap_fixed<24,4>", "error_tolerance": "0.004"},
            confirmation_notes="CORDIC sin/cos generation has been confirmed.",
            behavior=["Keep tolerance-aware sin/cos comparison intent explicit."],
            constraints=["Do not hide fixed-point format assumptions."],
            headers=["ap_int.h", "ap_fixed.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_cordic_axis_template.json": make_spec(
            name="cordic_axis_template",
            top="cordic_axis_kernel",
            pattern="cordic",
            description="Template for an AXI-Stream CORDIC kernel.",
            interface_family="axi_stream",
            arguments=deepcopy(axis_mem),
            metadata={"cordic_mode": "sincos", "iteration_count": 16, "angle_range": "[-pi, pi]", "fixed_point_format": "ap_fixed<24,4>", "error_tolerance": "0.004"},
            confirmation_notes="AXIS CORDIC streaming surface has been confirmed.",
            behavior=["Preserve stream framing and tolerance-aware trig expectations."],
            constraints=["Keep AXIS boundaries explicit."],
            headers=["ap_int.h", "ap_fixed.h", "hls_stream.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_matmul_blocked_template.json": make_spec(
            name="matmul_blocked_template",
            top="matmul_blocked_kernel",
            pattern="matmul",
            description="Template for a blocked matrix multiply kernel.",
            interface_family="axi4",
            arguments=deepcopy(binary_mem),
            metadata={"tile_shape": "4x4x4", "layout": "row_major", "accumulator_type": "ap_int<40>", "memory_schedule": "blocked", "ii_target": 1},
            confirmation_notes="Blocked matmul tile shape and accumulator strategy have been confirmed.",
            behavior=["Preserve blocked local-buffer roles in comments and pragmas."],
            constraints=["Do not blur tile shape and memory schedule facts."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1", "#pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1"],
        ),
        "hls_matmul_partitioned_template.json": make_spec(
            name="matmul_partitioned_template",
            top="matmul_partitioned_kernel",
            pattern="matmul",
            description="Template for a partitioned matrix multiply kernel.",
            interface_family="axi4",
            arguments=deepcopy(binary_mem),
            metadata={"tile_shape": "4x4x4", "layout": "row_major", "accumulator_type": "ap_int<40>", "memory_schedule": "partitioned_tiles", "ii_target": 1},
            confirmation_notes="Partitioned matmul tile schedule has been confirmed.",
            behavior=["Keep local partitioning motivation explicit."],
            constraints=["Do not collapse the partitioned schedule into the blocked baseline."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1", "#pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1"],
        ),
        "hls_matmul_dataflow_template.json": make_spec(
            name="matmul_dataflow_template",
            top="matmul_dataflow_kernel",
            pattern="matmul",
            description="Template for a load-compute-store matrix multiply kernel.",
            interface_family="axi4",
            arguments=deepcopy(binary_mem),
            metadata={"tile_shape": "4x4x4", "layout": "row_major", "accumulator_type": "ap_int<40>", "memory_schedule": "load_compute_store", "ii_target": 1},
            confirmation_notes="Matmul load-compute-store schedule has been confirmed.",
            behavior=["Keep read, compute, and write stages explicit in comments and pragmas."],
            constraints=["Do not claim DATAFLOW without named stage boundaries."],
            headers=["ap_int.h", "hls_stream.h"],
            pragmas=["#pragma HLS DATAFLOW", "#pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1"],
        ),
        "hls_prefix_basic_template.json": make_spec(
            name="prefix_basic_template",
            top="prefix_basic_kernel",
            pattern="prefix_scan",
            description="Template for a basic inclusive prefix-scan kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"block_size": 1, "scan_mode": "inclusive", "offset_propagation": "none", "latency_strategy": "sequential_chain", "boundary_policy": "clamp_length"},
            confirmation_notes="Basic prefix-scan recurrence has been confirmed.",
            behavior=["Preserve the inclusive scan contract and recurrence story."],
            constraints=["Do not convert inclusive scan semantics into a generic reduction."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_prefix_blocked_template.json": make_spec(
            name="prefix_blocked_template",
            top="prefix_blocked_kernel",
            pattern="prefix_scan",
            description="Template for a blocked prefix-scan kernel.",
            interface_family="axi4",
            arguments=deepcopy(axi_mem),
            metadata={"block_size": 16, "scan_mode": "inclusive", "offset_propagation": "block_offsets", "latency_strategy": "blocked_parallel", "boundary_policy": "clamp_length"},
            confirmation_notes="Blocked prefix-scan offset propagation has been confirmed.",
            behavior=["Keep local scan and block-offset propagation explicit."],
            constraints=["Do not hide block-offset correctness assumptions."],
            headers=["ap_int.h"],
            pragmas=["#pragma HLS PIPELINE II=1", "#pragma HLS UNROLL factor=4"],
        ),
        "hls_rle_axis_encode_template.json": make_spec(
            name="rle_axis_encode_template",
            top="rle_axis_encode_kernel",
            pattern="rle_axis",
            description="Template for an AXI-Stream RLE encoder.",
            interface_family="axi_stream",
            arguments=[
                {"name": "in_stream", "type": "hls::stream<ap_axiu<8,0,0,0> >&", "direction": "input", "interface": "axis"},
                {"name": "out_stream", "type": "hls::stream<ap_axiu<16,0,0,0> >&", "direction": "output", "interface": "axis"},
                {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
            ],
            metadata={"frame_boundary_mode": "tlast_terminated", "tlast_policy": "propagate_final_pair", "run_length_limit": 255, "empty_frame_policy": "reject", "ii_target": 1},
            confirmation_notes="RLE AXIS encode frame and TLAST policy have been confirmed.",
            behavior=["Preserve TLAST, TKEEP, and TSTRB handling explicitly in the generated scaffold comments."],
            constraints=["Do not claim empty-frame behavior without metadata."],
            headers=["ap_int.h", "ap_axi_sdata.h", "hls_stream.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
        "hls_rle_axis_decode_template.json": make_spec(
            name="rle_axis_decode_template",
            top="rle_axis_decode_kernel",
            pattern="rle_axis",
            description="Template for an AXI-Stream RLE decoder.",
            interface_family="axi_stream",
            arguments=[
                {"name": "in_stream", "type": "hls::stream<ap_axiu<16,0,0,0> >&", "direction": "input", "interface": "axis"},
                {"name": "out_stream", "type": "hls::stream<ap_axiu<8,0,0,0> >&", "direction": "output", "interface": "axis"},
                {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
            ],
            metadata={"frame_boundary_mode": "tlast_terminated", "tlast_policy": "propagate_final_symbol", "run_length_limit": 255, "empty_frame_policy": "reject", "ii_target": 1},
            confirmation_notes="RLE AXIS decode frame and TLAST policy have been confirmed.",
            behavior=["Preserve explicit AXIS boundary handling in comments and validation."],
            constraints=["Keep run splitting and final-symbol framing explicit."],
            headers=["ap_int.h", "ap_axi_sdata.h", "hls_stream.h"],
            pragmas=["#pragma HLS PIPELINE II=1"],
        ),
    }


def docs_payloads() -> dict[str, str]:
    return {
        "ref-opt-catalog.md": """# ref/Opt Catalog

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
""",
        "hls-fir-template-family.md": """# HLS FIR Template Family

This note curates the FIR-related material from `ref/Opt` into durable HLS guidance.
It intentionally keeps algorithm-family facts while discarding the dialogue scaffolding in the raw source notes.

## Scope From ref/Opt

- `Template 1-10` cover basic FIR pipeline structure, loop unroll, complete partition, symmetric coefficients, transposed structure, AXIS streaming, DATAFLOW staging, double buffering, constant-coefficient shift-add, and packed complex I/Q handling.
- In the first asset wave, the promoted first-class variants are `pipeline`, `symmetric`, `AXIS`, and `dataflow`.
- `transposed`, `double-buffer`, `shift-add`, and `complex I/Q` stay reference-only until the validator and mock assets grow enough to model them honestly.

## Reusable Design Facts

- Confirm FIR `tap count` before choosing unroll, complete partition, or direct-form versus transposed structure.
- Treat `coefficient symmetry` as a structural choice: symmetric filters justify pairwise sample reuse and fewer multipliers.
- Treat the `structure style` as a first-class decision. Direct-form, transposed, and staged DATAFLOW FIR kernels have different local-buffer and II behavior.
- `AXIS` FIR variants need an explicit packet or sample-boundary story; do not infer TLAST behavior from a generic stream surface.
- Constant-coefficient shift-add and complex I/Q FIR structures are optimization or specialization branches, not default codegen baselines.

## Assetization Map

- `template-ready`: FIR pipeline, FIR symmetric, FIR AXIS, FIR dataflow.
- `reference-only`: FIR transposed, FIR PIPO double-buffer, FIR shift-add, FIR complex I/Q.
- `validator-ready`: none beyond the first-batch static profile and testbench checks.
""",
        "hls-fft-cordic-template-family.md": """# HLS FFT And CORDIC Template Family

This note curates the `DFT / FFT / CORDIC` material from `ref/Opt` into one reusable transform-oriented family note.
It keeps only durable HLS facts and records the one known errata explicitly.

## Scope From ref/Opt

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
""",
        "hls-stream-codec-template-family.md": """# HLS Stream Codec Template Family

This note curates the stream-codec material from `ref/Opt` into reusable AXIS-oriented guidance.
It keeps the RLE assets first-class and keeps LZ77 reference-first.

## Scope From ref/Opt

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
""",
        "hls-linear-algebra-template-family.md": """# HLS Linear Algebra Template Family

This note curates the matrix, sparse, and scan material from `ref/Opt` into one reusable linear-algebra family note.

## Scope From ref/Opt

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
""",
    }


def example_overrides() -> dict[str, tuple[str, list[dict[str, Any]]]]:
    return {
        "hls_fir_pipeline_spec.json": ("hls_fir_pipeline_template.json", array_vectors()),
        "hls_fir_symmetric_spec.json": ("hls_fir_symmetric_template.json", array_vectors()),
        "hls_fir_axis_spec.json": ("hls_fir_axis_template.json", axis_vectors()),
        "hls_fir_dataflow_spec.json": ("hls_fir_dataflow_template.json", array_vectors()),
        "hls_fft_fixed_point_spec.json": ("hls_fft_fixed_point_template.json", array_vectors()),
        "hls_fft_scaled_spec.json": ("hls_fft_scaled_template.json", array_vectors()),
        "hls_fft_power_spectrum_spec.json": ("hls_fft_power_spectrum_template.json", array_vectors()),
        "hls_cordic_rotation_spec.json": ("hls_cordic_rotation_template.json", array_vectors()),
        "hls_cordic_vectoring_spec.json": ("hls_cordic_vectoring_template.json", array_vectors()),
        "hls_cordic_sincos_spec.json": ("hls_cordic_sincos_template.json", array_vectors()),
        "hls_cordic_axis_spec.json": ("hls_cordic_axis_template.json", axis_vectors()),
        "hls_matmul_blocked_spec.json": ("hls_matmul_blocked_template.json", binary_vectors()),
        "hls_matmul_partitioned_spec.json": ("hls_matmul_partitioned_template.json", binary_vectors()),
        "hls_matmul_dataflow_spec.json": ("hls_matmul_dataflow_template.json", binary_vectors()),
        "hls_prefix_basic_spec.json": ("hls_prefix_basic_template.json", array_vectors()),
        "hls_prefix_blocked_spec.json": ("hls_prefix_blocked_template.json", array_vectors()),
        "hls_rle_axis_encode_spec.json": ("hls_rle_axis_encode_template.json", axis_vectors()),
        "hls_rle_axis_decode_spec.json": ("hls_rle_axis_decode_template.json", axis_vectors()),
    }


def make_reference_only_examples() -> dict[str, dict[str, Any]]:
    spmv = make_spec(
        name="spmv_csr_reference",
        top="spmv_csr_reference_kernel",
        pattern="spmv_csr",
        description="Reference-first CSR SpMV example kept out of Tier 1 runtime pattern support.",
        interface_family="axi4",
        arguments=[
            {"name": "input_a", "type": "const ap_uint<32> *", "direction": "input", "interface": "m_axi", "bundle": "gmem_a", "depth": 256},
            {"name": "input_b", "type": "const ap_uint<32> *", "direction": "input", "interface": "m_axi", "bundle": "gmem_b", "depth": 256},
            {"name": "output", "type": "ap_uint<32> *", "direction": "output", "interface": "m_axi", "bundle": "gmem_out", "depth": 256},
            {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
        ],
        metadata={"representation": "csr", "tier": "reference_first"},
        confirmation_notes="Keep CSR SpMV as a reference-first example until sparse validation coverage grows.",
        behavior=["Describe CSR row_ptr, col_idx, and value scheduling without claiming first-class runtime support."],
        constraints=["Do not upgrade this example into default mock or provider behavior yet."],
        headers=["ap_int.h"],
        pragmas=["#pragma HLS PIPELINE II=1"],
    )
    spmv["workflow"]["mock_vectors"] = [
        {"id": "case_nominal", "inputs": {"input_a": [1, 2], "input_b": [3, 4], "length": 2}, "expected_outputs": {"output": [4, 6]}},
        {"id": "case_boundary", "inputs": {"input_a": [0], "input_b": [5], "length": 1}, "expected_outputs": {"output": [5]}},
    ]

    lz77 = make_spec(
        name="lz77_reference",
        top="lz77_reference_kernel",
        pattern="lz77",
        description="Reference-first LZ77 example kept out of Tier 1 runtime pattern support.",
        interface_family="axi_stream",
        arguments=[
            {"name": "in_stream", "type": "hls::stream<ap_uint<32> >&", "direction": "input", "interface": "axis"},
            {"name": "out_stream", "type": "hls::stream<ap_uint<32> >&", "direction": "output", "interface": "axis"},
            {"name": "length", "type": "int", "direction": "input", "interface": "s_axilite"},
        ],
        metadata={"representation": "token_stream", "tier": "reference_first"},
        confirmation_notes="Keep LZ77 as a reference-first example until dictionary-window validation coverage grows.",
        behavior=["Describe tokenized sliding-window compression boundaries without claiming first-class runtime support."],
        constraints=["Do not upgrade this example into default mock or provider behavior yet."],
        headers=["ap_int.h", "hls_stream.h"],
        pragmas=["#pragma HLS PIPELINE II=1"],
    )
    lz77["workflow"]["mock_vectors"] = axis_vectors()
    return {
        "hls_spmv_csr_reference_spec.json": spmv,
        "hls_lz77_reference_spec.json": lz77,
    }


def render_assets() -> None:
    for name, text in docs_payloads().items():
        write_text(references_dir() / name, text)

    templates = template_payloads()
    for name, payload in templates.items():
        write_json(templates_dir() / name, payload)

    for name, (template_name, vectors) in example_overrides().items():
        payload = deepcopy(templates[template_name])
        payload["workflow"] = deepcopy(payload.get("workflow") or {})
        payload["workflow"]["mock_vectors"] = deepcopy(vectors)
        payload["name"] = payload["name"].removesuffix("_template")
        payload["interfaces"]["top_function"] = payload["interfaces"]["top_function"].removesuffix("_template")
        payload["outputs"] = outputs(payload["interfaces"]["top_function"])
        write_json(examples_dir() / name, payload)

    for name, payload in make_reference_only_examples().items():
        write_json(examples_dir() / name, payload)


if __name__ == "__main__":
    render_assets()
