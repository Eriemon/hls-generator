#!/usr/bin/env python3
"""汇总远端 Vitis、board 和覆盖率验收阶段的信心门结果。"""

# 未来注解避免运行时解析复杂类型提示。
from __future__ import annotations

# 标准库负责远端子进程、JSON 载荷和路径导入边界。
import json
import site
import subprocess
import sys

# 并发执行器只用于互相独立的远端样例验收。
from concurrent.futures import ThreadPoolExecutor, as_completed

# partial 用于把 split 模式的固定远端参数绑定成单样例 worker。
from functools import partial

# Path 用于定位 skill 根目录和本地脚本路径。
from pathlib import Path

# Any 表示远端 JSON payload 的动态字段边界。
from typing import Any

# validation 目录需要加入导入路径，以兼容直接运行本文件的场景。
MODULE_DIR = Path(__file__).resolve().parent  # validation 脚本目录

# skill 根目录承载 scripts.python 包和远端验收脚本。
SKILL_ROOT = Path(__file__).resolve().parents[3]  # erie-hls-generator 技能根目录

# 直接执行 validation 脚本时，先让同目录 helper 可被解析。
site.addsitedir(str(MODULE_DIR))

# scripts.python 包位于 skill 根目录下，测试导入与 CLI 执行共享同一入口。
site.addsitedir(str(SKILL_ROOT))

# board 分区函数读取示例声明中的板卡验收元数据。
from scripts.python.remote.board_acceptance import partition_example_specs_by_board_acceptance

# 配置 helper 提供 skill 内部 examples_dir 的规范路径。
from scripts.python.config.hls_config import skill_config_path

# 远端目录契约校验保证保留的 run 产物可审查。
from scripts.python.remote.remote_directory_contract import validate_remote_result_contract

# 路由契约约束 server_6 等远端目标不能被临时绕过。
from scripts.python.remote.route_contract import load_remote_route_contract, validate_remote_route_target

# 本地 confidence helper 复用进程运行和输出裁剪逻辑。
from confidence_local import _load_tier1_board_matrix, _run_process, _tail

# 所有远端阶段统一使用 runtime JSON 中的通过状态。
PASS_STATUS = "passed"  # 远端阶段通过状态

# 并发上限保护远端 FPGA 主机，避免同一轮验收抢占过多资源。
MAX_REMOTE_PARALLELISM = 3  # 远端样例最大并发数

# 远端 Vitis 验收统一复用同一个 skill 内脚本入口。
REMOTE_VITIS_ACCEPTANCE_SCRIPT = Path(  # 远端 Vitis 验收脚本相对路径
    "scripts",  # skill 内脚本根目录
    "python",  # Python 脚本族目录
    "remote",  # 远端验收脚本目录
    "remote_vitis_acceptance.py",  # 远端 Vitis 验收入口
).as_posix()

# SSH 连接抖动允许重试，综合或板卡验收失败仍保持原始状态。
TRANSIENT_REMOTE_FAILURE_MARKERS = (  # 可重试远端错误片段
    "ssh: connect to host",  # SSH 端口暂时不可达
    "unknown error",  # SSH 客户端偶发未知错误
    "connection reset by peer",  # 远端连接被重置
    "connection closed by remote host",  # 远端主动关闭连接
    "broken pipe",  # 管道在传输阶段断开
)

# 路由契约先于真实远端执行，防止验收目标绕过项目 AGENTS 限制。
def _route_contract_gate(
    server: str | None,
    build_server: str | None,
    validate_server: str | None,
    *,
    remote_requested: bool,
) -> dict[str, Any]:
    """校验本轮远端目标是否符合项目路由契约。

    参数:
        server: 单机远端模式使用的目标服务器名。
        build_server: split 拓扑构建阶段使用的服务器名。
        validate_server: split 拓扑验证阶段使用的服务器名。
        remote_requested: 本轮是否真的请求远端执行。

    返回:
        包含契约状态、模式和违规项的结构化结果字典。
    """

    # 读取当前 skill 的远端路由表，后续校验会复用同一份契约。
    dict_route_contract = load_remote_route_contract(SKILL_ROOT)  # 远端路由契约

    # 本地模式只报告契约内容，不强制匹配远端服务器。
    if not remote_requested:

        # 返回未请求远端验收时的通过状态。
        return {"status": PASS_STATUS, "mode": "not_requested", "contract": dict_route_contract}

    # 校验本轮指定的构建和验证服务器是否符合路由表。
    list_route_issues = validate_remote_route_target(  # 路由违规说明
        dict_route_contract,  # 当前 skill 声明的远端路由契约
        server=server,  # 单机远端模式的目标服务器
        build_server=build_server,  # split 构建阶段目标服务器
        validate_server=validate_server,  # split 验证阶段目标服务器
    )

    # 返回路由契约门禁结果，供总信心门统一聚合。
    return {
        "status": PASS_STATUS if not list_route_issues else "failed",
        "mode": "remote_requested",
        "contract": dict_route_contract,
        "issues": list_route_issues,
    }

# board 声明分区先做静态检查，真实远端执行只处理需要上板的样例。
def _board_acceptance_partition_gate() -> dict[str, Any]:
    """扫描 examples 声明并汇总 board 验收分区。

    参数:
        无。

    返回:
        包含 board 必验、豁免与无效样例清单的结构化结果字典。
    """

    # 从 examples_dir 中提取 board 必验、豁免和声明错误清单。
    dict_board_partition = partition_example_specs_by_board_acceptance(skill_config_path("examples_dir"))  # board 验收分区

    # 声明错误需要在远端执行前暴露，避免硬件资源被无效输入占用。
    list_invalid_specs = dict_board_partition["invalid_specs"]  # board 声明错误样例

    # 返回分区结果，同时把无声明错误视为静态通过。
    return {
        "status": PASS_STATUS if not list_invalid_specs else "failed",
        **dict_board_partition,
    }

