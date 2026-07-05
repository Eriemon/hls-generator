"""生成和审查参考向量 JSON 的语义契约摘要。"""

# 启用延迟注解，避免运行期解析泛型类型。
from __future__ import annotations

# 导入哈希、JSON、路径和通用载荷类型。
import hashlib
import json
from pathlib import Path
from typing import Any

# HLS testbench 中用于绑定参考向量契约的 hash 标签。
VECTOR_HASH_TAG = "HLS-GEN-VECTORS-SHA256:"  # 向量契约 hash 标签

# 参考向量文件约定以 vectors.json 结尾，递归扫描由 Path.rglob 承担。
VECTOR_FILE_PATTERN = "*vectors.json"  # 参考向量文件名匹配模式

# audit_vectors 是文件级参考向量审查入口。
def audit_vectors(path_vectors: Path) -> dict[str, Any]:
    """读取 vectors JSON 文件并生成语义契约。

    Args:
        path_vectors: 参考向量 JSON 文件路径。

    Returns:
        包含 sha256、case_count、case_ids 和输入输出 key 的契约字典。
    """

    # 读取 JSON 载荷，允许顶层 list 或包含 cases/vectors 的对象。
    obj_payload = json.loads(path_vectors.read_text(encoding="utf-8"))  # 参考向量文件的 JSON 载荷

    # source 字段记录原始文件路径，便于后续验证定位。
    return vector_contract_from_payload(obj_payload, source=str(path_vectors))

