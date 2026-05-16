from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.hls_generator.hls_profile import validate_hls_profile
from runtime.hls_generator.model_provider import _hls_pragmas, _mock_hls_header_text, _mock_hls_source_text, _mock_hls_testbench_text
from runtime.hls_generator.prompt import render_prompt
from runtime.hls_generator.requirements import build_codegen_plan


def _spec_with_pattern(pattern: str, metadata: dict[str, object] | None = None) -> dict[str, object]:
    base = json.loads((SKILL_ROOT / "assets" / "examples" / "hls_vector_scale_spec.json").read_text(encoding="utf-8"))
    base["name"] = f"{pattern}_kernel"
    base["interfaces"]["top_function"] = f"{pattern}_kernel"
    base["design_requirements"]["confirmed_by_user"] = True
    base["design_requirements"]["confirmation_notes"] = f"Confirmed pattern {pattern}."
    base["hls_profile"] = {
        "example_pattern": pattern,
        "required_metadata_fields": [],
        "metadata": metadata or {},
    }
    return base


class HLSPatternContractTests(unittest.TestCase):
    def test_codegen_plan_adds_pattern_specific_open_questions(self) -> None:
        spec = _spec_with_pattern("task_graph")
        spec["hls_profile"] = {
            "example_pattern": "task_graph",
            "required_metadata_fields": [
                "restart_semantics",
                "channel_depth",
                "channel_ownership",
            ],
            "metadata": {},
        }

        plan = build_codegen_plan(spec)
        open_questions = "\n".join(plan["open_questions"])

        self.assertIn("restart semantics", open_questions.lower())
        self.assertIn("channel depth", open_questions.lower())
        self.assertIn("channel ownership", open_questions.lower())

    def test_validate_hls_profile_enforces_extended_profile_fields(self) -> None:
        profile = {
            "example_pattern": "line_buffer_stencil",
            "allowed_libraries": ["hls_task.h", "ap_int.h"],
            "required_headers": ["hls_task.h"],
            "required_pragmas": ["#pragma HLS DATAFLOW"],
            "required_metadata_fields": ["restart_semantics"],
            "metadata": {},
            "forbidden_combinations": [
                {
                    "all_of": [
                        "#pragma HLS ARRAY_PARTITION variable=line_buf complete dim=1",
                        "#pragma HLS ARRAY_RESHAPE variable=line_buf complete dim=1",
                    ],
                    "message": "Do not partition and reshape the same stencil line buffer.",
                }
            ],
            "required_cfg_entries": ["clock=8", "syn.file=src/stencil_kernel.cpp"],
        }
        spec = _spec_with_pattern("line_buffer_stencil")
        spec["hls_profile"] = profile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "stencil_kernel.cpp").write_text(
                '#include <hls_task.h>\n'
                '#include <ap_int.h>\n'
                'void stencil_kernel(const ap_uint<32>* input, ap_uint<32>* output, int length) {\n'
                '  #pragma HLS ARRAY_PARTITION variable=line_buf complete dim=1\n'
                '  #pragma HLS ARRAY_RESHAPE variable=line_buf complete dim=1\n'
                '}\n',
                encoding="utf-8",
            )
            (root / "hls_config.cfg").write_text("syn.file=src/stencil_kernel.cpp\n", encoding="utf-8")
            issues = validate_hls_profile(profile, root, spec)

        messages = "\n".join(item["message"] for item in issues)
        self.assertIn("missing metadata", messages.lower())
        self.assertIn("dataflow", messages.lower())
        self.assertIn("line buffer", messages.lower())
        self.assertIn("clock=8", messages.lower())

    def test_render_prompt_injects_pattern_specific_rules(self) -> None:
        vector_spec = _spec_with_pattern(
            "vector_lane",
            metadata={"lane_width": 4, "pack_intent": "pack adjacent samples into a lane vector"},
        )
        vector_spec["hls_profile"].update(
            {
                "required_metadata_fields": ["lane_width", "pack_intent"],
                "required_headers": ["hls_vector.h"],
            }
        )
        vector_prompt = render_prompt(vector_spec, comment_language="en")

        directio_spec = _spec_with_pattern(
            "directio_freerun",
            metadata={"free_running": True, "control_protocol": "ap_ctrl_none"},
        )
        directio_spec["hls_profile"].update(
            {
                "required_metadata_fields": ["free_running", "control_protocol"],
                "required_headers": [],
            }
        )
        directio_prompt = render_prompt(directio_spec, comment_language="en")

        self.assertIn("lane width", vector_prompt.lower())
        self.assertIn("hls_vector.h", vector_prompt)
        self.assertIn("free-running", directio_prompt.lower())
        self.assertIn("ap_ctrl_none", directio_prompt)

    def test_task_graph_mock_header_puts_hls_task_before_hls_stream(self) -> None:
        spec = _spec_with_pattern(
            "task_graph",
            metadata={
                "restart_semantics": "per_transaction_restart",
                "channel_depth": 16,
                "channel_ownership": "reader -> compute -> writer",
            },
        )
        spec["hls_profile"].update({"required_headers": ["hls_task.h"]})

        header_text = _mock_hls_header_text(spec, "en")

        self.assertLess(header_text.index("#include <hls_task.h>"), header_text.index("#include <hls_stream.h>"))

    def test_task_graph_top_level_pragmas_do_not_mix_pipeline_with_dataflow(self) -> None:
        spec = _spec_with_pattern(
            "task_graph",
            metadata={
                "restart_semantics": "per_transaction_restart",
                "channel_depth": 16,
                "channel_ownership": "reader -> compute -> writer",
            },
        )

        pragma_text = _hls_pragmas(spec)

        self.assertIn("#pragma HLS DATAFLOW", pragma_text)
        self.assertNotIn("#pragma HLS PIPELINE II=1", pragma_text)

    def test_task_graph_mock_source_uses_hls_task_actor(self) -> None:
        spec = json.loads((SKILL_ROOT / "assets" / "examples" / "hls_task_graph_axis_spec.json").read_text(encoding="utf-8"))

        source_text = _mock_hls_source_text(spec, "task_graph_kernel.h", "en")

        self.assertIn("hls::task compute_stage", source_text)
        self.assertIn("load_task_graph_memory_increment", source_text)
        self.assertIn("compute_stage", source_text)
        self.assertIn("store_task_graph_memory_increment", source_text)
        self.assertIn("#pragma HLS PIPELINE II=1 style=flp", source_text)
        self.assertNotIn("hls_thread_local hls::task", source_text)
        self.assertNotIn("compute_task_graph_memory_increment(task_stream, task_result_stream, task_count_stream);", source_text)

    def test_task_graph_profile_requires_hls_task_usage(self) -> None:
        spec = _spec_with_pattern(
            "task_graph",
            metadata={
                "restart_semantics": "per_transaction_restart",
                "channel_depth": 16,
                "channel_ownership": "reader -> compute -> writer",
            },
        )
        profile = {
            "example_pattern": "task_graph",
            "required_headers": ["hls_task.h"],
            "required_metadata_fields": ["restart_semantics", "channel_depth", "channel_ownership"],
            "metadata": spec["hls_profile"]["metadata"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "task_graph_kernel.cpp").write_text(
                '#include <hls_task.h>\n'
                '#include <hls_stream.h>\n'
                'void task_graph_kernel(hls::stream<int>& in_stream, hls::stream<int>& out_stream, int length) {\n'
                '  #pragma HLS DATAFLOW\n'
                '  (void)length;\n'
                '  out_stream.write(in_stream.read());\n'
                '}\n',
                encoding="utf-8",
            )
            (root / "hls_config.cfg").write_text("syn.file=src/task_graph_kernel.cpp\n", encoding="utf-8")
            issues = validate_hls_profile(profile, root, spec)

        messages = "\n".join(item["message"] for item in issues)
        self.assertIn("instantiate hls::task explicitly", messages)
        self.assertIn("flushing or free-running pipeline style", messages)

    def test_task_graph_testbench_uses_standard_memory_cases(self) -> None:
        spec = json.loads((SKILL_ROOT / "assets" / "examples" / "hls_task_graph_axis_spec.json").read_text(encoding="utf-8"))
        vectors = [
            {
                "id": "case_nominal",
                "inputs": {"input": [1, 2, 3], "length": 3},
                "expected_outputs": {"output": [2, 3, 4]},
            },
            {
                "id": "case_boundary",
                "inputs": {"input": [0, 15], "length": 2},
                "expected_outputs": {"output": [1, 16]},
            },
        ]

        tb_text = _mock_hls_testbench_text(spec, vectors, "hash", "en")

        self.assertIn("task_graph_memory_increment_kernel(input, output, 3);", tb_text)
        self.assertIn("task_graph_memory_increment_kernel(input, output, 2);", tb_text)
        self.assertNotIn("one combined kernel transaction", tb_text)


if __name__ == "__main__":
    unittest.main()