# 远端目录契约确认每个远端结果都留下可追溯 run_id 和产物边界。
def _remote_directory_contract_gate(remote_results: list[dict[str, Any]], *, remote_requested: bool) -> dict[str, Any]:
    """检查远端结果是否满足 run 产物目录契约。

    参数:
        remote_results: 已收集到的远端阶段结果列表。
        remote_requested: 本轮是否要求真实远端执行。

    返回:
        包含目录契约状态、模式与逐项检查结果的结构化结果字典。
    """

    # 未请求远端时不要求动态 run 结果，但仍保留门禁项。
    if not remote_requested:

        # 返回静态契约模式，供报告中说明远端阶段未执行。
        return {"status": PASS_STATUS, "mode": "static_contract_only", "results": []}

    # 请求远端却没有任何结果时，目录契约无法被事实验证。
    if not remote_results:

        # 返回缺失远端结果的失败原因。
        return {"status": "failed", "mode": "remote_required", "results": [], "issues": ["remote results missing"]}

    # 汇总每个远端阶段的目录契约校验结果。
    list_results: list[dict[str, Any]] = []  # 远端目录契约逐项结果

    # 每个远端阶段都必须独立校验 run_id、保留路径和归档边界。
    for item in remote_results:

        # 提取当前远端 payload 的目录契约错误。
        list_contract_errors = validate_remote_result_contract(item)  # 当前远端结果契约错误

        # 记录当前样例或阶段的契约状态，保留 run_id 便于追查。
        list_results.append(
            {
                "example_spec": str(item.get("example_spec") or item.get("phase") or ""),
                "run_id": item.get("run_id"),
                "status": PASS_STATUS if not list_contract_errors else "failed",
                "issues": list_contract_errors,
            }
        )

    # 只有所有远端结果都满足目录契约时，本门禁才算通过。
    bool_passed = all(entry["status"] == PASS_STATUS for entry in list_results)  # 目录契约整体状态

    # 返回远端目录契约门禁聚合结果。
    return {
        "status": PASS_STATUS if bool_passed else "failed",
        "mode": "remote_result_validation",
        "results": list_results,
    }

# 远端脚本通过 JSON stdout 返回结构化结果，本函数统一超时和解析失败形态。
def _run_remote_command(command: list[str], *, timeout_s: int = 900) -> dict[str, Any]:
    """执行远端验收命令并规范化返回载荷。

    参数:
        command: 传给远端验收脚本的命令行参数列表。
        timeout_s: 当前命令允许的最长执行秒数。

    返回:
        包含状态、返回码、输出摘要与超时信息的结构化结果字典。
    """

    # 在 skill 根目录执行远端子命令，保证相对脚本路径稳定。
    dict_process_result = _run_process(command, cwd=SKILL_ROOT, timeout_s=timeout_s)  # 子进程执行结果

    # 超时保留 stdout/stderr 尾部，方便判断是 SSH 还是 Vitis 阶段卡住。
    if dict_process_result["timed_out"]:

        # 返回超时载荷，避免后续 JSON 解析误判。
        return dict(
            status="timeout",
            command=command,
            returncode=None,
            timeout_s=timeout_s,
            stdout_tail=_tail(dict_process_result["stdout"]),
            stderr_tail=_tail(dict_process_result["stderr"]),
        )

    # 远端验收脚本约定 stdout 是单个 JSON 文档。
    try:

        # 解析远端脚本机器可读输出，保留原始字段供上层聚合。
        dict_payload = json.loads(dict_process_result["stdout"])  # 远端阶段 JSON 载荷

    # 非 JSON 输出通常意味着远端脚本崩溃或 shell 层错误。
    except json.JSONDecodeError:

        # 解析失败时转成失败载荷，并裁剪输出避免报告过长。
        dict_payload = {  # 非 JSON 远端输出摘要
        "status": "failed",  # 非 JSON 输出统一记为失败
        "stdout_tail": _tail(dict_process_result["stdout"]),  # 标准输出尾部摘要
        "stderr_tail": _tail(dict_process_result["stderr"]),  # 标准错误尾部摘要
        }

    # 返回码是本地进程事实证据，不覆盖远端 payload 的业务状态。
    dict_payload["returncode"] = dict_process_result["returncode"]  # 本地子进程返回码

    # 超时阈值写入结果，便于审查报告复现实验设置。
    dict_payload["timeout_s"] = timeout_s  # 本次远端命令超时阈值

    # 返回标准化后的远端命令结果。
    return dict_payload

# 只把连接层抖动识别为可重试，综合失败和 board 失败不能被隐藏。
def _is_transient_remote_failure(payload: dict[str, Any]) -> bool:
    """判断失败载荷是否属于可重试的连接层抖动。

    参数:
        payload: 单次远端命令返回的结构化结果载荷。

    返回:
        如果错误文本命中瞬时 SSH 故障标记则返回 `True`。
    """

    # 已通过的阶段不需要进入重试判断。
    if str(payload.get("status") or "") == PASS_STATUS:

        # 通过状态不是瞬时失败。
        return False

    # 拼接远端错误字段，用小写匹配 SSH 连接层错误片段。
    str_failure_text = "\n".join(  # 远端错误文本合并视图
        str(payload.get(str_role_key) or "")  # 当前错误字段的文本片段
        for str_role_key in ("error", "message", "stdout_tail", "stderr_tail")  # 参与瞬时故障判断的字段名
    ).lower()

    # 返回是否命中允许重试的连接层错误。
    return any(marker in str_failure_text for marker in TRANSIENT_REMOTE_FAILURE_MARKERS)

