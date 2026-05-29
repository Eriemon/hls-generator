"""Shared HLS pattern metadata and rule helpers."""

from __future__ import annotations

from typing import Any


PATTERN_RULES: dict[str, dict[str, Any]] = {
    "fir": {
        "label": "FIR filter family",
        "metadata_fields": {
            "tap_count": "Confirm the tap count before generating the FIR kernel.",
            "coefficient_symmetry": "Confirm whether the FIR coefficients are symmetric, antisymmetric, or unconstrained.",
            "structure_style": "Confirm the FIR structure style such as direct form, transposed, or systolic.",
            "interface_style": "Confirm whether the FIR surface is memory-mapped, AXI-Stream, or another interface style.",
            "ii_target": "Confirm the II target for the FIR sample loop before generation.",
        },
        "prompt_rules": [
            "For FIR patterns, make tap count, coefficient symmetry, structure style, interface style, and II target explicit before choosing PIPELINE, UNROLL, or DATAFLOW pragmas.",
            "Do not switch between direct-form, transposed, symmetric, or AXI-Stream FIR structures unless the confirmed metadata supports that structure choice.",
            "Explain how the confirmed tap count, symmetry, and II target shape the local buffer, multiply-accumulate schedule, and stream or memory interface behavior.",
        ],
    },
    "fft": {
        "label": "FFT/DFT transform family",
        "metadata_fields": {
            "point_count": "Confirm the point count before generating the FFT or DFT kernel.",
            "scaling_strategy": "Confirm the scaling strategy such as per-stage scale, block floating point, or none.",
            "twiddle_representation": "Confirm the twiddle representation such as lookup table, fixed-point format, or generated constants.",
            "complex_data_mode": "Confirm the complex input/output representation such as packed IQ, split real-imag arrays, or real-input transform mode.",
            "error_tolerance": "Confirm the acceptable FFT numerical error tolerance or comparison threshold.",
        },
        "prompt_rules": [
            "For FFT and DFT patterns, keep point count, scaling strategy, twiddle representation, complex data mode, and error tolerance explicit in the generated comments and testbench.",
            "Do not mix block-floating, per-stage scaling, or fixed-point twiddle policies; choose one scaling strategy and keep the error tolerance reviewable.",
            "Explain how twiddle storage, stage scheduling, and complex-data packing follow from the confirmed FFT metadata instead of inventing a transform structure ad hoc.",
        ],
    },
    "cordic": {
        "label": "CORDIC iterative math family",
        "metadata_fields": {
            "cordic_mode": "Confirm the CORDIC mode such as rotation, vectoring, or sincos generation.",
            "iteration_count": "Confirm the CORDIC iteration count before generation.",
            "angle_range": "Confirm the supported input angle range or quadrant normalization contract.",
            "fixed_point_format": "Confirm the fixed-point width and integer-bit format used by the CORDIC datapath.",
            "error_tolerance": "Confirm the acceptable CORDIC numerical error tolerance.",
        },
        "prompt_rules": [
            "For CORDIC patterns, make mode, iteration count, angle range, fixed-point format, and error tolerance explicit before generating lookup constants or shift-add loops.",
            "Do not claim a CORDIC design is valid across quadrants or angle ranges unless the confirmed angle-range contract explains the normalization or sign-correction strategy.",
            "Explain how the confirmed iteration count and fixed-point format trade off resource usage, latency, and numerical error tolerance.",
        ],
    },
    "matmul": {
        "label": "matrix multiply family",
        "metadata_fields": {
            "tile_shape": "Confirm the matrix tile shape or blocking geometry before generation.",
            "layout": "Confirm the matrix layout or packing convention used by the compute kernel.",
            "accumulator_type": "Confirm the accumulation type and growth policy for the matrix compute path.",
            "memory_schedule": "Confirm the memory schedule such as naive, blocked, or load-compute-store dataflow.",
            "ii_target": "Confirm the II target for the matrix inner loop or tile loop.",
        },
        "prompt_rules": [
            "For matrix multiply patterns, keep tile shape, layout, accumulator type, memory schedule, and II target explicit before choosing partition or dataflow pragmas.",
            "Do not mix blocked local-buffer, partitioned tile, and load-compute-store dataflow strategies without a confirmed memory schedule and tile geometry.",
            "Explain how the confirmed tile shape, layout, and accumulator type constrain local buffering, loop order, and matrix interface depth values.",
        ],
    },
    "prefix_scan": {
        "label": "prefix scan family",
        "metadata_fields": {
            "block_size": "Confirm the block size used by the prefix-scan schedule.",
            "scan_mode": "Confirm whether the prefix scan is inclusive, exclusive, segmented, or another scan mode.",
            "offset_propagation": "Confirm how block offsets or carry values propagate between scan segments.",
            "latency_strategy": "Confirm the latency strategy such as sequential chain or blocked parallel scan.",
            "boundary_policy": "Confirm the boundary policy for empty, short, or partial scan ranges.",
        },
        "prompt_rules": [
            "For prefix-scan patterns, keep scan mode, block size, offset propagation, latency strategy, and boundary policy explicit before restructuring the accumulation chain.",
            "Do not claim a blocked scan is correct unless the offset propagation contract between blocks is explicit and covered by the testbench.",
            "Explain how the confirmed block size and latency strategy shorten or preserve loop-carried dependencies without hiding boundary behavior.",
        ],
    },
    "rle_axis": {
        "label": "AXI-Stream run-length codec family",
        "metadata_fields": {
            "frame_boundary_mode": "Confirm the frame boundary mode for AXI-Stream packets before generation.",
            "tlast_policy": "Confirm the TLAST policy for the final encoded or decoded symbol.",
            "run_length_limit": "Confirm the maximum run-length value before splitting or rejecting a run.",
            "empty_frame_policy": "Confirm the empty-frame policy instead of guessing how AXI-Stream represents it.",
            "ii_target": "Confirm the II target for the stream processing loop.",
        },
        "prompt_rules": [
            "For RLE AXI-Stream patterns, keep frame boundary mode, TLAST policy, run-length limit, empty-frame policy, and II target explicit before generating stream control logic.",
            "Do not hide TLAST, TKEEP, or TSTRB handling behind generic AXIS language; explain how the final symbol or pair preserves the frame boundary contract.",
            "Explain how the confirmed run-length limit and empty-frame policy shape split-run behavior, pending output rules, and stream backpressure assumptions.",
        ],
    },
    "minimal_vitis_pipeline": {
        "label": "minimal Vitis kernel compile/link structure",
        "metadata_fields": {
            "compile_link_boundary": "Confirm the compile/link boundary that the minimal Vitis kernel flow must preserve.",
            "top_kernel_role": "Confirm the role of the top kernel source in the minimal Vitis project layout.",
            "bundle_naming_rule": "Confirm the stable bundle naming rule that downstream Vitis integration should preserve.",
        },
        "prompt_rules": [
            "For minimal Vitis kernel patterns, keep the compile/link split clear: do not mix package or host orchestration into the generated HLS source.",
            "Use stable bundle names and explicit depth values so downstream Vitis compile/link flows can plan around the generated HLS interface cleanly.",
            "Treat the top kernel source as the primary home of the interface contract and compute logic, while keeping wider project orchestration out of scope.",
        ],
    },
    "host_kernel_split": {
        "label": "kernel source and helper-header structure",
        "metadata_fields": {
            "kernel_source_boundary": "Confirm the boundary between the main kernel source and the supporting helper headers.",
            "helper_header_role": "Confirm what responsibilities belong in helper headers instead of the top kernel source.",
            "hotspot_file_strategy": "Confirm how pragma-dense hotspot logic should stay concentrated instead of spreading across every file.",
        },
        "prompt_rules": [
            "For host-kernel split patterns, keep helper header responsibilities distinct from the main kernel source so the top HLS file still owns the visible interface contract.",
            "Concentrate dense pragma usage in a small number of hotspot helper/source files rather than scattering complex directives uniformly across all files.",
            "Do not mix package or host orchestration into the generated HLS source even when the wider project uses separate host or package stages.",
        ],
    },
    "array_partition": {
        "label": "local-buffer array partition",
        "metadata_fields": {
            "target_buffer": "Confirm the target buffer that needs ARRAY_PARTITION before generation.",
            "partition_dim": "Confirm the partition dim that matches the contended access dimension.",
            "partition_type": "Confirm the partition type (complete, cyclic, or block) before generation.",
            "partition_factor": "Confirm the partition factor required to relieve the parallel access bottleneck.",
            "contention_reason": "Confirm the memory contention reason that justifies ARRAY_PARTITION.",
        },
        "prompt_rules": [
            "For ARRAY_PARTITION patterns, treat the directive as a response to a specific parallel access bottleneck, not as a generic speed hint.",
            "When an outer loop is pipelined, account for the implied inner-loop concurrency and bind the partition dimension, type, and factor to the accessed dimension identified by schedule-viewer or equivalent report evidence.",
            "Explain why ARRAY_PARTITION is better than ARRAY_RESHAPE for the confirmed target buffer and do not combine them on the same variable.",
        ],
    },
    "array_reshape": {
        "label": "local-buffer array reshape",
        "metadata_fields": {
            "target_buffer": "Confirm the target buffer that needs ARRAY_RESHAPE before generation.",
            "reshape_dim": "Confirm the reshape dim that matches the adjacent access dimension.",
            "reshape_type": "Confirm the reshape type used for the widened local view.",
            "adjacent_access_reason": "Confirm why adjacent elements are consumed together on the reshaped buffer.",
            "bandwidth_bottleneck": "Confirm the bandwidth bottleneck, preferably from schedule-viewer or equivalent report evidence.",
        },
        "prompt_rules": [
            "For ARRAY_RESHAPE patterns, widen the storage or local word only when adjacent elements are consumed together and the access pattern justifies it.",
            "When an outer loop is pipelined, account for the implied inner-loop concurrency and use schedule-viewer or equivalent report evidence to tie ARRAY_RESHAPE to the real load/store bottleneck.",
            "Explain why ARRAY_RESHAPE is preferred over ARRAY_PARTITION for the confirmed target buffer and keep the reshape dimension aligned with the adjacent-access story.",
        ],
    },
    "axi4_burst": {
        "label": "AXI4 burst/coalesced memory",
        "metadata_fields": {
            "burst_max_len": "Confirm the AXI4 maximum burst length for the coalesced memory path.",
            "coalesced_access": "Confirm the contiguous/coalesced AXI4 access pattern that makes burst transfers valid.",
        },
        "prompt_rules": [
            "When the pattern is AXI4 burst/coalescing, keep memory access contiguous, preserve an explicit burst length, and explain why the chosen loop order enables burst transfers.",
            "Do not claim burst throughput unless the access pattern is sequential enough for the configured max burst length and the cfg/interface settings match it.",
        ],
    },
    "dataflow": {
        "label": "read-compute-write dataflow",
        "metadata_fields": {
            "stage_boundaries": "Confirm the explicit read/compute/write stage boundaries before generating a DATAFLOW design.",
            "channel_kind": "Confirm the channel kind inferred or required between DATAFLOW stages.",
            "channel_depth": "Confirm the FIFO or channel depth needed between DATAFLOW stages.",
            "cosim_required": "Confirm that co-simulation or dataflow viewer validation is required for this DATAFLOW design.",
        },
        "prompt_rules": [
            "For DATAFLOW patterns, default to an explicit read/compute/write stage split with named helper regions or functions and clear channel ownership.",
            "Treat DATAFLOW as a task-architecture decision that must preserve stage boundaries, FIFO depth assumptions, and backpressure behavior.",
            "Do not claim the DATAFLOW design is complete from syntax alone; require co-simulation/dataflow-viewer style evidence for FIFO sizing, stalls, and throughput confirmation.",
        ],
    },
    "fixed_point": {
        "label": "fixed-point numeric strategy",
        "metadata_fields": {
            "numeric_range": "Confirm the fixed-point numeric range that the kernel must preserve.",
            "integer_bits": "Confirm the number of integer bits required by the fixed-point design.",
            "quantization_mode": "Confirm the fixed-point quantization mode before generation.",
            "overflow_mode": "Confirm the fixed-point overflow mode before generation.",
            "error_budget": "Confirm the acceptable fixed-point error budget or tolerance.",
        },
        "prompt_rules": [
            "For fixed-point patterns, make numeric range, integer bits, quantization mode, overflow mode, and error budget explicit in the generated comments and helper choices.",
            "Do not silently translate a floating-point tutorial idea into fixed-point code without preserving the confirmed numeric contract and oracle expectations.",
            "Use fixed-point widths as a hardware decision that remains reviewable across device migration, report review, and regression vectors.",
        ],
    },
    "line_buffer_stencil": {
        "label": "line-buffer / stencil window",
        "metadata_fields": {
            "window_shape": "Confirm the stencil window shape or tap count before generating a line-buffer design.",
            "border_policy": "Confirm the boundary handling policy for the line-buffer stencil.",
            "line_buffer_name": "Confirm the intended local line-buffer identifier used by the stencil structure.",
        },
        "prompt_rules": [
            "For line-buffer/stencil patterns, make the window shape, border policy, and local line-buffer ownership explicit before optimization.",
            "Use local buffer comments and pragmas to explain how the stencil reuses neighboring samples without mixing ARRAY_PARTITION and ARRAY_RESHAPE on the same buffer.",
        ],
    },
    "multi_m_axi": {
        "label": "multi-m_axi bandwidth split",
        "metadata_fields": {
            "bundle_map": "Confirm the bundle map for each independent m_axi argument before generation.",
            "traffic_independence": "Confirm which traffic groups must stay on independent memory channels.",
            "read_write_concurrency": "Confirm the intended read/write concurrency that justifies multiple m_axi bundles.",
        },
        "prompt_rules": [
            "For multi-m_axi patterns, make the bundle map explicit and keep each independent traffic group aligned with its own memory channel.",
            "Do not stop at bundle names alone; explain how the chosen bundle split preserves the confirmed read/read/write concurrency and arbitration intent.",
            "When multiple masters share no data dependence, preserve distinct bundles and concrete depths for each channel so co-simulation matches the intended bandwidth split.",
        ],
    },
    "reduction_tree": {
        "label": "reduction / tree accumulation",
        "metadata_fields": {
            "reduction_op": "Confirm the reduction operator before generating a tree-accumulation design.",
            "accumulator_type": "Confirm the accumulator type and overflow strategy for the reduction tree.",
            "tree_shape": "Confirm the reduction-tree shape or fan-in policy.",
        },
        "prompt_rules": [
            "For reduction-tree patterns, keep the accumulator type, reduction operator, and tree shape explicit so latency/resource tradeoffs stay reviewable.",
            "Do not unroll or reassociate reductions without a confirmed accumulator strategy and matching vectors for overflow or rounding edges.",
        ],
    },
    "tiled_gemm": {
        "label": "tiled GEMM / systolic-like local buffering",
        "metadata_fields": {
            "tile_shape": "Confirm the tile shape used by the GEMM local buffers.",
            "accumulator_type": "Confirm the accumulation type used by the GEMM tile compute path.",
            "layout": "Confirm the matrix layout or packing convention for the tiled GEMM interfaces.",
        },
        "prompt_rules": [
            "For tiled GEMM patterns, explain the tile shape, local buffer roles, and accumulation policy before adding unroll or partition directives.",
            "Preserve the intended matrix layout and show why the chosen local buffering matches the tile reuse strategy.",
        ],
    },
    "vector_lane": {
        "label": "hls_vector lane packing",
        "metadata_fields": {
            "lane_width": "Confirm the hls_vector lane width before generating packed-lane logic.",
            "pack_intent": "Confirm how adjacent samples are packed into hls_vector lanes.",
        },
        "prompt_rules": [
            "When the pattern is hls_vector lane packing, make the lane width and packing intent explicit and keep scalar boundary handling visible.",
            "Do not add hls_vector.h unless the lane mapping and packed datapath intent are confirmed.",
        ],
    },
    "task_graph": {
        "label": "hls_task task graph",
        "metadata_fields": {
            "restart_semantics": "Confirm the restart semantics for the hls_task graph.",
            "channel_depth": "Confirm the channel depth needed between hls_task stages.",
            "channel_ownership": "Confirm channel ownership for each task-graph producer/consumer boundary.",
        },
        "prompt_rules": [
            "For hls_task task graphs, distinguish control-driven orchestration from data-driven stages and keep restart semantics explicit.",
            "Only introduce hls_task.h when channel ownership, depth, and task boundaries are confirmed.",
        ],
    },
    "streamofblocks": {
        "label": "hls_streamofblocks block streaming",
        "metadata_fields": {
            "block_size": "Confirm the stream-of-blocks block size before generation.",
            "block_ownership": "Confirm producer/consumer ownership of each block-stream stage.",
        },
        "prompt_rules": [
            "For hls_streamofblocks patterns, explain the block size and block ownership so buffering decisions stay reviewable.",
            "Do not treat block streaming as ordinary scalar FIFO traffic; preserve the block-level contract explicitly.",
        ],
    },
    "directio_freerun": {
        "label": "hls_directio free-running control",
        "metadata_fields": {
            "free_running": "Confirm whether the kernel must be free-running before using hls_directio or ap_ctrl_none style control.",
            "control_protocol": "Confirm the control protocol used by the free-running direct I/O surface.",
        },
        "prompt_rules": [
            "For hls_directio/free-running patterns, keep the control protocol and always-on execution semantics explicit.",
            "When the target Vitis HLS toolchain does not provide hls_directio.h, keep the same free-running contract with ap_ctrl_none and standard stream headers instead of inventing unavailable library dependencies.",
            "Do not claim a free-running kernel unless the control interface and reset behavior are both confirmed.",
        ],
    },
    "fence_ordering": {
        "label": "hls_fence ordering",
        "metadata_fields": {
            "ordering_reason": "Confirm the memory-ordering reason before generating hls_fence usage.",
            "ordering_scope": "Confirm which memory or stream interactions require fence ordering.",
        },
        "prompt_rules": [
            "For hls_fence ordering patterns, explain the ordering reason and scope in comments; fence usage must be justified by a specific hazard.",
            "Do not include hls_fence.h unless the ordering contract is explicit and narrower synchronization would be insufficient.",
        ],
    },
}