# vector_contract_from_payload 将任意 JSON 载荷规范化为契约摘要。
def vector_contract_from_payload(
    obj_payload: Any,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """从参考向量载荷生成稳定契约。

    Args:
        obj_payload: 顶层 list，或包含 cases/vectors 字段的 JSON 对象。
        source: 可选来源路径，会写入返回契约。

    Returns:
        包含 canonical_json 和 sha256 的参考向量契约字典。
    """

    # 提取 case 列表，统一处理 cases、vectors 和顶层 list 三种形态。
    list_cases = _cases_from_payload(obj_payload)  # 原始 case 列表

    # 每个 case 先递归规范化，再按稳定 key 排序。
    list_normalized_cases = sorted(  # 规范化后按稳定键排序的 case 列表
        (_normalize_json(obj_case) for obj_case in list_cases),  # 逐个规范化原始 case
        key=_case_sort_key,  # 使用稳定排序键保证契约顺序一致
    )

    # canonical JSON 只包含 cases，避免来源路径影响 hash。
    dict_canonical = {"cases": list_normalized_cases}  # 规范化契约载荷

    # 紧凑 JSON 作为 SHA256 输入，确保跨平台稳定。
    str_canonical_json = json.dumps(  # 参与 SHA256 计算的规范化 JSON 文本
        dict_canonical,  # 只保留规范化后的 cases 载荷
        ensure_ascii=False,  # 保留中文字符原样写入 canonical JSON
        sort_keys=True,  # 字典键排序后再序列化，保证 hash 稳定
        separators=(",", ":"),  # 使用紧凑分隔符，避免空白差异影响 hash
    )

    # case id 优先使用显式 id/name，缺失时按排序后序号兜底。
    list_case_ids = [  # 规范化后按稳定顺序生成的 case 标识列表
        _case_id(obj_case, int_index)  # 当前 case 生成的稳定契约标识
        for int_index, obj_case in enumerate(list_normalized_cases, start=1)  # 带一基序号遍历规范化 case
    ]

    # 契约字典保留历史字段，供 prompt、validation 和 workflow 复用。
    dict_contract = {  # 供 prompt、validation 和 workflow 复用的向量契约字典
        "version": 1,  # 参考向量契约格式版本
        "sha256": hashlib.sha256(str_canonical_json.encode("utf-8")).hexdigest(),  # canonical JSON 的稳定哈希
        "case_count": len(list_normalized_cases),  # 规范化后 case 总数
        "case_ids": list_case_ids,  # 供提示词和验证引用的 case 标识列表
        "input_keys": _keys_for(list_normalized_cases, ("inputs", "input")),  # 所有输入字段 key 的去重汇总
        "output_keys": _keys_for(list_normalized_cases, ("outputs", "expected", "output")),  # 所有输出字段 key 的去重汇总
        "canonical_json": str_canonical_json,  # 参与哈希计算并可落盘复核的规范化文本
    }  # 参考向量契约

    # source 只在调用方提供时写入，避免影响 canonical hash。
    if source:

        # source 帮助报告定位原始 vectors 文件。
        dict_contract["source"] = source  # 参考向量来源路径

    # 返回完整参考向量契约。
    return dict_contract

# find_vector_contracts 在产物目录中扫描所有 vectors.json。
def find_vector_contracts(path_root: Path) -> list[dict[str, Any]]:
    """扫描目录下所有参考向量文件并返回契约列表。

    Args:
        path_root: 需要递归扫描的运行产物根目录。

    Returns:
        成功解析的参考向量契约列表；坏文件会被跳过。
    """

    # 契约列表按文件路径排序后的扫描顺序追加。
    list_contracts: list[dict[str, Any]] = []  # 参考向量契约列表

    # 只扫描命名为 *vectors.json 的参考向量文件。
    for path_vectors in sorted(path_root.rglob(VECTOR_FILE_PATTERN)):

        # 单个坏文件不应中断整轮产物验证。
        try:

            # 审查单个 vectors JSON 并计算契约 hash。
            dict_contract = audit_vectors(path_vectors)  # 单文件向量契约

        # vectors 文件可能来自模型输出，坏 JSON 交给其他验证项报告。
        except Exception:

            # 坏 JSON 由其它验证项单独报告，这一轮扫描先跳过当前文件。
            continue

        # path 字段使用相对路径，保持报告可迁移。
        dict_contract["path"] = path_vectors.relative_to(path_root).as_posix()  # 契约文件相对路径

        # 已解析契约加入返回列表。
        list_contracts.append(dict_contract)

    # 返回所有成功解析的参考向量契约。
    return list_contracts

# extract_vector_hashes 从 HLS 文本中提取契约 hash。
def extract_vector_hashes(str_text: str) -> list[str]:
    """提取文本中出现的参考向量契约 hash。

    Args:
        str_text: HLS 源码或 testbench 文本。

    Returns:
        按首次出现顺序去重后的 hash 字符串列表。
    """

    # list_hashes 保留首次出现顺序，便于和报告上下文对齐。
    list_hashes: list[str] = []  # 向量契约 hash 列表

    # 逐行查找 hash 标签，避免跨行误匹配。
    for str_line in str_text.splitlines():

        # 没有标签的行不参与 hash 提取。
        if VECTOR_HASH_TAG not in str_line:

            # 非标签行不可能携带向量契约 hash。
            continue

        # 标签后第一个 token 是契约 hash。
        str_hash_token = str_line.split(VECTOR_HASH_TAG, 1)[1].strip().split()[0]  # 提取到的 hash token

        # 只记录非空且未出现过的 hash。
        if str_hash_token and str_hash_token not in list_hashes:

            # 追加新 hash，保持源码出现顺序。
            list_hashes.append(str_hash_token)

    # 返回去重后的 hash 列表。
    return list_hashes

# _cases_from_payload 统一支持顶层 list 和对象内 cases/vectors。
def _cases_from_payload(obj_payload: Any) -> list[Any]:
    """从参考向量载荷中提取 case 列表。

    Args:
        obj_payload: 顶层 list，或包含 cases/vectors 字段的 JSON 对象。

    Returns:
        原始 case 列表。

    Raises:
        ValueError: 载荷没有提供 list 形式的参考向量时抛出。
    """

    # dict 载荷优先读取 cases，兼容旧字段 vectors。
    if isinstance(obj_payload, dict):

        # cases 是当前推荐字段，vectors 是历史兼容字段。
        list_raw_cases = obj_payload.get("cases", obj_payload.get("vectors", []))  # 候选 case 列表

    # 非 dict 载荷直接作为候选 case 列表。
    else:

        # 顶层 list 是最小参考向量 JSON 形态。
        list_raw_cases = obj_payload  # 顶层候选 case 列表

    # 参考向量必须最终是 list。
    if not isinstance(list_raw_cases, list):

        # 非列表载荷无法生成稳定 case 契约。
        raise ValueError(
            "> ERR: [Python] Reference vectors must be a JSON list or an object with a cases list.",
        )

    # 返回原始 case 列表，后续步骤负责规范化。
    return list_raw_cases

# _normalize_json 递归排序 JSON 对象键，保证 hash 稳定。
def _normalize_json(obj_value: Any) -> Any:
    """递归规范化 JSON 值。

    Args:
        obj_value: 任意 JSON 兼容值。

    Returns:
        字典键已排序、列表元素已递归规范化的 JSON 值。
    """

    # 字典按 key 排序，并递归规范化每个字段值。
    if isinstance(obj_value, dict):

        # 排序后的 dict 让 JSON 序列化结果稳定。
        return {
            str(obj_key): _normalize_json(obj_value[obj_key])
            for obj_key in sorted(obj_value)
        }

    # 列表保持顺序，但递归规范化内部元素。
    if isinstance(obj_value, list):

        # 列表元素可能继续包含嵌套 dict 或 list。
        return [_normalize_json(obj_item) for obj_item in obj_value]

    # 标量 JSON 值不需要规范化。
    return obj_value

# _case_sort_key 为 case 排序提供稳定 key。
def _case_sort_key(obj_case: Any) -> str:
    """生成参考向量 case 的排序键。

    Args:
        obj_case: 单个规范化后的 case 值。

    Returns:
        优先来自 id/name 的排序字符串，缺失时使用 JSON 文本。
    """

    # dict case 优先使用人工定义的 id 或 name。
    if isinstance(obj_case, dict):

        # id/name 缺失时退回完整 JSON，保证排序仍稳定。
        return str(
            obj_case.get("id")
            or obj_case.get("name")
            or json.dumps(obj_case, sort_keys=True, ensure_ascii=False),
        )

    # 非 dict case 使用其 JSON 文本参与排序。
    return json.dumps(obj_case, sort_keys=True, ensure_ascii=False)

# _case_id 为每个 case 生成契约中可读的标识。
def _case_id(obj_case: Any, int_index: int) -> str:
    """生成参考向量 case 的契约标识。

    Args:
        obj_case: 单个规范化后的 case 值。
        int_index: 该 case 在排序后列表中的一基序号。

    Returns:
        case id、case name 或 `case_<index>` 兜底标识。
    """

    # dict case 优先沿用显式 id 或 name。
    if isinstance(obj_case, dict):

        # 缺少显式名称时按排序后序号生成稳定 id。
        return str(obj_case.get("id") or obj_case.get("name") or f"case_{int_index}")

    # 非 dict case 只能使用排序后序号生成 id。
    return f"case_{int_index}"

# _keys_for 汇总输入或输出字段中的业务 key。
def _keys_for(
    list_cases: list[Any],
    tuple_candidate_fields: tuple[str, ...],
) -> list[str]:
    """从 case 列表中收集输入或输出 key。

    Args:
        list_cases: 规范化后的参考向量 case 列表。
        tuple_candidate_fields: 需要检查的输入或输出字段名。

    Returns:
        按首次出现顺序去重后的 key 列表。
    """

    # list_keys 保留首次出现顺序，避免集合排序改变报告可读性。
    list_keys: list[str] = []  # 输入或输出 key 列表

    # 逐个 case 提取候选字段中的 key。
    for obj_case in list_cases:

        # 非对象 case 没有命名输入输出字段。
        if not isinstance(obj_case, dict):

            # 标量 case 不提供具名输入输出字段，直接跳过。
            continue

        # 从单个 case 中补充缺失的输入或输出 key。
        _extend_keys_from_case(
            list_keys,
            obj_case,
            tuple_candidate_fields,
        )

    # 返回按首次出现顺序去重后的 key。
    return list_keys

# _extend_keys_from_case 处理单个 case 的 key 提取。
def _extend_keys_from_case(
    list_keys: list[str],
    dict_case: dict[str, Any],
    tuple_candidate_fields: tuple[str, ...],
) -> None:
    """把单个 case 中的候选字段 key 追加到列表。

    Args:
        list_keys: 正在累计的输入或输出 key 列表。
        dict_case: 单个规范化后的 case 字典。
        tuple_candidate_fields: 需要检查的输入或输出字段名。

    Returns:
        该函数只修改 list_keys，不返回业务值。
    """

    # 按候选字段顺序检查，保持旧契约字段顺序。
    for str_field in tuple_candidate_fields:

        # field_payload 可能是输入输出字典，也可能是标量字段。
        obj_field_payload = dict_case.get(str_field)  # 候选字段载荷

        # dict 载荷中的每个 key 都代表一个命名输入或输出。
        if isinstance(obj_field_payload, dict):

            # 字典 key 按其原有顺序追加。
            _append_mapping_keys(list_keys, obj_field_payload)

        # 标量字段存在时，字段名本身就是一个输入或输出 key。
        elif str_field in dict_case and str_field not in list_keys:

            # 追加标量候选字段名，保持首次出现顺序。
            list_keys.append(str_field)

# _append_mapping_keys 将 dict 载荷内的 key 去重追加。
def _append_mapping_keys(
    list_keys: list[str],
    dict_payload: dict[Any, Any],
) -> None:
    """把字典载荷中的 key 按顺序追加到 key 列表。

    Args:
        list_keys: 正在累计的输入或输出 key 列表。
        dict_payload: inputs/outputs 等字段中的映射载荷。

    Returns:
        该函数只修改 list_keys，不返回业务值。
    """

    # 逐个读取原始 JSON key，转换为字符串后参与去重。
    for obj_key in dict_payload:

        # str_key 是契约报告中对外展示的字段名。
        str_key = str(obj_key)  # 输入输出契约字段名

        # 首次出现的 key 才追加，保持稳定去重语义。
        if str_key not in list_keys:

            # 追加该输入或输出 key。
            list_keys.append(str_key)