# 远端命令只在连接层抖动时重试一次，避免重复消耗 FPGA 编译资源。
def _run_remote_command_with_retry(command: list[str], *, timeout_s: int = 900, retries: int = 1) -> dict[str, Any]:
    """在允许范围内对瞬时远端故障执行重试。

    参数:
        command: 要执行的远端命令行参数列表。
        timeout_s: 单次命令执行的超时秒数。
        retries: 允许追加的重试次数。

    返回:
        原始执行或最后一次重试得到的结构化结果字典。
    """

    # 先执行一次远端命令，失败原因决定是否重试。
    dict_payload = _run_remote_command(command, timeout_s=timeout_s)  # 当前远端命令载荷

    # 记录已经执行的重试次数，写入最终 payload 供报告审计。
    int_attempt = 0  # 已完成重试次数

    # 只要仍有重试额度且失败属于连接抖动，就再次执行同一命令。
    while int_attempt < retries and _is_transient_remote_failure(dict_payload):

        # 递增重试次数，确保最终报告能解释重复远端调用。
        int_attempt += 1  # 当前重试序号

        # 重试使用相同命令和超时，保持验收条件一致。
        dict_payload = _run_remote_command(command, timeout_s=timeout_s)  # 重试后的远端命令载荷

        # 将重试次数写回最终载荷，方便审查连接稳定性。
        dict_payload["retry_count"] = int_attempt  # 命中的远端重试次数

    # 返回原始执行或最后一次重试的标准化结果。
    return dict_payload

# 并发数量同时受用户参数、任务数量和项目上限约束。
def _parallelism_limit(parallelism: int, item_count: int) -> int:
    """把用户请求并发裁剪到任务数和项目上限之内。

    参数:
        parallelism: 用户请求的远端并发数。
        item_count: 当前待执行的样例数量。

    返回:
        至少为 1 且不超过项目上限的实际并发数。
    """

    # 返回不会超过远端资源保护上限的 worker 数。
    return max(1, min(max(1, int(parallelism)), max(1, item_count), MAX_REMOTE_PARALLELISM))

# 并行运行样例时保留输入顺序，避免报告顺序随线程完成顺序漂移。
def _run_parallel_specs(spec_names: list[str], worker, *, parallelism: int) -> list[dict[str, Any]]:
    """并行运行多个样例并保持结果顺序与输入一致。

    参数:
        spec_names: 需要执行的样例名列表。
        worker: 接收单个样例名并返回结构化结果的执行函数。
        parallelism: 用户请求的远端并发数。

    返回:
        按输入样例顺序排列的远端结果列表。
    """

    # 单样例或禁用并发时走顺序路径，便于调试和复现。
    if len(spec_names) <= 1 or parallelism <= 1:

        # 返回按输入顺序执行得到的远端结果。
        return [worker(str_spec_name) for str_spec_name in spec_names]

    # 计算受保护的并发 worker 数。
    int_max_workers = _parallelism_limit(parallelism, len(spec_names))  # 实际远端并发数

    # 预分配结果槽位，线程完成后按原始索引写回。
    list_ordered_results: list[dict[str, Any] | None] = [None] * len(spec_names)  # 保序远端结果槽

    # 线程池只承载独立样例，单个样例内部仍由远端脚本串行控制。
    with ThreadPoolExecutor(max_workers=int_max_workers, thread_name_prefix="hls-remote-review") as executor:

        # future 到原始索引的映射用于恢复输入顺序。
        dict_future_index = {  # 远端 future 对应的样例索引
            executor.submit(worker, str_spec_name): int_result_index  # 当前 future 对应的原始位置
            for int_result_index, str_spec_name in enumerate(spec_names)  # 按输入顺序枚举样例
        }

        # 按完成顺序收集结果，但写回输入顺序对应的位置。
        for future_result in as_completed(dict_future_index):

            # 找回当前 future 对应的输入样例位置。
            int_result_index = dict_future_index[future_result]  # 当前 future 的原始索引

            # 保存当前 future 的远端验收结果。
            list_ordered_results[int_result_index] = future_result.result()  # 当前样例远端结果

    # 过滤理论上的空槽，返回稳定顺序的结果列表。
    return [item for item in list_ordered_results if isinstance(item, dict)]

