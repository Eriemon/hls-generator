"""Shared HLS pattern metadata and rule helpers."""

from __future__ import annotations

from typing import Any


PATTERN_RULES: dict[str, dict[str, Any]] = {
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