ADVANCED_PATTERN_HEADERS = {
    "vector_lane": ["hls_vector.h"],
    "task_graph": ["hls_task.h"],
    "streamofblocks": ["hls_streamofblocks.h"],
    "fence_ordering": ["hls_fence.h"],
}

ADVANCED_LIBRARY_HEADERS = {
    "hls_task.h",
    "hls_vector.h",
    "hls_streamofblocks.h",
    "hls_directio.h",
    "hls_fence.h",
}


def canonical_pattern_name(spec_or_profile: dict[str, Any] | None) -> str:
    if not isinstance(spec_or_profile, dict):
        return ""
    profile = spec_or_profile.get("hls_profile") if isinstance(spec_or_profile.get("hls_profile"), dict) else spec_or_profile
    workflow = spec_or_profile.get("workflow") if isinstance(spec_or_profile.get("workflow"), dict) else {}
    pattern = profile.get("example_pattern") or workflow.get("example_pattern") or ""
    return str(pattern).strip().lower().replace("-", "_")


def pattern_definition(spec_or_profile: dict[str, Any] | None) -> dict[str, Any]:
    return PATTERN_RULES.get(canonical_pattern_name(spec_or_profile), {})


def pattern_prompt_rules(spec_or_profile: dict[str, Any] | None) -> list[str]:
    definition = pattern_definition(spec_or_profile)
    return [str(item) for item in definition.get("prompt_rules", [])]


def pattern_open_questions(spec: dict[str, Any]) -> list[str]:
    profile = spec.get("hls_profile") if isinstance(spec.get("hls_profile"), dict) else {}
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    definition = pattern_definition(spec)
    metadata_fields = definition.get("metadata_fields", {})
    questions: list[str] = []
    for key, question in metadata_fields.items():
        if metadata.get(key) in (None, "", [], {}):
            questions.append(str(question))
    for key in profile.get("required_metadata_fields", []) or []:
        field = str(key)
        if metadata.get(field) in (None, "", [], {}):
            human = field.replace("_", " ")
            question = f"Confirm the {human} metadata before generation."
            if question not in questions:
                questions.append(question)
    return questions


def required_pattern_headers(spec_or_profile: dict[str, Any] | None) -> list[str]:
    profile = spec_or_profile.get("hls_profile") if isinstance(spec_or_profile, dict) and isinstance(spec_or_profile.get("hls_profile"), dict) else spec_or_profile or {}
    required = list(ADVANCED_PATTERN_HEADERS.get(canonical_pattern_name(profile), []))
    if isinstance(profile, dict):
        for item in profile.get("required_headers", []) or []:
            header = str(item).strip()
            if header and header not in required:
                required.append(header)
    return required