# 单机 Vitis 验收先确认 link 模式，再分发每个样例的远端 Vitis 阶段。
def _run_remote_acceptance(
    server: str,
    readiness: str,
    example_specs: list[str],
    *,
    vitis_version: str | None = None,
    parallelism: int = 1,
) -> dict[str, Any]:
    """执行单机远端 Vitis 验收并聚合所有样例结果。

    参数:
        server: 单机远端模式使用的目标服务器名。
        readiness: 远端验收深度，例如 synth、cosim 或 board。
        example_specs: 本轮需要验收的样例名列表。
        vitis_version: 可选的远端 Vitis 版本选择。
        parallelism: 用户请求的远端并发数。

    返回:
        包含 link 预检结果和逐样例 Vitis 结果的结构化结果字典。
    """

    # link 模式先确认远端工具链和工作区可用。
    dict_link_payload = _run_remote_command_with_retry(  # 远端 link 预检结果
        [
            sys.executable,  # 当前 Python 解释器入口
            REMOTE_VITIS_ACCEPTANCE_SCRIPT,  # 远端 Vitis 验收脚本
            "--mode",  # 远端执行模式参数名
            "link",  # 先验证链路是否可达的模式
            "--server",  # 单机远端服务器参数名
            server,  # 本轮目标远端服务器
            "--timeout",  # link 预检超时参数名
            "300",  # link 预检允许的秒数
            "--json",  # 要求远端脚本输出 JSON
        ],
        retries=1,  # link 预检只允许一次连接层重试
    )

    # link 失败时不继续占用远端资源跑样例验收。
    if dict_link_payload.get("status") != PASS_STATUS:

        # 返回 link 失败载荷，保留空结果表明样例阶段未执行。
        return dict(
            status="failed",
            server=server,
            vitis_version=vitis_version,
            link=dict_link_payload,
            results=[],
        )

    # 对已选择的样例运行远端 Vitis 验收，必要时并行。
    list_vitis_results = _run_parallel_specs(  # 每个样例的 Vitis 验收结果
        example_specs,  # 本轮选中的样例名列表
        lambda str_spec_name: _run_remote(server, readiness, str_spec_name, vitis_version=vitis_version),  # 单样例远端 Vitis worker
        parallelism=parallelism,  # 用户请求的远端并发数
    )

    # Vitis 阶段必须全部通过，并确认远端产物被保留。
    bool_passed = dict_link_payload.get("status") == PASS_STATUS and all(  # 单机 Vitis 验收整体状态
        item.get("status") == PASS_STATUS and bool(item.get("remote_artifacts_retained"))  # 当前样例需要同时通过并保留远端产物
        for item in list_vitis_results  # 参与单机 Vitis 聚合的样例结果
    )

    # 返回单机远端 Vitis 验收结果。
    return dict(
        status=PASS_STATUS if bool_passed else "failed",
        server=server,
        vitis_version=vitis_version,
        link=dict_link_payload,
        results=list_vitis_results,
    )

# 单个样例的 Vitis 远端命令由公共 runner 统一解析和重试。
def _run_remote(server: str, readiness: str, str_spec_name: str, *, vitis_version: str | None = None) -> dict[str, Any]:
    """执行单个样例的远端 Vitis 验收命令。

    参数:
        server: 当前单机远端模式使用的服务器名。
        readiness: 远端验收深度，例如 synth、cosim 或 board。
        str_spec_name: 当前要验收的样例名。
        vitis_version: 可选的远端 Vitis 版本选择。

    返回:
        当前样例对应的远端 Vitis 结构化结果字典。
    """

    # 远端 Vitis 命令先固定解释器与脚本入口，再逐段追加模式、样例和输出约束。
    list_command = [sys.executable, REMOTE_VITIS_ACCEPTANCE_SCRIPT]  # Vitis 远端脚本入口

    # 单机模式需要显式声明 Vitis 验收模式和目标服务器。
    list_command.extend(["--mode", "vitis", "--server", server])  # 单机服务器与 Vitis 执行模式

    # readiness 和 example-spec 共同决定当前要跑哪一个样例与深度。
    list_command.extend(["--readiness", readiness, "--example-spec", str_spec_name])  # 当前验收深度与样例名

    # 当前项目要求远端脚本返回中文注释和 JSON 机器输出。
    list_command.extend(["--comment-language", "zh", "--json"])  # 中文注释与 JSON 输出约束

    # 指定 Vitis 版本时，把版本选择显式传给远端脚本。
    if vitis_version:

        # 追加远端 Vitis 版本参数，保持命令主体可读。
        list_command.extend(["--vitis-version", vitis_version])

    # 执行远端 Vitis 阶段，综合类任务使用更长超时。
    dict_payload = _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)  # Vitis 样例远端载荷

    # 写回样例名，便于上层覆盖率和目录契约聚合。
    dict_payload["example_spec"] = str_spec_name  # 当前远端样例名

    # 返回当前样例的 Vitis 远端结果。
    return dict_payload

# 单个样例的 board 阶段必须在 Vitis 通过后独立执行。
def _run_remote_board(
    server: str,
    readiness: str,
    str_spec_name: str,
    *,
    vitis_version: str | None = None,
) -> dict[str, Any]:
    """执行单个样例的真实上板远端验收。

    参数:
        server: 当前单机远端模式使用的服务器名。
        readiness: 远端验收深度，通常沿用前置 Vitis 阶段设置。
        str_spec_name: 当前需要上板的样例名。
        vitis_version: 可选的远端 Vitis 版本选择。

    返回:
        当前样例对应的远端 board 结构化结果字典。
    """

    # board 命令除了样例与服务器信息，还要把上板链路专用超时显式传给远端脚本。
    list_command = [sys.executable, REMOTE_VITIS_ACCEPTANCE_SCRIPT]  # 真实上板命令入口

    # 单机 board 验收必须固定为真实上板模式并绑定目标服务器。
    list_command.extend(["--mode", "board", "--server", server])  # 单机服务器与真实上板模式

    # readiness 和 example-spec 决定当前要上板执行的样例与验收深度。
    list_command.extend(["--readiness", readiness, "--example-spec", str_spec_name])  # 当前上板样例与验收深度

    # board 阶段要求中文注释、5400 秒脚本自管超时以及 JSON 机器输出。
    list_command.extend(["--comment-language", "zh", "--timeout", "5400", "--json"])  # 上板链路输出和时限约束

    # 版本参数可选，未指定时使用远端默认 Vitis 配置。
    if vitis_version:

        # 追加 Vitis 版本选择，确保 board 阶段与 Vitis 阶段一致。
        list_command.extend(["--vitis-version", vitis_version])

    # 公共 runner 负责保留上板超时、SSH 抖动和非 JSON 失败的统一证据形态。
    dict_payload = _run_remote_command_with_retry(list_command, timeout_s=5400, retries=1)  # 上板阶段标准化执行结果

    # 写回样例名，让 board 结果可与 Vitis 覆盖率交叉匹配。
    dict_payload["example_spec"] = str_spec_name  # 当前 board 样例名

    # 把当前样例的 board 事实证据交回 gate 聚合层。
    return dict_payload

