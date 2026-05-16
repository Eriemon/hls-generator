"""Pluggable model-provider adapters for HLS workflow execution."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .patterns import required_pattern_headers
from .reference_contract import REFERENCE_RESULT_TAG
from .vectors import VECTOR_HASH_TAG


class ModelProviderError(ValueError):
    """Raised when a model provider cannot return a valid response."""


class ManualResponseRequired(ModelProviderError):
    """Raised when the manual provider has no prepared response file."""


@dataclass(frozen=True)
class GenerationContext:
    attempt_id: str
    stage: str
    prompt_path: Path
    response_path: Path
    run_dir: Path
    attempt_dir: Path
    spec: dict[str, Any]
    manifest: dict[str, Any]
    workflow_config: dict[str, Any]
    vector_contract: dict[str, Any] | None = None
    comment_language: str = "zh"


class ModelProvider(Protocol):
    name: str

    def generate(self, prompt: str, context: GenerationContext) -> str:
        """Return a raw fenced-block model response."""


def build_model_provider(
    provider_name: str,
    *,
    command: str | Sequence[str] | None = None,
    timeout_s: int = 120,
    config: dict[str, Any] | None = None,
) -> ModelProvider:
    normalized = provider_name.lower()
    if normalized == "mock":
        return MockModelProvider(config=config)
    if normalized == "manual":
        return ManualModelProvider()
    if normalized == "command":
        if not command:
            raise ModelProviderError("Command provider requires a model command.")
        return CommandModelProvider(command, timeout_s=timeout_s)
    raise ModelProviderError(f"Unknown model provider {provider_name!r}.")


class ManualModelProvider:
    name = "manual"

    def generate(self, prompt: str, context: GenerationContext) -> str:
        del prompt
        if not context.response_path.exists():
            raise ManualResponseRequired(f"Manual provider expects a prepared response file at {context.response_path}.")
        return context.response_path.read_text(encoding="utf-8")


class CommandModelProvider:
    name = "command"

    def __init__(self, command: str | Sequence[str], *, timeout_s: int = 120) -> None:
        self._command = _normalize_command(command)
        self._timeout_s = timeout_s

    def generate(self, prompt: str, context: GenerationContext) -> str:
        env = os.environ.copy()
        env.update(
            {
                "HLS_GEN_PROMPT_PATH": str(context.prompt_path),
                "HLS_GEN_RESPONSE_PATH": str(context.response_path),
                "HLS_GEN_STAGE": context.stage,
                "HLS_GEN_ATTEMPT_ID": context.attempt_id,
                "HLS_GEN_CONTEXT_JSON": json.dumps(
                    {
                        "attempt_id": context.attempt_id,
                        "stage": context.stage,
                        "prompt_path": str(context.prompt_path),
                        "response_path": str(context.response_path),
                        "run_dir": str(context.run_dir),
                        "attempt_dir": str(context.attempt_dir),
                        "target": "hls",
                        "name": context.spec.get("name"),
                        "manifest": context.manifest,
                    },
                    ensure_ascii=False,
                ),
            }
        )
        command = [_expand_part(part, context) for part in self._command]
        try:
            result = subprocess.run(
                command,
                cwd=context.run_dir,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ModelProviderError(f"Command provider timed out after {self._timeout_s}s.") from exc
        except OSError as exc:
            raise ModelProviderError(f"Command provider failed to start: {exc}") from exc

        if result.returncode != 0:
            output = (result.stderr or result.stdout).strip()
            detail = output.splitlines()[0] if output else f"exit code {result.returncode}"
            raise ModelProviderError(f"Command provider failed: {detail}")
        if result.stdout.strip():
            return result.stdout
        if context.response_path.exists():
            return context.response_path.read_text(encoding="utf-8")
        raise ModelProviderError("Command provider produced no stdout and did not write the expected response file.")


class MockModelProvider:
    name = "mock"

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    def generate(self, prompt: str, context: GenerationContext) -> str:
        del prompt
        mode = _mock_mode(context, self._config)
        if mode == "invalid_response":
            return "This is not a fenced response.\n"
        manifest = context.manifest
        files = [entry for entry in manifest.get("files", []) if isinstance(entry, dict) and entry.get("path")]
        if mode == "spec_issue" and len(files) > 1:
            dropped_path = next((str(entry["path"]) for entry in files if entry.get("kind") == "testbench" or "_tb." in str(entry["path"]).lower()), str(files[-1]["path"]))
            files = [entry for entry in files if str(entry["path"]) != dropped_path]
        response_manifest = {
            **manifest,
            "files": files,
            "checks": {
                "spec_coverage": [f"Mock provider generated HLS stage {context.stage} artifacts."],
                "verification_plan": ["Mock response includes deterministic vectors and PASS/FAIL hooks."],
                "execution_plan": ["Mock response is intended for local workflow tests."],
                "implementation_assessment": ["Mock HLS artifacts satisfy structural workflow contracts."],
                "reviewability_assessment": ["Mock artifacts include minimal comments and result markers."],
                "assumptions": [],
                "known_limitations": ["Mock provider prioritizes workflow determinism over hardware fidelity."],
            },
        }
        blocks = ["```json", json.dumps(response_manifest, indent=2, ensure_ascii=False), "```"]
        file_map = _mock_file_contents(context, files)
        for file_entry in files:
            rel_path = str(file_entry["path"])
            language = str(file_entry.get("language") or "text")
            blocks.extend([f"```{language} path={rel_path}", file_map[rel_path].rstrip(), "```"])
        return "\n".join(blocks) + "\n"


def _normalize_command(command: str | Sequence[str]) -> list[str]:
    parts = shlex.split(command, posix=False) if isinstance(command, str) else [str(item) for item in command]
    if not parts:
        raise ModelProviderError("Model command must not be empty.")
    return parts


def _expand_part(part: str, context: GenerationContext) -> str:
    values = {
        "attempt_id": context.attempt_id,
        "stage": context.stage,
        "prompt_path": str(context.prompt_path),
        "response_path": str(context.response_path),
        "run_dir": str(context.run_dir),
        "attempt_dir": str(context.attempt_dir),
        "target": "hls",
        "name": str(context.spec.get("name") or ""),
    }
    try:
        return part.format_map(values)
    except Exception:
        return part


def _mock_mode(context: GenerationContext, config: dict[str, Any]) -> str:
    behavior = config.get("mock_behavior")
    if behavior is None:
        behavior = (context.spec.get("workflow") or {}).get("mock_behavior")
    if isinstance(behavior, str):
        return behavior
    if isinstance(behavior, dict):
        raw = behavior.get(context.stage, behavior.get("*", behavior.get("default", "success")))
        if isinstance(raw, dict):
            return str(raw.get("mode", "success"))
        if raw:
            return str(raw)
    return "success"


def _mock_file_contents(context: GenerationContext, files: list[dict[str, Any]]) -> dict[str, str]:
    stage = context.stage
    spec = context.spec
    vectors = _mock_vectors(spec)
    vector_hash = str((context.vector_contract or {}).get("sha256") or "")
    contents: dict[str, str] = {}
    if stage == "tests":
        payload = {"version": 1, "case_ids": [str(item["id"]) for item in vectors], "cases": vectors}
        for file_entry in files:
            contents[str(file_entry["path"])] = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        return contents
    if stage == "python":
        for file_entry in files:
            rel_path = str(file_entry["path"])
            if rel_path.endswith("_model.py"):
                contents[rel_path] = _mock_python_model_text(vectors)
            elif rel_path.endswith("_vectors.json"):
                contents[rel_path] = json.dumps({"cases": vectors}, indent=2, ensure_ascii=False) + "\n"
            else:
                contents[rel_path] = "{}\n"
        return contents
    if stage == "hls":
        header_name = next((Path(str(item["path"])).name for item in files if str(item["path"]).endswith((".h", ".hpp"))), "kernel.h")
        for file_entry in files:
            rel_path = str(file_entry["path"])
            suffix = Path(rel_path).suffix.lower()
            if suffix in {".h", ".hpp"}:
                contents[rel_path] = _mock_hls_header_text(spec, context.comment_language)
            elif suffix in {".cpp", ".cc", ".cxx"} and "_tb" not in Path(rel_path).stem:
                contents[rel_path] = _mock_hls_source_text(spec, header_name, context.comment_language)
            elif suffix in {".cpp", ".cc", ".cxx"}:
                contents[rel_path] = _mock_hls_testbench_text(spec, vectors, vector_hash, context.comment_language)
            elif suffix == ".cfg":
                contents[rel_path] = _mock_hls_cfg_text(spec, files)
            else:
                contents[rel_path] = "\n"
        return contents
    for file_entry in files:
        contents[str(file_entry["path"])] = "{}\n"
    return contents


def _mock_vectors(spec: dict[str, Any]) -> list[dict[str, Any]]:
    configured = (spec.get("workflow") or {}).get("mock_vectors")
    if isinstance(configured, list) and configured:
        return configured
    pattern = _example_pattern(spec)
    arguments = {str(item.get("name")): item for item in spec.get("interfaces", {}).get("arguments", []) if isinstance(item, dict) and item.get("name")}
    if pattern == "line_buffer_stencil":
        return [
            {
                "id": "case_nominal",
                "inputs": {"input": [1, 2, 3, 4], "length": 4},
                "expected_outputs": {"output": [4, 6, 9, 11]},
                "checkpoints": {"length": 4, "first_output": 4},
            },
            {
                "id": "case_boundary",
                "inputs": {"input": [5, 1], "length": 2},
                "expected_outputs": {"output": [11, 7]},
                "checkpoints": {"length": 2, "first_output": 11},
            },
        ]
    if pattern == "reduction_tree":
        return [
            {
                "id": "case_nominal",
                "inputs": {"input": [1, 2, 3, 4], "length": 4},
                "expected_outputs": {"output": [10]},
                "checkpoints": {"length": 4, "first_output": 10},
            },
            {
                "id": "case_boundary",
                "inputs": {"input": [9], "length": 1},
                "expected_outputs": {"output": [9]},
                "checkpoints": {"length": 1, "first_output": 9},
            },
        ]
    if pattern == "tiled_gemm":
        return [
            {
                "id": "case_nominal",
                "inputs": {"input_a": [1, 2, 3, 4], "input_b": [5, 6, 7, 8], "length": 4},
                "expected_outputs": {"output": [5, 12, 21, 32]},
                "checkpoints": {"length": 4, "first_output": 5},
            },
            {
                "id": "case_boundary",
                "inputs": {"input_a": [2, 0], "input_b": [4, 9], "length": 2},
                "expected_outputs": {"output": [8, 0]},
                "checkpoints": {"length": 2, "first_output": 8},
            },
        ]
    if pattern == "task_graph" and {"input", "output", "length"}.issubset(arguments):
        return [
            {
                "id": "case_nominal",
                "inputs": {"input": [1, 2, 3], "length": 3},
                "expected_outputs": {"output": [2, 3, 4]},
                "checkpoints": {"length": 3, "first_output": 2},
            },
            {
                "id": "case_boundary",
                "inputs": {"input": [0, 15], "length": 2},
                "expected_outputs": {"output": [1, 16]},
                "checkpoints": {"length": 2, "first_output": 1},
            },
        ]
    if pattern == "directio_freerun" and {"in_stream", "out_stream"}.issubset(arguments):
        return [
            {
                "id": "case_nominal",
                "inputs": {"in_stream": [1, 2, 3]},
                "expected_outputs": {"out_stream": [2, 3, 4]},
                "checkpoints": {"token_count": 3, "first_output": 2},
            },
            {
                "id": "case_boundary",
                "inputs": {"in_stream": [0, 15]},
                "expected_outputs": {"out_stream": [1, 16]},
                "checkpoints": {"token_count": 2, "first_output": 1},
            },
        ]
    if {"input", "output", "scale", "length"}.issubset(arguments):
        return [
            {
                "id": "case_nominal",
                "inputs": {"input": [1, 2, 3], "scale": 2, "length": 3},
                "expected_outputs": {"output": [2, 4, 6]},
                "checkpoints": {"length": 3, "first_output": 2},
            },
            {
                "id": "case_boundary",
                "inputs": {"input": [9, 8, 7], "scale": 0, "length": 3},
                "expected_outputs": {"output": [0, 0, 0]},
                "checkpoints": {"length": 3, "first_output": 0},
            },
        ]
    if {"input_a", "input_b", "output", "length"}.issubset(arguments):
        return [
            {
                "id": "case_nominal",
                "inputs": {"input_a": [1, 2, 3], "input_b": [4, 5, 6], "length": 3},
                "expected_outputs": {"output": [5, 7, 9]},
                "checkpoints": {"length": 3, "first_output": 5},
            },
            {
                "id": "case_boundary",
                "inputs": {"input_a": [9, 0], "input_b": [1, 7], "length": 2},
                "expected_outputs": {"output": [10, 7]},
                "checkpoints": {"length": 2, "first_output": 10},
            },
        ]
    if {"in_stream", "out_stream", "length"}.issubset(arguments):
        return [
            {
                "id": "case_nominal",
                "inputs": {"in_stream": [1, 2, 3], "length": 3},
                "expected_outputs": {"out_stream": [2, 3, 4]},
                "checkpoints": {"length": 3, "first_output": 2},
            },
            {
                "id": "case_boundary",
                "inputs": {"in_stream": [0, 15], "length": 2},
                "expected_outputs": {"out_stream": [1, 16]},
                "checkpoints": {"length": 2, "first_output": 1},
            },
        ]
    return [
        {
            "id": "case_passthrough",
            "inputs": {"value": 1},
            "expected_outputs": {"value": 1},
            "checkpoints": {"value": 1},
        }
    ]


def _mock_python_model_text(vectors: list[dict[str, Any]]) -> str:
    payload = repr(vectors)
    return f'''REFERENCE_VECTORS = {payload}


def run_case(case):
    inputs = case.get("inputs", {{}})
    if all(key in inputs for key in ("input", "scale", "length")):
        length = int(inputs["length"])
        scale = int(inputs["scale"])
        return {{"output": [int(value) * scale for value in list(inputs["input"])[:length]]}}
    if "input" in inputs and "length" in inputs:
        length = int(inputs["length"])
        return {{"output": [int(value) + 1 for value in list(inputs["input"])[:length]]}}
    if all(key in inputs for key in ("input_a", "input_b", "length")):
        length = int(inputs["length"])
        return {{"output": [int(a) + int(b) for a, b in zip(list(inputs["input_a"])[:length], list(inputs["input_b"])[:length])]}}
    if "in_stream" in inputs and "length" in inputs:
        length = int(inputs["length"])
        return {{"out_stream": [int(value) + 1 for value in list(inputs["in_stream"])[:length]]}}
    if "expected_outputs" in case:
        return case["expected_outputs"]
    return case.get("outputs", inputs)


def collect_checkpoints(case):
    return case.get("checkpoints", {{"observed": run_case(case)}})


def run_tests():
    for case in REFERENCE_VECTORS:
        expected = case.get("expected_outputs", run_case(case))
        if run_case(case) != expected:
            print(f"FAIL {{case.get('id', 'case')}}")
            return False
    print("PASS")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
'''


def _mock_hls_header_text(spec: dict[str, Any], comment_language: str) -> str:
    top = str(spec.get("interfaces", {}).get("top_function") or spec.get("name") or "kernel")
    headers = ["ap_fixed.h", "ap_int.h"]
    for header in required_pattern_headers(spec):
        if header not in headers:
            headers.append(header)
    if "hls_stream.h" not in headers:
        headers.append("hls_stream.h")
    include_block = "".join(f"#include <{header}>\n" for header in headers)
    return "#pragma once\n" + include_block + "\n" + f"// {_comment(comment_language, 'Vitis HLS top function declaration.', 'Vitis HLS 顶层函数声明。')}\nvoid {top}({_cpp_arguments(spec)});\n"


def _mock_hls_source_text(spec: dict[str, Any], header_name: str, comment_language: str) -> str:
    top = str(spec.get("interfaces", {}).get("top_function") or spec.get("name") or "kernel")
    helpers = _mock_hls_helpers_text(spec, comment_language)
    helper_block = helpers + "\n" if helpers else ""
    return f'''#include "{header_name}"

{helper_block}
void {top}({_cpp_arguments(spec)}) {{
  // {_comment(comment_language, 'Port protocols and pipeline constraints follow the confirmed HLS spec.', '端口协议和流水线约束由确认后的 HLS spec 驱动。')}
{_hls_pragmas(spec)}
  // {_comment(comment_language, 'Core computation stays synthesizable and aligned with the Python oracle.', '核心计算保持可综合并与 Python oracle 对齐。')}
{_mock_hls_body(spec)}
}}
'''


def _mock_hls_helpers_text(spec: dict[str, Any], comment_language: str) -> str:
    pattern = _example_pattern(spec)
    if pattern not in {"dataflow", "task_graph"}:
        return ""
    name = str(spec.get("name") or "kernel")
    arg_names = {
        str(item.get("name"))
        for item in spec.get("interfaces", {}).get("arguments", [])
        if isinstance(item, dict) and item.get("name")
    }
    stream_name = "task_stream" if pattern == "task_graph" else "mid_stream"
    result_name = "task_result_stream" if pattern == "task_graph" else "result_stream"
    if pattern == "task_graph":
        if {"input", "output", "length"}.issubset(arg_names):
            return f'''static void load_{name}(const ap_uint<32>* input, hls::stream<ap_uint<32> >& {stream_name}, hls::stream<int>& count_stream, int length) {{
  // {_comment(comment_language, 'Load stage captures the bounded transaction length before streaming memory data into the task actor.', '加载阶段先锁定有界事务长度，再把存储数据流送入 task actor。')}
  count_stream.write(length);
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    {stream_name}.write(input[i]);
  }}
}}

static void compute_{name}(hls::stream<ap_uint<32> >& {stream_name}, hls::stream<ap_uint<32> >& {result_name}, hls::stream<int>& count_stream) {{
  // {_comment(comment_language, 'Compute actor consumes the streamed transaction count so hls::task remains stream-only.', '计算 actor 通过流式事务计数保持 hls::task 仅流参数约束。')}
  int length = count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    ap_uint<32> value = {stream_name}.read();
    {result_name}.write(value + 1);
  }}
}}

static void store_{name}(hls::stream<ap_uint<32> >& {result_name}, ap_uint<32>* output, int length) {{
  // {_comment(comment_language, 'Store stage drains the task result stream back to memory under the same bounded transaction length.', '回写阶段在相同有界事务长度下把 task 结果流写回存储。')}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    output[i] = {result_name}.read();
  }}
}}'''
        return f'''static void seed_{name}_counts(int length, hls::stream<int>& read_count_stream) {{
  // {_comment(comment_language, 'Seed one bounded transaction count into the task graph so restart semantics stay explicit.', '将一次有界事务的计数写入 task graph，使重启语义保持显式。')}
  read_count_stream.write(length);
}}

static void read_{name}(hls::stream<ap_uint<32> >& in_stream, hls::stream<ap_uint<32> >& {stream_name}, hls::stream<int>& read_count_stream, hls::stream<int>& compute_count_stream) {{
  // {_comment(comment_language, 'Read actor consumes one seeded transaction count before streaming AXI tokens.', '读取 actor 先消费一次预置事务计数，再顺序吸收 AXI token。')}
  int length = read_count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    {stream_name}.write(in_stream.read());
  }}
  compute_count_stream.write(length);
}}

static void compute_{name}(hls::stream<ap_uint<32> >& {stream_name}, hls::stream<ap_uint<32> >& {result_name}, hls::stream<int>& compute_count_stream, hls::stream<int>& write_count_stream) {{
  // {_comment(comment_language, 'Compute actor uses a streamed transaction count so Vitis 2022.2 hls::task stays stream-only.', '计算 actor 通过流式事务计数保持 Vitis 2022.2 的 hls::task 仅流参数约束。')}
  int length = compute_count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    ap_uint<32> value = {stream_name}.read();
    {result_name}.write(value + 1);
  }}
  write_count_stream.write(length);
}}

static void write_{name}(hls::stream<ap_uint<32> >& {result_name}, hls::stream<ap_uint<32> >& out_stream, hls::stream<int>& write_count_stream) {{
  // {_comment(comment_language, 'Write actor consumes the streamed transaction count before draining result tokens.', '写出 actor 先消费流式事务计数，再按边界取走结果 token。')}
  int length = write_count_stream.read();
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1 style=flp
    out_stream.write({result_name}.read());
  }}
}}'''
    return f'''static void read_{name}(hls::stream<ap_uint<32> >& in_stream, hls::stream<ap_uint<32> >& {stream_name}, int length) {{
  // {_comment(comment_language, 'Read stage isolates external AXI-Stream input from compute latency.', '读取阶段将外部 AXI-Stream 输入与计算延迟解耦。')}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    {stream_name}.write(in_stream.read());
  }}
}}

static void compute_{name}(hls::stream<ap_uint<32> >& {stream_name}, hls::stream<ap_uint<32> >& {result_name}, int length) {{
  // {_comment(comment_language, 'Compute stage owns the token transform so DATAFLOW can overlap stages.', '计算阶段独立负责 token 变换，便于 DATAFLOW 重叠执行。')}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    ap_uint<32> value = {stream_name}.read();
    {result_name}.write(value + 1);
  }}
}}

static void write_{name}(hls::stream<ap_uint<32> >& {result_name}, hls::stream<ap_uint<32> >& out_stream, int length) {{
  // {_comment(comment_language, 'Write stage preserves one output token for each input token.', '写出阶段确保每个输入 token 对应一个输出 token。')}
  for (int i = 0; i < length; ++i) {{
    #pragma HLS PIPELINE II=1
    out_stream.write({result_name}.read());
  }}
}}'''


def _mock_hls_testbench_text(spec: dict[str, Any], vectors: list[dict[str, Any]], vector_hash: str, comment_language: str) -> str:
    top = str(spec.get("interfaces", {}).get("top_function") or spec.get("name") or "kernel")
    arg_names = {str(item.get("name")) for item in spec.get("interfaces", {}).get("arguments", []) if isinstance(item, dict)}
    hash_comment = f"  // {VECTOR_HASH_TAG} {vector_hash}\n" if vector_hash else ""
    case_comments = "\n".join(f'  // {item["id"]} PASS FAIL' for item in vectors)
    if {"input_a", "input_b", "output", "length"}.issubset(arg_names):
        body = _mock_multi_m_axi_cases(spec, top, vectors, comment_language)
    elif {"input", "output", "scale", "length"}.issubset(arg_names):
        body = _mock_vector_scale_cases(spec, top, vectors, comment_language)
    elif {"input", "output", "length"}.issubset(arg_names):
        body = _mock_input_output_cases(spec, top, vectors, comment_language)
    elif {"in_stream", "out_stream", "length"}.issubset(arg_names):
        body = _mock_task_graph_axis_cases(top, vectors, comment_language) if _example_pattern(spec) == "task_graph" else _mock_axis_cases(top, vectors, comment_language)
    elif {"in_stream", "out_stream"}.issubset(arg_names):
        body = _mock_directio_unit_cases(top, vectors, comment_language) if _example_pattern(spec) == "directio_freerun" else f"  {top}(in_stream, out_stream);\n"
    else:
        body = f"  {top}();\n"
    return f'''#include <iostream>
#include "../src/{top}.h"

int main() {{
{hash_comment}{case_comments}
  int failures = 0;
{body}
  if (failures != 0) {{
    std::cout << "FAIL\\n";
    return 1;
  }}
  std::cout << "PASS\\n";
  return 0;
}}
'''


def _mock_hls_cfg_text(spec: dict[str, Any], files: list[dict[str, Any]]) -> str:
    top = str(spec.get("interfaces", {}).get("top_function") or spec.get("name") or "kernel")
    lines = ["[HLS]", f"syn.top={top}"]
    for item in files:
        path = str(item["path"])
        suffix = Path(path).suffix.lower()
        if suffix in {".cpp", ".cc", ".cxx"} and "_tb" not in Path(path).stem:
            lines.append(f"syn.file={path}")
        if suffix in {".h", ".hpp"}:
            lines.append(f"syn.file={path}")
    for item in files:
        path = str(item["path"])
        if "_tb" in Path(path).stem and Path(path).suffix.lower() in {".cpp", ".cc", ".cxx"}:
            lines.append(f"tb.file={path}")
    clock = spec.get("clock", {})
    if isinstance(clock, dict) and clock.get("period_ns") not in (None, ""):
        lines.append(f"clock={clock['period_ns']}")
    part = (spec.get("workflow") or {}).get("part") or spec.get("part")
    if part:
        lines.append(f"part={part}")
    interface_profile = spec.get("interface_profile") if isinstance(spec.get("interface_profile"), dict) else {}
    if interface_profile.get("burst_support") is True and interface_profile.get("max_burst_len"):
        lines.extend(["", "[interface]", f"m_axi_max_read_burst_length={int(interface_profile['max_burst_len'])}"])
    return "\n".join(lines) + "\n"


def _cpp_arguments(spec: dict[str, Any]) -> str:
    args = []
    for item in spec.get("interfaces", {}).get("arguments", []):
        if isinstance(item, dict) and item.get("name"):
            args.append(f'{item.get("type", "int")} {item["name"]}')
    return ", ".join(args) or "void"


def _hls_pragmas(spec: dict[str, Any]) -> str:
    lines = []
    pattern = _example_pattern(spec)
    for item in spec.get("interfaces", {}).get("arguments", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        interface = str(item.get("interface") or "s_axilite")
        if interface == "m_axi":
            lines.append(f"#pragma HLS INTERFACE m_axi port={item['name']} bundle={item.get('bundle', 'gmem')} depth={_m_axi_depth(spec, item)}")
        elif interface in {"axis", "ap_fifo", "ap_none"}:
            lines.append(f"#pragma HLS INTERFACE {interface} port={item['name']}")
        else:
            lines.append(f"#pragma HLS INTERFACE s_axilite port={item['name']}")
    lines.append(f"#pragma HLS INTERFACE {spec.get('interfaces', {}).get('control', 's_axilite')} port=return")
    if pattern in {"dataflow", "task_graph", "streamofblocks"}:
        lines.append("#pragma HLS DATAFLOW")
    if spec.get("pipeline_required", True) and pattern not in {"dataflow", "task_graph", "streamofblocks"}:
        lines.append("#pragma HLS PIPELINE II=1")
    return "\n".join(f"  {line}" for line in lines)


def _m_axi_depth(spec: dict[str, Any], argument: dict[str, Any]) -> int:
    if isinstance(argument.get("depth"), int) and int(argument["depth"]) > 0:
        return int(argument["depth"])
    performance = spec.get("performance") if isinstance(spec.get("performance"), dict) else {}
    for key in ("max_length", "vector_length", "depth"):
        if isinstance(performance.get(key), int) and int(performance[key]) > 0:
            return int(performance[key])
    return 1024


def _mock_hls_body(spec: dict[str, Any]) -> str:
    arguments = {
        str(item.get("name")): item
        for item in spec.get("interfaces", {}).get("arguments", [])
        if isinstance(item, dict) and item.get("name")
    }
    arg_names = set(arguments)
    pattern = _example_pattern(spec)
    if {"input_a", "input_b", "output", "length"}.issubset(arg_names):
        if pattern == "tiled_gemm":
            value_type = _argument_storage_type(arguments["input_a"])
            return f"""  {value_type} tile_a[4];
  {value_type} tile_b[4];
  #pragma HLS ARRAY_PARTITION variable=tile_a complete dim=1
  #pragma HLS ARRAY_PARTITION variable=tile_b complete dim=1
  for (int base = 0; base < length; base += 4) {{
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      tile_a[j] = (j < chunk) ? input_a[base + j] : {value_type}(0);
      tile_b[j] = (j < chunk) ? input_b[base + j] : {value_type}(0);
    }}
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        output[base + j] = tile_a[j] * tile_b[j];
      }}
    }}
  }}"""
        if pattern == "vector_lane":
            value_type = _argument_storage_type(arguments["input_a"])
            return f"""  {value_type} lane_buf_a[4];
  {value_type} lane_buf_b[4];
  #pragma HLS ARRAY_PARTITION variable=lane_buf_a complete dim=1
  #pragma HLS ARRAY_PARTITION variable=lane_buf_b complete dim=1
  for (int base = 0; base < length; base += 4) {{
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL factor=4
      lane_buf_a[j] = (j < chunk) ? input_a[base + j] : {value_type}(0);
      lane_buf_b[j] = (j < chunk) ? input_b[base + j] : {value_type}(0);
    }}
    for (int j = 0; j < 4; ++j) {{
      #pragma HLS UNROLL factor=4
      if (j < chunk) {{
        output[base + j] = lane_buf_a[j] + lane_buf_b[j];
      }}
    }}
  }}"""
        if pattern == "fence_ordering":
            return """  for (int i = 0; i < length; ++i) {
    ap_uint<32> ordered_writeback = input_a[i] + input_b[i];
    output[i] = ordered_writeback;
  }"""
        return "  for (int i = 0; i < length; ++i) {\n    output[i] = input_a[i] + input_b[i];\n  }"
    if {"input", "output", "length"}.issubset(arg_names):
        if pattern == "task_graph":
            return f"""  hls::stream<ap_uint<32> > task_stream;
  hls::stream<ap_uint<32> > task_result_stream;
  hls::stream<int> task_count_stream;
  #pragma HLS STREAM variable=task_stream depth=16
  #pragma HLS STREAM variable=task_result_stream depth=16
  #pragma HLS STREAM variable=task_count_stream depth=2
  load_{str(spec.get("name") or "kernel")}(input, task_stream, task_count_stream, length);
  hls::task compute_stage(compute_{str(spec.get("name") or "kernel")}, task_stream, task_result_stream, task_count_stream);
  store_{str(spec.get("name") or "kernel")}(task_result_stream, output, length);"""
        if "scale" in arg_names:
            if _example_pattern(spec) == "array_partition":
                value_type = _argument_storage_type(arguments["input"])
                return f"""  {value_type} local_buf[16];
  // Local partition exposes parallel element access inside each tile.
  #pragma HLS ARRAY_PARTITION variable=local_buf complete dim=1
  for (int base = 0; base < length; base += 16) {{
    int chunk = (length - base < 16) ? (length - base) : 16;
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        local_buf[j] = input[base + j];
      }}
    }}
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        output[base + j] = local_buf[j] * scale;
      }}
    }}
  }}"""
            if _example_pattern(spec) == "array_reshape":
                value_type = _argument_storage_type(arguments["input"])
                return f"""  {value_type} wide_buf[16];
  // Local reshape widens adjacent element access without also partitioning the buffer.
  #pragma HLS ARRAY_RESHAPE variable=wide_buf complete dim=1
  for (int base = 0; base < length; base += 16) {{
    int chunk = (length - base < 16) ? (length - base) : 16;
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        wide_buf[j] = input[base + j];
      }}
    }}
    for (int j = 0; j < 16; ++j) {{
      #pragma HLS UNROLL
      if (j < chunk) {{
        output[base + j] = wide_buf[j] * scale;
      }}
    }}
  }}"""
            if pattern == "axi4_burst":
                return "  for (int i = 0; i < length; ++i) {\n    output[i] = input[i] * scale;\n  }"
            return "  for (int i = 0; i < length; ++i) {\n    output[i] = input[i] * scale;\n  }"
        if pattern == "line_buffer_stencil":
            value_type = _argument_storage_type(arguments["input"])
            return f"""  {value_type} line_buf[3];
  #pragma HLS ARRAY_PARTITION variable=line_buf complete dim=1
  for (int i = 0; i < length; ++i) {{
    line_buf[0] = input[(i == 0) ? 0 : (i - 1)];
    line_buf[1] = input[i];
    line_buf[2] = input[(i + 1 < length) ? (i + 1) : (length - 1)];
    output[i] = line_buf[0] + line_buf[1] + line_buf[2];
  }}"""
        if pattern == "reduction_tree":
            return """  ap_uint<32> tree_accum = 0;
  for (int i = 0; i < length; i += 4) {
    #pragma HLS UNROLL factor=4
    ap_uint<32> partial0 = (i + 0 < length) ? input[i + 0] : ap_uint<32>(0);
    ap_uint<32> partial1 = (i + 1 < length) ? input[i + 1] : ap_uint<32>(0);
    ap_uint<32> partial2 = (i + 2 < length) ? input[i + 2] : ap_uint<32>(0);
    ap_uint<32> partial3 = (i + 3 < length) ? input[i + 3] : ap_uint<32>(0);
    tree_accum += (partial0 + partial1) + (partial2 + partial3);
  }
  output[0] = tree_accum;"""
        return "  for (int i = 0; i < length; ++i) {\n    output[i] = input[i];\n  }"
    if {"in_stream", "out_stream"}.issubset(arg_names):
        if pattern in {"dataflow", "task_graph"} and "length" in arg_names:
            name = str(spec.get("name") or "kernel")
            stream_name = "task_stream" if pattern == "task_graph" else "mid_stream"
            result_name = "task_result_stream" if pattern == "task_graph" else "result_stream"
            if pattern == "task_graph":
                return f"""  hls::stream<ap_uint<32> > {stream_name};
  hls::stream<ap_uint<32> > {result_name};
  hls::stream<int> read_count_stream;
  hls::stream<int> compute_count_stream;
  hls::stream<int> write_count_stream;
  #pragma HLS STREAM variable={stream_name} depth=16
  #pragma HLS STREAM variable={result_name} depth=16
  #pragma HLS STREAM variable=read_count_stream depth=2
  #pragma HLS STREAM variable=compute_count_stream depth=2
  #pragma HLS STREAM variable=write_count_stream depth=2
  seed_{name}_counts(length, read_count_stream);
  hls::task read_stage(read_{name}, in_stream, {stream_name}, read_count_stream, compute_count_stream);
  hls::task compute_stage(compute_{name}, {stream_name}, {result_name}, compute_count_stream, write_count_stream);
  hls::task write_stage(write_{name}, {result_name}, out_stream, write_count_stream);"""
            return f"""  hls::stream<ap_uint<32> > {stream_name};
  hls::stream<ap_uint<32> > {result_name};
  #pragma HLS STREAM variable={stream_name} depth=16
  #pragma HLS STREAM variable={result_name} depth=16
  read_{name}(in_stream, {stream_name}, length);
  compute_{name}({stream_name}, {result_name}, length);
  write_{name}({result_name}, out_stream, length);"""
        if pattern == "streamofblocks" and "length" in arg_names:
            return """  ap_uint<32> block_buf[4];
  #pragma HLS ARRAY_PARTITION variable=block_buf complete dim=1
  for (int base = 0; base < length; base += 4) {
    #pragma HLS PIPELINE II=1
    int chunk = (length - base < 4) ? (length - base) : 4;
    for (int j = 0; j < 4; ++j) {
      #pragma HLS UNROLL factor=4
      block_buf[j] = (j < chunk) ? in_stream.read() : ap_uint<32>(0);
    }
    for (int j = 0; j < 4; ++j) {
      #pragma HLS UNROLL factor=4
      if (j < chunk) {
        out_stream.write(block_buf[j] + 1);
      }
    }
  }"""
        if "length" in arg_names:
            return "  for (int i = 0; i < length; ++i) {\n    ap_uint<32> value = in_stream.read();\n    out_stream.write(value + 1);\n  }"
        return "  if (!in_stream.empty()) {\n    ap_uint<32> value = in_stream.read();\n    out_stream.write(value + 1);\n  }"
    return "  // Mock fallback keeps the top function syntactically complete.\n  return;"


def _example_pattern(spec: dict[str, Any]) -> str:
    profile = spec.get("hls_profile") if isinstance(spec.get("hls_profile"), dict) else {}
    workflow = spec.get("workflow") if isinstance(spec.get("workflow"), dict) else {}
    pattern = profile.get("example_pattern") or workflow.get("example_pattern") or ""
    return str(pattern).strip().lower().replace("-", "_")


def _mock_vector_scale_cases(spec: dict[str, Any], top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    arguments = {
        str(item.get("name")): item
        for item in spec.get("interfaces", {}).get("arguments", [])
        if isinstance(item, dict) and item.get("name")
    }
    interface_depth = max(
        _m_axi_depth(spec, arguments.get("input", {})),
        _m_axi_depth(spec, arguments.get("output", {})),
    )
    input_type = _argument_storage_type(arguments.get("input", {}))
    output_type = _argument_storage_type(arguments.get("output", {}))
    scale_type = _argument_value_type(arguments.get("scale", {}))
    blocks: list[str] = []
    for case in vectors:
        inputs = case.get("inputs", {})
        values = [float(item) for item in inputs.get("input", [])]
        expected = [float(item) for item in case.get("expected_outputs", {}).get("output", [])]
        scale = float(inputs.get("scale", 1))
        length = int(inputs.get("length", len(values)))
        array_depth = max(1, interface_depth, len(values), length, len(expected))
        values_text = ", ".join(_literal_number(item) for item in values) or "0"
        expected_text = ", ".join(_literal_number(item) for item in expected) or "0"
        blocks.append(f'''  {{
    // {_comment(comment_language, f'Run vector case {case["id"]} and compare the observed output.', f'执行向量用例 {case["id"]} 并比较真实输出。')}
    {input_type} input[{array_depth}] = {{{values_text}}};
    {output_type} output[{array_depth}] = {{}};
    const double expected[{max(1, len(expected))}] = {{{expected_text}}};
    {top}(input, output, {_constructor_expr(scale_type, scale)}, {length});
    bool pass = true;
    for (int i = 0; i < {length}; ++i) {{
      if ((double)output[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout << "{REFERENCE_RESULT_TAG} {{\\"case_id\\":\\"{case["id"]}\\",\\"status\\":\\"" << (pass ? "PASS" : "FAIL") << "\\",\\"outputs\\":{{\\"output\\":[";
    for (int i = 0; i < {length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << (double)output[i];
    }}
    std::cout << "]}},\\"checkpoints\\":{{\\"length\\":{length},\\"first_output\\":" << (double)output[0] << "}}}}\\n";
    if (!pass) failures++;
  }}''')
    return "\n".join(blocks)


def _mock_input_output_cases(spec: dict[str, Any], top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    arguments = {
        str(item.get("name")): item
        for item in spec.get("interfaces", {}).get("arguments", [])
        if isinstance(item, dict) and item.get("name")
    }
    interface_depth = max(_m_axi_depth(spec, arguments.get("input", {})), _m_axi_depth(spec, arguments.get("output", {})))
    input_type = _argument_storage_type(arguments.get("input", {}))
    output_type = _argument_storage_type(arguments.get("output", {}))
    blocks: list[str] = []
    for case in vectors:
        inputs = case.get("inputs", {})
        values = [float(item) for item in inputs.get("input", [])]
        expected = [float(item) for item in case.get("expected_outputs", {}).get("output", [])]
        length = int(inputs.get("length", len(values)))
        array_depth = max(1, interface_depth, len(values), max(1, len(expected)), length)
        values_text = ", ".join(_literal_number(item) for item in values) or "0"
        expected_text = ", ".join(_literal_number(item) for item in expected) or "0"
        observed_bound = max(1, len(expected))
        blocks.append(f'''  {{
    // {_comment(comment_language, f'Run vector case {case["id"]} and compare the observed output.', f'执行向量用例 {case["id"]} 并比较真实输出。')}
    {input_type} input[{array_depth}] = {{{values_text}}};
    {output_type} output[{array_depth}] = {{}};
    const double expected[{observed_bound}] = {{{expected_text}}};
    {top}(input, output, {length});
    bool pass = true;
    for (int i = 0; i < {observed_bound}; ++i) {{
      if ((double)output[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout << "{REFERENCE_RESULT_TAG} {{\\"case_id\\":\\"{case["id"]}\\",\\"status\\":\\"" << (pass ? "PASS" : "FAIL") << "\\",\\"outputs\\":{{\\"output\\":[";
    for (int i = 0; i < {observed_bound}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << (double)output[i];
    }}
    std::cout << "]}},\\"checkpoints\\":{{\\"length\\":{length},\\"first_output\\":" << (double)output[0] << "}}}}\\n";
    if (!pass) failures++;
  }}''')
    return "\n".join(blocks)


def _mock_multi_m_axi_cases(spec: dict[str, Any], top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    arguments = {
        str(item.get("name")): item
        for item in spec.get("interfaces", {}).get("arguments", [])
        if isinstance(item, dict) and item.get("name")
    }
    interface_depth = max(
        _m_axi_depth(spec, arguments.get("input_a", {})),
        _m_axi_depth(spec, arguments.get("input_b", {})),
        _m_axi_depth(spec, arguments.get("output", {})),
    )
    input_a_type = _argument_storage_type(arguments.get("input_a", {}))
    input_b_type = _argument_storage_type(arguments.get("input_b", {}))
    output_type = _argument_storage_type(arguments.get("output", {}))
    blocks: list[str] = []
    for case in vectors:
        inputs = case.get("inputs", {})
        a_values = [float(item) for item in inputs.get("input_a", [])]
        b_values = [float(item) for item in inputs.get("input_b", [])]
        expected = [float(item) for item in case.get("expected_outputs", {}).get("output", [])]
        length = int(inputs.get("length", min(len(a_values), len(b_values))))
        array_depth = max(1, interface_depth, len(a_values), len(b_values), length, len(expected))
        a_text = ", ".join(_literal_number(item) for item in a_values) or "0"
        b_text = ", ".join(_literal_number(item) for item in b_values) or "0"
        expected_text = ", ".join(_literal_number(item) for item in expected) or "0"
        blocks.append(f'''  {{
    // {_comment(comment_language, f'Run multi-m_axi case {case["id"]} and compare both memory channels.', f'执行 multi-m_axi 用例 {case["id"]} 并比较两个存储通道。')}
    {input_a_type} input_a[{array_depth}] = {{{a_text}}};
    {input_b_type} input_b[{array_depth}] = {{{b_text}}};
    {output_type} output[{array_depth}] = {{}};
    const double expected[{max(1, len(expected))}] = {{{expected_text}}};
    {top}(input_a, input_b, output, {length});
    bool pass = true;
    for (int i = 0; i < {length}; ++i) {{
      if ((double)output[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout << "{REFERENCE_RESULT_TAG} {{\\"case_id\\":\\"{case["id"]}\\",\\"status\\":\\"" << (pass ? "PASS" : "FAIL") << "\\",\\"outputs\\":{{\\"output\\":[";
    for (int i = 0; i < {length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << (double)output[i];
    }}
    std::cout << "]}},\\"checkpoints\\":{{\\"length\\":{length},\\"first_output\\":" << (double)output[0] << "}}}}\\n";
    if (!pass) failures++;
  }}''')
    return "\n".join(blocks)


def _argument_storage_type(argument: dict[str, Any]) -> str:
    return _strip_cpp_storage_type(str(argument.get("type") or "ap_uint<32>"))


def _argument_value_type(argument: dict[str, Any]) -> str:
    return _strip_cpp_storage_type(str(argument.get("type") or "int"))


def _strip_cpp_storage_type(raw_type: str) -> str:
    value = raw_type.replace("const ", "").replace("volatile ", "").strip()
    value = value.replace("&", "").replace("*", "").strip()
    return " ".join(value.split()) or "int"


def _constructor_expr(cpp_type: str, value: float) -> str:
    literal = _literal_number(value)
    if cpp_type in {"int", "unsigned", "unsigned int", "long", "float", "double"}:
        return literal
    return f"{cpp_type}({literal})"


def _literal_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _mock_axis_cases(top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    blocks: list[str] = []
    for case in vectors:
        inputs = case.get("inputs", {})
        values = [int(item) for item in inputs.get("in_stream", [])]
        expected = [int(item) for item in case.get("expected_outputs", {}).get("out_stream", [])]
        length = int(inputs.get("length", len(values)))
        expected_text = ", ".join(str(item) for item in expected) or "0"
        writes = "\n".join(f"    in_stream.write(ap_uint<32>({value}));" for value in values)
        blocks.append(f'''  {{
    // {_comment(comment_language, f'Run AXI-Stream case {case["id"]} and compare the observed output.', f'执行 AXI-Stream 用例 {case["id"]} 并比较真实输出。')}
    hls::stream<ap_uint<32> > in_stream;
    hls::stream<ap_uint<32> > out_stream;
{writes}
    const unsigned expected[{max(1, len(expected))}] = {{{expected_text}}};
    unsigned observed[{max(1, length)}] = {{}};
    {top}(in_stream, out_stream, {length});
    bool pass = true;
    for (int i = 0; i < {length}; ++i) {{
      if (out_stream.empty()) {{
        pass = false;
        observed[i] = 0;
      }} else {{
        observed[i] = (unsigned)out_stream.read();
      }}
      if (observed[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout << "{REFERENCE_RESULT_TAG} {{\\"case_id\\":\\"{case["id"]}\\",\\"status\\":\\"" << (pass ? "PASS" : "FAIL") << "\\",\\"outputs\\":{{\\"out_stream\\":[";
    for (int i = 0; i < {length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << observed[i];
    }}
    std::cout << "]}},\\"checkpoints\\":{{\\"length\\":{length},\\"first_output\\":" << observed[0] << "}}}}\\n";
    if (!pass) failures++;
  }}''')
    return "\n".join(blocks)


def _mock_task_graph_axis_cases(top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    all_values: list[int] = []
    case_records: list[dict[str, Any]] = []
    offset = 0
    for case in vectors:
        inputs = case.get("inputs", {})
        values = [int(item) for item in inputs.get("in_stream", [])]
        expected = [int(item) for item in case.get("expected_outputs", {}).get("out_stream", [])]
        length = int(inputs.get("length", len(values)))
        all_values.extend(values)
        case_records.append({"id": case["id"], "length": length, "offset": offset, "expected": expected})
        offset += length
    total_length = offset
    writes = "\n".join(f"  in_stream.write(ap_uint<32>({value}));" for value in all_values)
    case_blocks: list[str] = []
    for record in case_records:
        expected_text = ", ".join(str(item) for item in record["expected"]) or "0"
        start = int(record["offset"])
        length = int(record["length"])
        case_blocks.append(f'''  {{
    // {_comment(comment_language, f'Validate task-graph slice {record["id"]} after one combined kernel transaction.', f'在一次合并 kernel 事务后校验 task-graph 分段 {record["id"]}。')}
    const unsigned expected[{max(1, len(record["expected"]))}] = {{{expected_text}}};
    bool pass = true;
    for (int i = 0; i < {length}; ++i) {{
      if (observed[{start} + i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout << "{REFERENCE_RESULT_TAG} {{\\"case_id\\":\\"{record["id"]}\\",\\"status\\":\\"" << (pass ? "PASS" : "FAIL") << "\\",\\"outputs\\":{{\\"out_stream\\":[";
    for (int i = 0; i < {length}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << observed[{start} + i];
    }}
    std::cout << "]}},\\"checkpoints\\":{{\\"length\\":{length},\\"first_output\\":" << observed[{start}] << "}}}}\\n";
    if (!pass) failures++;
  }}''')
    return f'''  // {_comment(comment_language, 'Task-graph cosim uses one top-level invocation so the task actor restart contract stays explicit.', 'task-graph cosim 只做一次顶层调用，以保持 task actor 的重启契约显式可控。')}
  hls::stream<ap_uint<32> > in_stream;
  hls::stream<ap_uint<32> > out_stream;
{writes}
  unsigned observed[{max(1, total_length)}] = {{}};
  bool stream_underflow = false;
  {top}(in_stream, out_stream, {total_length});
  for (int i = 0; i < {total_length}; ++i) {{
    if (out_stream.empty()) {{
      stream_underflow = true;
      observed[i] = 0;
    }} else {{
      observed[i] = (unsigned)out_stream.read();
    }}
  }}
{chr(10).join(case_blocks)}'''


def _mock_directio_unit_cases(top: str, vectors: list[dict[str, Any]], comment_language: str) -> str:
    blocks: list[str] = []
    for case in vectors:
        inputs = case.get("inputs", {})
        values = [int(item) for item in inputs.get("in_stream", [])]
        expected = [int(item) for item in case.get("expected_outputs", {}).get("out_stream", [])]
        writes = "\n".join(f"    in_stream.write(ap_uint<32>({value}));" for value in values)
        expected_text = ", ".join(str(item) for item in expected) or "0"
        token_count = len(values)
        blocks.append(f'''  {{
    // {_comment(comment_language, f'Run free-running direct-I/O case {case["id"]} by invoking the kernel once per token.', f'逐 token 调用 free-running direct-I/O 内核以执行用例 {case["id"]}。')}
    hls::stream<ap_uint<32> > in_stream;
    hls::stream<ap_uint<32> > out_stream;
{writes}
    const unsigned expected[{max(1, len(expected))}] = {{{expected_text}}};
    unsigned observed[{max(1, token_count)}] = {{}};
    bool pass = true;
    for (int i = 0; i < {token_count}; ++i) {{
      {top}(in_stream, out_stream);
      if (out_stream.empty()) {{
        pass = false;
        observed[i] = 0;
      }} else {{
        observed[i] = (unsigned)out_stream.read();
      }}
      if (observed[i] != expected[i]) {{
        pass = false;
      }}
    }}
    std::cout << "{REFERENCE_RESULT_TAG} {{\\"case_id\\":\\"{case["id"]}\\",\\"status\\":\\"" << (pass ? "PASS" : "FAIL") << "\\",\\"outputs\\":{{\\"out_stream\\":[";
    for (int i = 0; i < {token_count}; ++i) {{
      if (i != 0) std::cout << ",";
      std::cout << observed[i];
    }}
    std::cout << "]}},\\"checkpoints\\":{{\\"token_count\\":{token_count},\\"first_output\\":" << observed[0] << "}}}}\\n";
    if (!pass) failures++;
  }}''')
    return "\n".join(blocks)


def _comment(comment_language: str, english: str, chinese: str) -> str:
    return chinese if comment_language == "zh" else english