# 分离构建和验证服务器时，单个样例通过 build/validate 参数路由。
def _run_split_remote(
    build_server: str,
    validate_server: str,
    readiness: str,
    str_spec_name: str,
    *,
    vitis_version: str | None = None,
) -> dict[str, Any]:
    """执行 split build/validate 拓扑下的单样例远端验收。

    参数:
        build_server: 负责远端构建阶段的服务器名。
        validate_server: 负责远端验证阶段的服务器名。
        readiness: 远端验收深度，例如 synth、cosim 或 board。
        str_spec_name: 当前要验收的样例名。
        vitis_version: 可选的远端 Vitis 版本选择。

    返回:
        当前样例对应的 split 远端结构化结果字典。
    """

    # split 命令同样先固定解释器与脚本入口，再分段写入双机拓扑参数。
    list_command = [sys.executable, REMOTE_VITIS_ACCEPTANCE_SCRIPT]  # 双机拓扑命令入口

    # split 拓扑需要同时声明 Vitis 模式、构建服务器和验证服务器。
    list_command.extend(["--mode", "vitis", "--build-server", build_server, "--validate-server", validate_server])  # 双机拓扑路由参数

    # readiness 和 example-spec 共同标识当前分离构建验证要处理的样例。
    list_command.extend(["--readiness", readiness, "--example-spec", str_spec_name])  # 当前 split 样例与验收深度

    # split 远端脚本仍要求中文注释和 JSON 输出，便于与单机场景统一聚合。
    list_command.extend(["--comment-language", "zh", "--json"])  # split 模式输出约束

    # 指定版本时，build 和 validate 两端都由远端脚本统一处理。
    if vitis_version:

        # 追加 Vitis 版本参数，避免 split 模式使用隐式默认版本。
        list_command.extend(["--vitis-version", vitis_version])

    # split 拓扑复用同一 runner，避免双机路径产生额外的失败载荷分叉。
    dict_payload = _run_remote_command_with_retry(list_command, retries=1)  # split 拓扑标准化执行结果

    # 补写样例名，让 split 结果和单机结果共享同一覆盖率索引键。
    dict_payload["example_spec"] = str_spec_name  # 覆盖率聚合使用的 split 样例键

    # 返回当前样例的 split 远端验收结果。
    return dict_payload

# split 验收先跑首个样例验证链路，再并行分发剩余样例。
def _run_split_remote_acceptance(
    build_server: str, validate_server: str, readiness: str, example_specs: list[str],
    *, vitis_version: str | None = None, parallelism: int = 1,
) -> dict[str, Any]:
    """执行 split build/validate 拓扑下的整批样例远端验收。

    参数:
        build_server: 负责远端构建阶段的服务器名。
        validate_server: 负责远端验证阶段的服务器名。
        readiness: 远端验收深度，例如 synth、cosim 或 board。
        example_specs: 本轮需要验收的样例名列表。
        vitis_version: 可选的远端 Vitis 版本选择。
        parallelism: 用户请求的远端并发数。

    返回:
        包含首样例链路预热结果和批量样例结果的结构化结果字典。
    """

    # 没有样例时 split 验收无法提供事实证据。
    if not example_specs:

        # 返回失败状态，明确没有任何远端样例被执行。
        return dict(
            status="failed",
            topology="split_build_validate",
            build_server=build_server,
            validate_server=validate_server,
            vitis_version=vitis_version,
            results=[],
        )

    # 首个样例承担链路预热职责，用于验证 build 到 validate 的完整闭环。
    str_first_spec = example_specs[0]  # 用于链路预热的首个样例名

    # 真正执行首个样例时，需要沿用同一组服务器、深度和版本选择。
    dict_first_result = _run_split_remote(  # split 首个样例结果
        build_server,  # 负责综合与链接的服务器
        validate_server,  # 负责远端验收的服务器
        readiness,  # 首样例沿用的验收深度
        str_first_spec,  # 当前链路预热样例名
        vitis_version=vitis_version,  # 本轮固定的 Vitis 版本
    )

    # 首个样例失败时停止后续并行任务，避免扩大远端消耗。
    if dict_first_result.get("status") != PASS_STATUS:

        # 返回首个样例失败证据，说明剩余样例未执行。
        return dict(
            status="failed",
            topology="split_build_validate",
            build_server=build_server,
            validate_server=validate_server,
            vitis_version=vitis_version,
            results=[],
            first_result=dict_first_result,
        )

    # 其余样例复用同一组服务器与版本配置，只把样例名留给并发 worker 注入。
    func_split_worker = partial(  # split 并发 worker 绑定器
        _run_split_remote,  # 复用 split 单样例执行入口
        build_server,  # 固定综合与链接服务器
        validate_server,  # 固定后续验证服务器
        readiness,  # 固定本轮远端验收深度
        vitis_version=vitis_version,  # 固定本轮 Vitis 版本选择
    )

    # 首个样例通过后，剩余样例才值得并发下发到 split 拓扑。
    list_remaining = _run_parallel_specs(example_specs[1:], func_split_worker, parallelism=parallelism)  # split 剩余样例结果

    # 合并首个样例和剩余样例结果，保持输入顺序。
    list_results = [dict_first_result, *list_remaining]  # split 全部样例结果

    # split 通过条件是所有样例都成功，并且每个样例都保留了远端产物。
    bool_passed = all(  # split 验收整体状态
        item.get("status") == PASS_STATUS and bool(item.get("remote_artifacts_retained"))  # 当前样例必须通过且保留远端产物
        for item in list_results  # 参与 split 聚合的全部样例结果
    )

    # 返回 split build/validate 远端验收结果。
    return dict(
        status=PASS_STATUS if bool_passed else "failed",
        topology="split_build_validate",
        build_server=build_server,
        validate_server=validate_server,
        vitis_version=vitis_version,
        results=list_results,
    )

# 兼容旧入口参数，同时让实际 board 逻辑使用紧凑上下文字典。
def _remote_board_context_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """把兼容层参数归一化为 board gate 所需的上下文对象。

    参数:
        args: 兼容旧入口保留的位置参数元组。
        kwargs: 调用方传入的关键字参数字典。

    返回:
        供 board gate 实际实现消费的结构化上下文字典。

    异常:
        TypeError: 位置参数数量不正确或出现未声明关键字参数时抛出。
    """

    # board gate 的兼容入口只接受 server 和 readiness 两个位置参数。
    if len(args) != 2:

        # 调用形态错误会让 board 验收上下文不可信，直接报告给测试或 CLI。
        raise TypeError("> ERR: [Python] _remote_board_acceptance_gate requires server and readiness positional args")

    # 复制关键字参数，避免 pop 操作修改调用方持有的字典。
    dict_options = dict(kwargs)  # board gate 关键字参数副本

    # 位置参数中的 server 可以为 None，其他值统一转成字符串。
    str_server = None if args[0] is None else str(args[0])  # 单机远端服务器名

    # readiness 表示远端验收深度，保持字符串形式传给远端脚本。
    str_readiness = str(args[1])  # 远端验收 readiness 模式

    # Vitis 版本可选，None 表示沿用远端环境的默认工具链版本。
    str_vitis_version = dict_options.pop("vitis_version")  # board 阶段沿用的 Vitis 版本钉住值

    # 是否真实请求远端 board 阶段由总信心门计算后传入。
    bool_remote_requested = bool(dict_options.pop("remote_requested"))  # 是否执行远端 board 阶段

    # Vitis 门禁结果用于保证 board 阶段不会越过前置验收。
    dict_remote_vitis_gate = dict_options.pop("remote_vitis_gate")  # 前置 Vitis 门禁结果

    # 静态扫描得到的 board 分区决定哪些样例必须上板、哪些样例仅作为豁免记录。
    dict_board_partition = dict_options.pop("board_partition")  # 必验、豁免与无效样例的声明分区

    # 选中样例统一复制成列表，避免后续 membership 判断依赖调用方容器类型。
    list_selected_specs = list(dict_options.pop("selected_specs"))  # 本轮选中的样例名

    # 并发参数在上下文边界转为整数，执行层只处理确定类型。
    int_parallelism = int(dict_options.pop("parallelism"))  # board 远端并发请求数

    # 未识别关键字意味着调用方和 gate 契约不同步。
    if dict_options:

        # 报告多余关键字，防止静默忽略新增控制字段。
        raise TypeError(f"> ERR: [Python] unexpected board acceptance options: {sorted(dict_options)}")

    # 返回压缩后的 board 验收上下文，字段名与原调用参数保持一致。
    return {
        "server": str_server,
        "readiness": str_readiness,
        "vitis_version": str_vitis_version,
        "remote_requested": bool_remote_requested,

        # 远端 Vitis 与 board 分区字段体量较大，单独留出视觉边界。
        "remote_vitis_gate": dict_remote_vitis_gate,
        "board_partition": dict_board_partition,
        "selected_specs": list_selected_specs,
        "parallelism": int_parallelism,
    }

# board gate 对外保留旧调用形态，内部用上下文对象降低参数复杂度。
def _remote_board_acceptance_gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """兼容旧签名并转发到新的 board gate 实现。

    参数:
        args: 兼容层保留的位置参数元组。
        kwargs: 兼容层保留的关键字参数字典。

    返回:
        由实际 board gate 实现产出的结构化结果字典。
    """

    # 将旧签名参数归一化为不可变上下文。
    dict_context = _remote_board_context_from_args(args, kwargs)  # board 验收调用上下文

    # 交给实际实现，避免兼容层混入验收业务分支。
    return _remote_board_acceptance_gate_from_context(dict_context)

# 只保留本轮选中且声明要求上板或豁免的样例条目。
def _selected_board_partition_entries(
    dict_context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """筛选当前批次真正相关的 board 必验与豁免条目。

    参数:
        dict_context: board gate 已归一化的执行上下文字典。

    返回:
        由必验条目列表和豁免条目列表组成的元组。
    """

    # selected_specs 转为集合后复用，避免两个分区筛选重复构造集合。
    set_selected_specs = set(dict_context["selected_specs"])  # 本轮选中样例集合

    # 从 board 分区中筛出本轮真正需要上板的样例。
    list_board_specs = [  # 本轮需要 board 验收的样例声明
        entry  # 当前需要真实上板的样例声明条目
        for entry in dict_context["board_partition"].get("board_specs", [])  # 静态声明中要求上板的样例条目
        if entry["spec"] in set_selected_specs  # 过滤掉未进入本轮的必验样例
    ]

    # 豁免样例进入报告，但不会触发远端 board 执行。
    list_exempt_specs = [  # 本轮 board 豁免样例声明
        entry  # 当前无需上板但要进入报告的豁免条目
        for entry in dict_context["board_partition"].get("exempt_specs", [])  # 静态声明中的豁免样例条目
        if entry["spec"] in set_selected_specs  # 过滤掉未进入本轮的豁免样例
    ]

    # 返回本轮有效的 board 必验和豁免样例声明。
    return list_board_specs, list_exempt_specs

# 聚合 board 阶段结果，区分通过、阻塞和失败三种状态。
def _board_gate_status(list_results: list[dict[str, Any]]) -> str:
    """把多个 board 子结果聚合为最终状态。

    参数:
        list_results: 已完成的 board 样例结果列表。

    返回:
        `passed`、`blocked` 或 `failed` 之一。
    """

    # 所有 board 返回状态都会进入聚合判定。
    set_statuses = {str(item.get("status")) for item in list_results}  # board 阶段状态集合

    # 只有所有上板样例都返回 passed，board gate 才能直接给出通过结论。
    if set_statuses == {PASS_STATUS}:

        # 记录 board gate 的最终通过状态。
        return PASS_STATUS

    # 路由缺失、profile 配置缺失或版本不可选等前置问题都应保持阻塞而不是失败。
    if any(
        item in set_statuses
        for item in {"blocked_board_validation", "blocked_remote_profile_config", "blocked_remote_version_choice"}
    ):

        # 记录 board gate 的阻塞状态，便于最终报告要求补证据。
        return "blocked"

    # 其他非通过状态视为 board 验收失败。
    return "failed"

# 单机 board 验收只处理声明需要上板且本轮被选中的样例。
def _remote_board_acceptance_gate_from_context(dict_context: dict[str, Any]) -> dict[str, Any]:
    """基于归一化上下文执行单机 board 验收门禁。

    参数:
        dict_context: board gate 已归一化的执行上下文字典。

    返回:
        包含 board 声明、执行结果与最终状态的结构化结果字典。
    """

    # board_acceptance 元数据一旦声明错误，远端执行无法补救，必须先在本地阻断。
    list_invalid_specs = dict_context["board_partition"].get("invalid_specs", [])  # board_acceptance 元数据不合法的样例名列表

    # 有声明错误时直接失败，避免继续启动远端 board 任务。
    if list_invalid_specs:

        # 返回无效 board 元数据说明。
        return {"status": "failed", "reason": "invalid_board_acceptance_metadata", "invalid_specs": list_invalid_specs}

    # 先取回本轮需要上板和被豁免的样例声明元组。
    tuple_board_partition_entries = _selected_board_partition_entries(dict_context)  # 本轮有效 board 分区元组

    # 第一个返回值是需要真实上板执行的样例声明条目。
    list_board_specs = tuple_board_partition_entries[0]  # 需要真实上板执行的样例声明条目

    # 第二个返回值是只进入报告、不会触发远端执行的豁免样例声明。
    list_exempt_specs = tuple_board_partition_entries[1]  # 仅保留在报告中的豁免样例声明条目

    # 未请求远端时只验证声明分区，不执行硬件阶段。
    if not dict_context["remote_requested"]:

        # 返回声明模式结果，供报告展示 board 必验和豁免清单。
        return {
            "status": PASS_STATUS,
            "mode": "declarations_only",
            "board_specs": list_board_specs,
            "exempt_specs": list_exempt_specs,
            "results": [],
        }

    # board 阶段必须绑定单一远端服务器，split 模式不在这里执行。
    if not dict_context["server"]:

        # 返回缺少单机服务器的失败状态。
        return {"status": "failed", "reason": "board acceptance requires a single remote server", "results": []}

    # board 阶段依赖 Vitis 阶段先通过，防止跳过 bitstream 构建事实。
    if not dict_context["remote_vitis_gate"] or dict_context["remote_vitis_gate"].get("status") != PASS_STATUS:

        # 返回 blocked 而非 failed，表示前置证据不足。
        return {
            "status": "blocked",
            "reason": "board acceptance requires successful remote vitis acceptance first",
            "results": [],
        }

    # 当前选择没有 board 必验样例时，远端 board 阶段自然通过。
    if not list_board_specs:

        # 返回空 board 执行结果，并保留豁免样例信息。
        return {
            "status": PASS_STATUS,
            "mode": "no_board_specs_selected",
            "board_specs": [],
            "exempt_specs": list_exempt_specs,
            "results": [],
        }

    # 先抽取样例名，再把服务器与版本配置绑定成 board 并发 worker。
    list_target_board_specs = [entry["spec"] for entry in list_board_specs]  # 需要真实上板的样例名列表

    # board worker 只接收样例名，服务器、readiness 和版本由上下文预先固定。
    func_board_worker = partial(  # 预绑定服务器、深度与版本的上板执行器
        _run_remote_board,  # 直接进入真实上板子命令入口
        dict_context["server"],  # 单机远端服务器
        dict_context["readiness"],  # 当前远端验收深度
        vitis_version=dict_context["vitis_version"],  # board 阶段沿用的 Vitis 版本
    )

    # 读取本轮 board 阶段的并发数，避免并发调用行过长。
    int_board_parallelism = dict_context["parallelism"]  # board 阶段请求的并发数

    # 对所有需上板样例运行 board 远端验收。
    list_results = _run_parallel_specs(list_target_board_specs, func_board_worker, parallelism=int_board_parallelism)  # board 样例远端结果

    # 聚合 board 返回状态，区分阻塞和真实失败。
    str_status = _board_gate_status(list_results)  # board gate 聚合状态

    # 返回 board gate 的完整审查载荷。
    return {
        "status": str_status,
        "mode": "remote_board_validation",
        "board_specs": list_board_specs,
        "exempt_specs": list_exempt_specs,
        "results": list_results,
    }

# tier1 覆盖率从矩阵中提取代表样例和高风险样例。
def _required_tier1_specs(dict_board_matrix: dict[str, Any]) -> list[str]:
    """从 tier1 board 矩阵中提取必须覆盖的样例名。

    参数:
        dict_board_matrix: 描述 family 代表样例与高风险样例的矩阵配置。

    返回:
        按矩阵声明顺序去重后的必验样例名列表。
    """

    # families 字段缺失或格式错误时按空覆盖要求处理。
    dict_families = dict_board_matrix.get("families", {}) if isinstance(dict_board_matrix, dict) else {}  # tier1 家族配置

    # 需求样例保持声明顺序，后续报告按矩阵顺序展示缺口。
    list_required_specs: list[str] = []  # tier1 必须覆盖样例

    # 每个 family 至多贡献 representative 和 high_risk 两个样例。
    for dict_family_config in dict_families.values():

        # 非字典 family 配置忽略，避免坏数据中断整个报告。
        if not isinstance(dict_family_config, dict):

            # 跳过格式不符合预期的 family 配置。
            continue

        # 代表样例和高风险样例都需要出现在 tier1 覆盖证据中。
        for str_role_key in ("representative", "high_risk"):

            # 读取当前角色对应的样例名，并去掉配置里的多余空白。
            str_required_spec = str(dict_family_config.get(str_role_key) or "").strip()  # 当前角色要求的样例名

            # 同一样例可能同时承担两个角色，覆盖要求中只保留一次。
            if str_required_spec and str_required_spec not in list_required_specs:

                # 按矩阵声明顺序追加新的必验样例。
                list_required_specs.append(str_required_spec)

    # 返回 tier1 覆盖所需的样例名列表。
    return list_required_specs

# 从远端结果中过滤指定阶段已经通过的样例名。
def _passed_specs_for_phase(results: list[dict[str, Any]], *, phase: str) -> set[str]:
    """收集指定阶段已经通过的样例名集合。

    参数:
        results: 远端阶段执行结果列表。
        phase: 需要筛选的阶段名，例如 `vitis` 或 `board`。

    返回:
        在目标阶段已经通过的样例名集合。
    """

    # 只收集阶段名和状态都匹配的样例。
    set_passed_specs = {  # 指定阶段已通过样例集合
        str(item.get("example_spec") or "")  # 当前结果对应的样例名
        for item in results  # 待筛选的远端阶段结果
        if str(item.get("phase") or "") == phase and str(item.get("status") or "") == PASS_STATUS  # 只保留目标阶段且状态通过的结果
    }

    # 返回指定阶段的通过样例集合。
    return set_passed_specs

# 远端覆盖率门禁确认 tier1 矩阵要求的样例同时具备 Vitis 和 board 证据。
def _remote_family_coverage_gate(results: list[dict[str, Any]], *, coverage_mode: str) -> dict[str, Any]:
    """检查远端验收结果是否满足 tier1 family 覆盖要求。

    参数:
        results: 已收集到的远端阶段结果列表。
        coverage_mode: 当前启用的覆盖率模式。

    返回:
        包含必验样例、已通过样例和缺口清单的结构化结果字典。
    """

    # 非 tier1 模式只报告跳过，不制造缺口。
    if coverage_mode != "tier1":

        # 返回跳过状态，保留统一字段便于报告消费。
        return {
            "status": "skipped",
            "mode": coverage_mode,
            "required_specs": [],
            "vitis_passed_specs": [],
            "board_passed_specs": [],
            "missing_specs": [],
        }

    # 读取 tier1 board 矩阵，获得代表样例和高风险样例要求。
    dict_board_matrix = _load_tier1_board_matrix()  # tier1 board 覆盖矩阵

    # tier1 矩阵会产出一组必须同时具备 Vitis 与 board 证据的目标样例。
    list_required_specs = _required_tier1_specs(dict_board_matrix)  # tier1 必须同时覆盖的样例名列表

    # 先收集已经具备远端 Vitis 成功证据的样例集合。
    set_vitis_passed = _passed_specs_for_phase(results, phase="vitis")  # 已具备远端 Vitis 成功证据的样例集合

    # 再收集已经具备真实上板成功证据的样例集合。
    set_board_passed = _passed_specs_for_phase(results, phase="board")  # 已具备真实上板成功证据的样例集合

    # 任一必验样例缺少 Vitis 或 board 证据，都需要进入缺口清单。
    list_missing_specs = [  # tier1 覆盖缺失样例
        str_spec_name  # 当前仍缺少完整 tier1 双阶段证据的样例名
        for str_spec_name in list_required_specs  # tier1 规定必须覆盖的样例
        if str_spec_name not in set_vitis_passed or str_spec_name not in set_board_passed  # 缺少任一阶段证据都算覆盖缺口
    ]

    # 返回 tier1 覆盖率门禁结果。
    return {
        "status": PASS_STATUS if not list_missing_specs else "failed",
        "mode": str(dict_board_matrix.get("mode") or coverage_mode),
        "required_specs": list_required_specs,
        "vitis_passed_specs": sorted(set_vitis_passed),
        "board_passed_specs": sorted(set_board_passed),
        "missing_specs": list_missing_specs,
    }
