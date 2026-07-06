"""从模型回复中提取 manifest、文件内容与 patch 内容。"""

# 标准库导入提供正则解析、JSON 解析与路径安全检查能力。
from __future__ import annotations

# json 负责解析模型回复中的 manifest 代码块。
import json

# re 用于扫描 fenced code block 与 patch marker 格式。
import re

# dataclass 让单个 fence 代码块的结构表达保持紧凑。
from dataclasses import dataclass

# Path 与纯路径类型共同承担输出路径安全校验。
from pathlib import Path, PurePosixPath, PureWindowsPath

# Any 与 cast 共同覆盖 manifest 中允许出现的混合 JSON 值类型。
from typing import Any, cast

# 分段保存 fence 正则片段，便于分别解释起始 info 行和正文闭合边界。
tuple_fence_pattern_parts = (
    r"^```(?P<info>[^\n`]*)\n",  # 匹配代码块起始三反引号与 info 行
    r"(?P<content>.*?)(?:\n)?^```[ \t]*$",  # 匹配正文并在结束三反引号处收束
)  # 组成完整 fence 扫描正则的片段元组

# 把两个正则片段拼成完整模式，供 FENCE_RE 编译成整段回复扫描器。
str_fence_pattern = "".join(tuple_fence_pattern_parts)  # FENCE_RE 使用的完整 fenced block 正则文本

# 预编译整段回复扫描器，供 manifest 和文件块共用。
FENCE_RE = re.compile(str_fence_pattern, re.MULTILINE | re.DOTALL)  # 统一扫描整段模型回复的 fenced block 正则对象

# 表示模型回复无法被安全提取成文件集合。
class ExtractionError(ValueError):
    """表示模型回复无法被安全提取成文件集合。"""

# 保存单个 fenced code block 的 info 字段与正文内容。
@dataclass(frozen=True)
class FencedBlock:
    """保存单个 fenced code block 的元信息与正文。"""

    # 保存 fence info 原文，后续会从中解析语言标签、path 与 patch marker。
    info: str  # 首个 token 提供语言标签，path=/patch= 参数也都从这段原文里解析

    # 保存 fence 正文，写盘与 patch 写回都直接复用这段文本。
    content: str  # extract_response 会把这段正文直接写入目标文件，patch 流程也会拿它替换 marker 区间

    # 提供便捷属性读取 fence info 中声明的 path 参数。
    @property

    # 通过 property 形式暴露 fence info 中声明的 path 参数。
    def path(self) -> str | None:
        """
        读取 fence info 中声明的相对路径。

        :param self: 当前 fenced code block 对象。
        :return: `path=` 对应的路径文本；未声明时返回 None。
        """

        # 复用统一的 info 解析逻辑抽取路径字段。
        str_path = path_from_info(self.info)  # fence info 中声明的相对路径

        # 返回给文件块分类逻辑继续判断。
        return str_path

# 解析模型回复中的所有 fenced code block。
def parse_fenced_blocks(text: str) -> list[FencedBlock]:
    """
    扫描整段模型回复并收集 fenced code block。

    :param text: 模型返回的完整文本。
    :return: 按回复出现顺序解析出的 fenced code block 列表。
    """

    # 将每个 regex 命中的 info 与正文收集为结构化对象。
    list_blocks = [
        FencedBlock(match.group("info").strip(), match.group("content"))  # 当前命中的 fence 元信息与正文对象
        for match in FENCE_RE.finditer(text)  # 按回复顺序遍历全部 fence 命中
    ]  # 回复中的 fenced code block 列表

    # 返回给 manifest 解析与文件提取流程复用。
    return list_blocks

# 从模型回复中提取 manifest JSON 对象。
def parse_manifest(text: str) -> dict[str, Any]:
    """
    在模型回复中查找首个合法的 manifest JSON 代码块。

    :param text: 模型返回的完整文本。
    :return: 包含 `files` 列表的 manifest 字典。
    异常:
        ExtractionError: 当回复中不存在合法 manifest 代码块时抛出。
    """

    # 逐个检查 fenced block，定位真正的 manifest JSON。
    for obj_block in parse_fenced_blocks(text):

        # 只让未声明 path/patch 的 json fence 参与 manifest 识别。
        str_language = _language_from_info(obj_block.info)  # 当前 fenced block 的语言标签

        # 非 json fence 一律跳过。
        if str_language != "json":

            # 继续检查后续 fenced block。
            continue

        # 带 path 或 patch 的 json fence 视为文件块而非 manifest。
        if obj_block.path is not None or patch_marker_from_info(obj_block.info) is not None:

            # 继续寻找真正的 manifest 代码块。
            continue

        # 尝试把当前 json fence 解析成 Python 对象。
        try:

            # 解析候选 manifest，后续再判断结构是否符合约束。
            raw_candidate_data = json.loads(obj_block.content)  # json fence 解析结果

        # 非法 JSON 不应阻断后续 fence 的继续识别。
        except json.JSONDecodeError:

            # 继续扫描后续 json fence。
            continue

        # 只有带 files 列表的对象才是受支持的 manifest。
        if isinstance(raw_candidate_data, dict) and isinstance(raw_candidate_data.get("files"), list):

            # 返回供提取流程继续校验 manifest 与文件块关系。
            return raw_candidate_data

    # 所有代码块都不满足 manifest 约束时阻断提取。
    raise ExtractionError(
        "> ERR: [Python] Response does not contain a JSON manifest with a files list."
    )

# 按 manifest 约束把模型回复落盘到目标目录。
def extract_response(
    text: str,
    out_dir: Path,
    *,
    expected_manifest: dict[str, Any] | None = None,
) -> list[Path]:
    """
    提取模型回复中的文件代码块并写入目标目录。

    :param text: 模型返回的完整文本。
    :param out_dir: 文件输出根目录。
    :param expected_manifest: 调用方预期的 manifest；为空时跳过比对。
    :return: 实际写入或打补丁触达的文件路径列表。
    异常:
        ExtractionError: 当回复违反 manifest 契约、缺少代码块或落盘路径不安全时抛出。
    """

    # 禁止 fence 外混入说明文字，避免把自由文本误当作安全输出。
    _reject_text_outside_fences(text)

    # 先解析 manifest，后续所有写盘动作都以它为准。
    dict_manifest = parse_manifest(text)  # 回复中声明的 manifest 对象

    # 调用方给了预期 manifest 时，需要先验证没有偷改文件集合。
    if expected_manifest is not None:

        # 阻断 manifest 被额外文件或 patch 篡改的情况。
        _validate_expected_manifest(expected_manifest, dict_manifest)

    # 收集全部 fenced block，供文件块与 patch 块分类使用。
    list_blocks = parse_fenced_blocks(text)  # 回复中的全部 fenced code block

    # 取得 manifest 中声明的普通文件路径列表。
    list_manifest_paths = _manifest_paths(dict_manifest)  # manifest 声明的输出文件路径

    # 取得 manifest 中声明的 patch 路径与 marker 列表。
    list_patch_entries = _manifest_patches(dict_manifest)  # manifest 声明的 patch 项

    # 统一按普通文件与 patch 文件两类建立索引，避免后续重复扫描 block 列表。
    tuple_classified_blocks = _classify_file_blocks(  # 普通文件索引与 patch 索引的组合结果
        list_blocks,  # 传入按回复顺序解析出的全部 fence
        list_manifest_paths,  # 传入 manifest 允许写出的普通文件路径
        list_patch_entries,  # 传入 manifest 声明的 patch 目标集合
    )  # 路径与 patch 标记到 fenced block 的索引

    # 解包普通文件代码块索引，供写盘阶段按路径定位正文。
    dict_blocks_by_path = tuple_classified_blocks[0]  # 普通文件路径到 fenced block 的映射

    # 解包 patch 代码块索引，供补丁写回阶段按 path+marker 定位正文。
    dict_patch_blocks_by_key = tuple_classified_blocks[1]  # patch 键到 fenced block 的映射

    # 记录本次真正落地或被 patch 触达的文件路径。
    list_written_paths: list[Path] = []  # 本次写盘产物路径集合

    # 先按 manifest 顺序写入所有普通文件。
    for str_rel_path in list_manifest_paths:

        # manifest 中的每个 path 都必须有唯一匹配的 fenced code block。
        obj_file_block: FencedBlock | None = dict_blocks_by_path.get(str_rel_path)  # 当前 manifest 文件路径对应的正文代码块对象

        # 缺失对应代码块时立即阻断写盘。
        if obj_file_block is None:

            # 保留既有报错关键词，方便 smoke 与单测继续匹配。
            raise ExtractionError(
                f"> ERR: [Python] Missing fenced code block for manifest path {str_rel_path!r}."
            )

        # 计算经过安全校验的最终输出路径。
        path_output = safe_output_path(out_dir, str_rel_path)  # 当前文件的安全输出路径

        # 确保父目录存在，再把提取内容写入目标文件。
        path_output.parent.mkdir(parents=True, exist_ok=True)

        # 统一补一个换行，保持与现有写盘行为一致。
        path_output.write_text(obj_file_block.content.rstrip() + "\n", encoding="utf-8")

        # 记录已经生成的文件路径。
        list_written_paths.append(path_output)

    # 再处理 manifest 声明的 patch 项，确保它们作用在已存在文件上。
    for dict_patch in list_patch_entries:

        # 组合 path 与 marker 作为 patch block 的唯一索引键。
        tuple_patch_key = (
            dict_patch["path"],  # 当前 patch 目标路径
            dict_patch["marker"],  # 当前 patch 边界标记
        )  # 当前 patch 项的唯一索引键

        # 先确认当前 patch 键已经在 fence 索引里登记过正文。
        if tuple_patch_key not in dict_patch_blocks_by_key:

            # 保留原有 path/marker 关键词，避免上层测试误判。
            raise ExtractionError(
                "> ERR: [Python] Missing fenced patch block for manifest patch "
                f"path {dict_patch['path']!r} marker {dict_patch['marker']!r}."
            )

        # 取出已经通过键存在性校验的 patch fenced block。
        fenced_block_for_patch: FencedBlock = dict_patch_blocks_by_key[tuple_patch_key]  # 当前 patch 键唯一命中的 fenced block 正文对象

        # 计算 patch 目标文件的安全路径。
        path_output = safe_output_path(out_dir, dict_patch["path"])  # patch 目标文件路径

        # 在 marker 区间内写回 patch 内容。
        _apply_patch_block(path_output, dict_patch["marker"], fenced_block_for_patch.content)

        # patch 命中的现有文件也要体现在返回值里。
        if path_output not in list_written_paths:

            # 记录 patch 触达的文件路径。
            list_written_paths.append(path_output)

    # 返回给工作流，用于后续验证与审计。
    return list_written_paths

# 校验回复 manifest 与调用方预期 manifest 是否保持同一文件契约。
def _validate_expected_manifest(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> None:
    """
    比对预期 manifest 与观察到的 manifest 是否一致。

    :param expected: 调用方声明的预期 manifest。
    :param observed: 模型回复中解析出的 manifest。
    :return: 无业务返回值；检测到契约漂移时抛出异常。
    """

    # 先校验普通文件集合与关键元数据保持一致。
    _validate_expected_manifest_files(expected, observed)

    # 再校验 patch 项集合没有新增或缺失。
    _validate_expected_manifest_patches(expected, observed)

# 对比普通文件条目是否保持同一 manifest 契约。
def _validate_expected_manifest_files(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> None:
    """
    验证普通文件路径集合及关键字段未被模型回复篡改。

    :param expected: 调用方声明的预期 manifest。
    :param observed: 模型回复中解析出的 manifest。
    :return: 无业务返回值；检测到文件集合或元数据漂移时抛出异常。
    """

    # 先把预期 manifest 转成按路径查找的索引，便于作为契约基线使用。
    dict_expected_files = _manifest_file_index(expected)  # 预期 manifest 的文件索引

    # 再把回复里的 manifest 转成对应索引，用于逐路径比较模型输出。
    dict_observed_files = _manifest_file_index(observed)  # 回复 manifest 的文件索引

    # 汇总预期文件路径集合，作为“应该出现”的目标集合。
    set_expected_paths = set(dict_expected_files)  # 预期的 manifest 文件路径集合

    # 汇总实际回复路径集合，作为“模型实际给出”的对照集合。
    set_observed_paths = set(dict_observed_files)  # 实际回复的 manifest 文件路径集合

    # 预期存在但回复缺失的路径必须立即阻断。
    for str_rel_path in sorted(set_expected_paths - set_observed_paths):

        # 保留既有关键词，方便测试继续按子串断言。
        raise ExtractionError(
            f"> ERR: [Python] missing manifest path {str_rel_path!r}."
        )

    # 回复额外声明的新路径也必须阻断。
    for str_rel_path in sorted(set_observed_paths - set_expected_paths):

        # 禁止模型偷偷扩展 manifest 文件集合。
        raise ExtractionError(
            f"> ERR: [Python] unexpected manifest path {str_rel_path!r}."
        )

    # 对交集路径逐项检查关键字段没有被改写。
    for str_rel_path in sorted(set_expected_paths & set_observed_paths):

        # 当前实现只锁定 kind 与 language 两个关键元数据字段。
        for str_key in ("kind", "language"):

            # 读取预期 manifest 中该字段的值，并统一转成字符串比较。
            str_expected_value = str(  # 预期 manifest 中当前字段的字符串化结果
                dict_expected_files[str_rel_path].get(str_key) or ""  # 当前路径在预期 manifest 中的字段原值
            )  # 预期的关键字段值

            # 读取回复 manifest 中同一字段的值，和预期值逐项做对照。
            str_observed_value = str(  # 回复 manifest 中当前字段的字符串化结果
                dict_observed_files[str_rel_path].get(str_key) or ""  # 当前路径在回复 manifest 中的字段原值
            )  # 实际回复的关键字段值

            # 任一关键字段变化都视为 manifest 契约漂移。
            if str_expected_value != str_observed_value:

                # 报告具体字段差异，便于上层直接定位模型偏航点。
                raise ExtractionError(
                    "> ERR: [Python] manifest path "
                    f"{str_rel_path!r} changed {str_key}: expected "
                    f"{str_expected_value!r}, got {str_observed_value!r}."
                )

# 对比 patch 条目集合是否保持同一 manifest 契约。
def _validate_expected_manifest_patches(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> None:
    """
    验证 patch 路径与 marker 集合未被模型回复篡改。

    :param expected: 调用方声明的预期 manifest。
    :param observed: 模型回复中解析出的 manifest。
    :return: 无业务返回值；检测到 patch 集合漂移时抛出异常。
    """

    # 收集 expected manifest 声明的全部 patch 键。
    set_expected_patch_keys = _manifest_patch_keys(expected)  # 预期的 patch 键集合

    # 收集回复里实际声明的 patch 键集合，供后续和预期契约做差分。
    set_observed_patch_keys = _manifest_patch_keys(observed)  # 实际回复的 patch 键集合

    # 预期存在但回复缺失的 patch 需要立即阻断。
    for tuple_patch_key in sorted(set_expected_patch_keys - set_observed_patch_keys):

        # 保留原有 path/marker 错误关键词，方便现有调用方继续匹配。
        raise ExtractionError(
            "> ERR: [Python] missing manifest patch "
            f"path {tuple_patch_key[0]!r} marker {tuple_patch_key[1]!r}."
        )

    # 回复额外声明的新 patch 也必须阻断。
    for tuple_patch_key in sorted(set_observed_patch_keys - set_expected_patch_keys):

        # 禁止模型偷偷追加 patch 操作集合。
        raise ExtractionError(
            "> ERR: [Python] unexpected manifest patch "
            f"path {tuple_patch_key[0]!r} marker {tuple_patch_key[1]!r}."
        )

# 把 manifest 的 files 列表转成按路径索引的字典。
def _manifest_file_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    基于 manifest 的 `files` 列表构造路径到条目的索引。

    :param manifest: 已解析的 manifest 对象。
    :return: 以规范化相对路径为键、文件条目字典为值的索引。
    异常:
        ExtractionError: 当 manifest 文件路径无法完整建立索引时抛出。
    """

    # 先拿到 manifest 中声明的合法文件路径顺序。
    list_manifest_paths = _manifest_paths(manifest)  # manifest 声明的文件路径列表

    # 准备累计路径到原始文件条目的映射。
    dict_file_index: dict[str, dict[str, Any]] = {}  # 文件路径到 manifest 条目的映射

    # 逐个登记 manifest 中的文件条目。
    for obj_entry in manifest["files"]:

        # 只让真正带 path 的字典条目进入索引。
        if isinstance(obj_entry, dict) and obj_entry.get("path"):

            # 统一把 path 规范化后作为索引键。
            str_rel_path = normalize_manifest_path(str(obj_entry["path"]))  # 规范化后的文件路径

            # 保存原始 manifest 条目，便于上层继续读取其他字段。
            dict_file_index[str_rel_path] = obj_entry  # 规范化路径到 manifest 原始条目的索引关系

    # 反向核对一次，确保所有合法路径都成功进入索引。
    list_missing_paths = [str_path for str_path in list_manifest_paths if str_path not in dict_file_index]  # 没有进入索引的 manifest 路径

    # 若有缺失路径，说明 files 列表内部结构不一致。
    if list_missing_paths:

        # 汇总缺失路径，便于定位损坏的 manifest 条目。
        raise ExtractionError(
            "> ERR: [Python] Manifest file index is missing paths: "
            f"{', '.join(list_missing_paths)}."
        )

    # 返回给 manifest 契约校验逻辑复用。
    return dict_file_index

# 把 manifest 的 patch 列表转成 path+marker 键集合。
def _manifest_patch_keys(manifest: dict[str, Any]) -> set[tuple[str, str]]:
    """
    收集 manifest 中全部 patch 条目的唯一键。

    :param manifest: 已解析的 manifest 对象。
    :return: 由 `(path, marker)` 组成的 patch 键集合。
    """

    # 用集合收集规范化后的 patch 唯一键，便于后续做集合差分。
    set_patch_keys = {
        (dict_patch["path"], dict_patch["marker"])  # 当前 patch 的 path+marker 唯一键
        for dict_patch in _manifest_patches(manifest)  # 遍历已经规范化的 patch 条目
    }  # manifest 声明的 patch 键集合

    # 把 patch 键集合交给 manifest patch 契约校验逻辑复用。
    return set_patch_keys

# 读取 manifest 中声明的普通文件路径列表。
def _manifest_paths(manifest: dict[str, Any]) -> list[str]:
    """
    提取 manifest `files` 列表中的规范化相对路径。

    :param manifest: 已解析的 manifest 对象。
    :return: 去重且保持原顺序的规范化文件路径列表。
    异常:
        ExtractionError: 当文件条目缺少 path 或路径重复时抛出。
    """

    # 记录已经出现过的路径，防止 manifest 重复声明同一文件。
    set_seen_paths: set[str] = set()  # 已登记的 manifest 路径集合

    # 保留 manifest 原始顺序，供写盘阶段按声明顺序输出。
    list_paths: list[str] = []  # 按 manifest 顺序收集的文件路径

    # 逐个检查 files 条目是否合法。
    for obj_file_entry in manifest["files"]:

        # 每个文件条目都必须是带 path 的字典。
        if not isinstance(obj_file_entry, dict) or not obj_file_entry.get("path"):

            # 阻断缺失 path 的非法 manifest 条目。
            raise ExtractionError(
                "> ERR: [Python] Every manifest file entry must contain a path."
            )

        # 规范化当前条目的相对路径文本。
        str_rel_path = normalize_manifest_path(str(obj_file_entry["path"]))  # 当前文件条目的规范化路径

        # 重复声明同一路径会导致写盘结果不确定，必须阻断。
        if str_rel_path in set_seen_paths:

            # 报告重复路径，便于定位 manifest 生成错误。
            raise ExtractionError(
                f"> ERR: [Python] Duplicate manifest path {str_rel_path!r}."
            )

        # 标记当前路径已经出现，供后续去重判断使用。
        set_seen_paths.add(str_rel_path)

        # 保留 manifest 原始顺序写盘。
        list_paths.append(str_rel_path)

    # 返回给写盘流程与契约校验流程复用。
    return list_paths

# 读取 manifest 中声明的 patch 列表并完成规范化。
def _manifest_patches(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """
    提取 manifest `patches` 列表中的 path/marker 条目。

    :param manifest: 已解析的 manifest 对象。
    :return: 规范化后的 patch 字典列表。
    异常:
        ExtractionError: 当 patch 条目缺字段、重复或类型不合法时抛出。
    """

    # 用于检查 `(path, marker)` 是否在 manifest 中重复出现。
    set_seen_patch_keys: set[tuple[str, str]] = set()  # 已登记的 patch 键集合

    # 保留 manifest 中 patch 条目的原始顺序。
    list_patches: list[dict[str, str]] = []  # 规范化后的 patch 条目列表

    # 调用方未声明 patches 字段时，直接按本轮没有 patch 操作处理。
    if "patches" not in manifest:

        # 返回空列表，表示本轮无需执行任何 patch。
        return list_patches

    # 显式写成 null 的情况视为没有 patch。
    if manifest["patches"] is None:

        # 显式 null 同样表示不需要 patch。
        return list_patches

    # 只有列表类型才符合 patch 字段契约。
    if not isinstance(manifest["patches"], list):

        # 阻断错误的 patch 字段类型。
        raise ExtractionError(
            "> ERR: [Python] Manifest patches must be a list when present."
        )

    # 经过类型校验后，把 patch 字段视为可安全遍历的原始条目列表。
    list_raw_patches = cast(list[Any], manifest["patches"])  # 已确认类型安全的 patch 条目列表

    # 逐个规范化 patch 条目。
    for obj_patch in list_raw_patches:

        # 每个 patch 都必须同时声明 path 与 marker。
        if (
            not isinstance(obj_patch, dict)
            or not obj_patch.get("path")
            or not obj_patch.get("marker")
        ):

            # 阻断缺失关键字段的 patch 条目。
            raise ExtractionError(
                "> ERR: [Python] Every manifest patch entry must contain path and marker."
            )

        # 按普通文件同一规则规范化 patch 目标路径，避免 path 合同分叉。
        str_rel_path = normalize_manifest_path(str(obj_patch["path"]))  # 当前 patch 条目写回的规范化目标路径

        # 单独规范化 patch marker，确保替换边界只使用受控字符集。
        str_marker = normalize_patch_marker(str(obj_patch["marker"]))  # 规范化后的 patch marker

        # 组合 path 与 marker 形成唯一键。
        tuple_patch_key = (
            str_rel_path,  # 规范化后的 patch 目标路径
            str_marker,  # 规范化后的 patch 边界标记
        )  # 当前 patch 条目的唯一索引键

        # 重复的 `(path, marker)` 会导致 patch 写回结果不确定。
        if tuple_patch_key in set_seen_patch_keys:

            # 报告重复 patch 键，阻断继续写盘。
            raise ExtractionError(
                "> ERR: [Python] Duplicate manifest patch "
                f"path {str_rel_path!r} marker {str_marker!r}."
            )

        # 标记当前 patch 键已经被占用。
        set_seen_patch_keys.add(tuple_patch_key)

        # 记录规范化后的 patch 条目。
        list_patches.append({"path": str_rel_path, "marker": str_marker})

    # 返回给 patch 写回与契约比对流程复用。
    return list_patches

# 判断当前 fenced block 是否就是 manifest 自身的 json 代码块。
def _is_manifest_json_block(block: FencedBlock) -> bool:
    """
    判断 fenced code block 是否表示 manifest 本体。

    :param block: 当前待分类的 fenced code block。
    :return: 若该代码块是 manifest 自身则返回 True，否则返回 False。
    """

    # 抽取当前 fenced block 声明的语言标签，用于判断是否可能是 manifest 自身。
    str_language = _language_from_info(block.info)  # 当前 fenced block 用于 manifest 判定的语言标签

    # 判断当前 block 是否满足 manifest 自身的判定条件。
    return (
        str_language == "json"
        and not block.path
        and not patch_marker_from_info(block.info)
    )

# 规范化 fenced block 的目标路径与 patch marker。
def _normalized_block_target(block: FencedBlock) -> tuple[str, str]:
    """
    提取并规范化 fenced code block 的输出目标。

    :param block: 当前待分类的 fenced code block。
    :return: 由规范化相对路径与 patch marker 文本组成的二元组。
    异常:
        ExtractionError: 当代码块缺少 path 或 path/patch 非法时抛出。
    """

    # 普通文件与 patch 文件都需要 path 信息定位目标文件。
    str_block_path = block.path or ""  # fence info 中声明的目标路径

    # 缺少 path 的文件型代码块无法安全落盘。
    if not str_block_path:

        # 报告缺失 path 的 fence info 原文，便于快速定位问题回复。
        raise ExtractionError(
            "> ERR: [Python] File code block is missing a path=<relative/path> "
            f"fence info: {block.info!r}."
        )

    # 规范化 fence 中声明的目标路径。
    str_rel_path = normalize_manifest_path(str_block_path)  # 当前 fenced block 的规范化路径

    # 规范化可选的 patch marker，供后续区分 patch 与普通文件分支。
    str_patch_marker = patch_marker_from_info(block.info) or ""  # 当前 fenced block 规范化前的 patch marker 文本

    # 返回给分类流程继续判断该 block 的归属分支。
    return str_rel_path, str_patch_marker

# 把 patch fenced block 登记到 path+marker 索引。
def _register_patch_block(
    block: FencedBlock,
    rel_path: str,
    patch_marker: str,
    patch_keys: set[tuple[str, str]],
    patch_blocks_by_key: dict[tuple[str, str], FencedBlock],
) -> None:
    """
    校验并登记单个 patch fenced code block。

    :param block: 当前待登记的 patch fenced code block。
    :param rel_path: 当前 block 对应的规范化目标路径。
    :param patch_marker: 当前 block 声明的 patch marker。
    :param patch_keys: manifest 声明允许出现的 patch 键集合。
    :param patch_blocks_by_key: 已登记 patch 代码块的索引。
    :return: 无业务返回值；登记结果会直接写入 patch 索引。
    异常:
        ExtractionError: 当 patch 重复、未声明或 marker 非法时抛出。
    """

    # 组合规范化后的 patch 唯一键。
    tuple_patch_key = (
        rel_path,  # 当前 patch fence 准备写回的目标路径
        normalize_patch_marker(patch_marker),  # 当前 patch fence 的规范化 marker
    )  # 当前 patch block 的唯一索引键

    # 重复 patch fence 会导致写回结果不确定，必须阻断。
    if tuple_patch_key in patch_blocks_by_key:

        # 报告重复的 path 与 marker，便于回复方修正。
        raise ExtractionError(
            "> ERR: [Python] Duplicate code fence patch "
            f"path {rel_path!r} marker {tuple_patch_key[1]!r}."
        )

    # manifest 未声明的 patch fence 不允许直接落盘。
    if tuple_patch_key not in patch_keys:

        # 阻断未声明 patch，避免模型偷偷修改额外片段。
        raise ExtractionError(
            "> ERR: [Python] Code fence patch "
            f"path {rel_path!r} marker {tuple_patch_key[1]!r} "
            "is not declared in manifest."
        )

    # 记录 patch 键到 fenced block 的映射。
    patch_blocks_by_key[tuple_patch_key] = block  # patch 键到 fenced block 正文对象的映射关系

# 把普通文件 fenced block 登记到路径索引。
def _register_regular_file_block(
    block: FencedBlock,
    rel_path: str,
    manifest_paths: set[str],
    blocks_by_path: dict[str, FencedBlock],
) -> None:
    """
    校验并登记单个普通文件 fenced code block。

    :param block: 当前待登记的普通文件 fenced code block。
    :param rel_path: 当前 block 对应的规范化目标路径。
    :param manifest_paths: manifest 声明允许出现的普通文件路径集合。
    :param blocks_by_path: 已登记普通文件代码块的索引。
    :return: 无业务返回值；登记结果会直接写入路径索引。
    异常:
        ExtractionError: 当普通文件路径重复或未在 manifest 中声明时抛出。
    """

    # 普通文件路径不得在回复中重复出现。
    if rel_path in blocks_by_path:

        # 报告重复的普通文件路径，防止后写覆盖前写。
        raise ExtractionError(
            f"> ERR: [Python] Duplicate code fence path {rel_path!r}."
        )

    # 普通文件 fence 也必须先在 manifest 中显式声明。
    if rel_path not in manifest_paths:

        # 阻断 manifest 外的额外文件输出。
        raise ExtractionError(
            f"> ERR: [Python] Code fence path {rel_path!r} is not declared in manifest."
        )

    # 记录普通文件路径到 fenced block 的映射。
    blocks_by_path[rel_path] = block  # 普通文件路径到 fenced block 正文对象的映射关系

# 按 manifest 把 fenced block 分类成普通文件块和 patch 文件块。
def _classify_file_blocks(
    blocks: list[FencedBlock],
    manifest_paths: list[str],
    patch_entries: list[dict[str, str]],
) -> tuple[dict[str, FencedBlock], dict[tuple[str, str], FencedBlock]]:
    """
    把 fenced code block 建立成普通文件与 patch 文件索引。

    :param blocks: 回复中解析出的全部 fenced code block。
    :param manifest_paths: manifest 声明的普通文件路径列表。
    :param patch_entries: manifest 声明的 patch 条目列表。
    :return: 普通文件路径映射与 patch 键映射组成的二元组。
    异常:
        ExtractionError: 当代码块缺少路径、重复声明或越出 manifest 契约时抛出。
    """

    # 先把普通文件路径转成集合，便于快速判定 fence path 是否合法。
    set_manifest_paths = set(manifest_paths)  # manifest 声明的普通文件路径集合

    # 把 patch 条目折叠成允许命中的键集合，供 fence 分类时快速判断合法性。
    set_patch_keys = {
        (dict_patch["path"], dict_patch["marker"])  # manifest 允许命中的 patch 键
        for dict_patch in patch_entries  # 遍历 manifest 声明的全部 patch 条目
    }  # fence 分类阶段允许命中的 patch 键集合

    # 保存普通文件路径到 fenced block 的唯一映射。
    dict_blocks_by_path: dict[str, FencedBlock] = {}  # 普通文件路径索引

    # 保存 patch 键到 fenced block 的唯一映射。
    dict_patch_blocks_by_key: dict[tuple[str, str], FencedBlock] = {}  # `(path, marker)` 到 patch 正文代码块对象的索引

    # 逐个处理模型回复中的 fenced block。
    for obj_block in blocks:

        # manifest 自身的 json fence 不参与普通文件/patch 分类。
        if _is_manifest_json_block(obj_block):

            # 跳过 manifest 代码块，继续处理下一个 fence。
            continue

        # 先取回当前 block 规范化后的目标信息元组。
        tuple_block_target = _normalized_block_target(obj_block)  # 当前 fenced block 的规范化目标信息元组

        # 读取当前 block 对应的规范化相对路径。
        str_rel_path = tuple_block_target[0]  # 当前 fenced block 的规范化目标路径

        # 读取当前 block 对应的 patch marker。
        str_patch_marker = tuple_block_target[1]  # 当前 fenced block 的 patch marker 文本

        # 带 patch marker 的代码块走 patch 分类分支。
        if str_patch_marker:

            # 把 patch block 交给专门 helper 校验并登记。
            _register_patch_block(
                obj_block,  # 当前 patch fenced block
                str_rel_path,
                str_patch_marker,
                set_patch_keys,  # manifest 允许的 patch 键集合
                dict_patch_blocks_by_key,  # patch 键到 fenced block 的索引
            )

            # 当前 block 已归入 patch 分支，无需再走普通文件逻辑。
            continue

        # 把普通文件 block 交给专门 helper 校验并登记。
        _register_regular_file_block(
            obj_block,  # 当前普通文件 fenced block
            str_rel_path,
            set_manifest_paths,  # manifest 允许的普通文件路径集合
            dict_blocks_by_path,  # 普通文件路径到 fenced block 的索引
        )

    # 返回给写盘阶段按路径与 patch 键取回正文内容。
    return dict_blocks_by_path, dict_patch_blocks_by_key

# 拒绝 fenced code block 之外的自由文本。
def _reject_text_outside_fences(text: str) -> None:
    """
    检查模型回复是否在 fenced code block 之外夹带说明文字。

    :param text: 模型返回的完整文本。
    :return: 无业务返回值；检测到 fence 外文本时抛出异常。
    """

    # 从回复起点开始累计尚未被 regex 命中的文本区间。
    int_cursor = 0  # 下一个未消费文本片段的起始下标

    # 收集所有 fence 外文本片段，最后统一判断是否为空白。
    list_outside_parts: list[str] = []  # fenced block 之外的文本片段

    # 逐个切分出每个 fence 之间的外部文本。
    for obj_match in FENCE_RE.finditer(text):

        # 保留当前 fence 之前的外部文本片段。
        list_outside_parts.append(text[int_cursor : obj_match.start()])

        # 推进游标到当前 fence 末尾。
        int_cursor = obj_match.end()  # 下一段 fence 外文本的起始游标

    # 追加最后一个 fence 之后的尾部文本。
    list_outside_parts.append(text[int_cursor:])

    # 合并并裁掉空白，得到真正有内容的 fence 外文本。
    str_outside_text = "".join(list_outside_parts).strip()  # 合并后的 fence 外文本

    # 只要仍有非空白内容，就说明回复掺杂了自由文本。
    if str_outside_text:

        # 只取第一行作为摘要，避免把整段自由文本塞进异常消息。
        str_first_line = str_outside_text.splitlines()[0].strip()  # 首行自由文本摘要

        # 阻断带说明文字的回复，避免提取器吞掉非协议内容。
        raise ExtractionError(
            "> ERR: [Python] Response contains prose outside fenced code blocks: "
            f"{str_first_line!r}."
        )

# 从 fence info 中读取 path 参数。
def path_from_info(info: str) -> str | None:
    """
    提取 fence info 中的 `path=` 参数值。

    :param info: fenced code block 的 info 字段原文。
    :return: `path=` 对应的值；未声明时返回 None。
    """

    # 委托通用 key=value 解析器读取 path 字段。
    str_path = _value_from_info(info, "path")  # fence info 中声明的 path 值

    # 返回给文件块分类逻辑继续使用。
    return str_path

# 提取 fence info 中声明的 patch marker 文本。
def patch_marker_from_info(info: str) -> str | None:
    """
    提取 fence info 中的 `patch=` 参数值。

    :param info: fenced code block 的 info 字段原文。
    :return: `patch=` 对应的值；未声明时返回 None。
    """

    # 通过通用 key=value 解析器单独提取 `patch=` 字段。
    str_patch_marker = _value_from_info(info, "patch")  # fence info 中声明的 patch 标记值

    # 把 patch 标记交还给 patch 分类逻辑决定是否走补丁分支。
    return str_patch_marker

# 从 fence info 中抽取语言标签。
def _language_from_info(info: str) -> str:
    """
    解析 fence info 中的首个语言标签。

    :param info: fenced code block 的 info 字段原文。
    :return: 归一化后的小写语言标签；缺失时返回空字符串。
    """

    # 空 info 直接按无语言标签处理。
    if not info:

        # 返回空字符串给调用方继续做默认分支判断。
        return ""

    # 语言标签始终位于 info 的首个空白分隔 token。
    str_language = info.split(maxsplit=1)[0].lower()  # 当前 fence 在 info 中声明的语言标签

    # 把语言标签交还给 manifest 识别与 fence 分类逻辑。
    return str_language

# 从 fence info 中读取指定 key 的值。
def _value_from_info(info: str, key: str) -> str | None:
    """
    从 fenced code block 的 info 字段中解析 `key=value` 片段。

    :param info: fenced code block 的 info 字段原文。
    :param key: 需要读取的参数名。
    :return: 指定参数对应的文本值；未命中时返回 None。
    """

    # 空 info 不可能包含任何键值对。
    if not info:

        # 返回 None 表示参数不存在。
        return None

    # 逐个 token 扫描形如 key=value 的字段。
    for str_token in info.split():

        # 只有前缀命中的 token 才属于目标参数。
        if str_token.startswith(f"{key}="):

            # 去掉 key= 前缀与包裹引号，返回参数正文。
            return str_token.split("=", 1)[1].strip("\"'")

    # 未找到目标参数时显式返回 None。
    return None

# 规范化并校验 manifest 中声明的相对路径。
def normalize_manifest_path(path: str | None) -> str:
    """
    校验 manifest 使用的相对路径文本是否合法。

    :param path: manifest 或 fence info 中声明的路径文本。
    :return: 去掉首尾空白后的规范化相对路径。
    异常:
        ExtractionError: 当路径缺失、为空或使用反斜杠时抛出。
    """

    # 路径字段缺失时无法继续做安全校验。
    if not path:

        # 阻断缺失路径的 manifest 或 fence 条目。
        raise ExtractionError("> ERR: [Python] Path is required.")

    # manifest 协议统一要求使用正斜杠分隔路径。
    if "\\" in path:

        # 阻断 Windows 反斜杠路径，避免跨平台解释差异。
        raise ExtractionError(
            f"> ERR: [Python] Path must use forward slashes, got {path!r}."
        )

    # 去掉路径文本首尾空白，避免空白干扰后续校验。
    str_stripped_path = path.strip()  # 去掉首尾空白后的路径文本

    # 去空白后仍为空字符串的路径不合法。
    if not str_stripped_path:

        # 阻断空路径，避免后续 path 拼接得到根目录。
        raise ExtractionError("> ERR: [Python] Path must not be empty.")

    # 返回给安全路径拼接与 manifest 索引逻辑复用。
    return str_stripped_path

# 规范化并校验 patch marker 文本。
def normalize_patch_marker(marker: str | None) -> str:
    """
    校验 patch marker 是否满足允许字符集约束。

    :param marker: manifest 或 fence info 中声明的 patch marker。
    :return: 去掉首尾空白后的规范化 patch marker。
    异常:
        ExtractionError: 当 marker 缺失或包含非法字符时抛出。
    """

    # marker 缺失时无法定位 patch 边界。
    if not marker:

        # 阻断缺失 marker 的 patch 条目。
        raise ExtractionError("> ERR: [Python] Patch marker is required.")

    # 去掉首尾空白，避免仅凭空白绕过规则。
    str_cleaned_marker = marker.strip()  # 去掉首尾空白后的 patch marker

    # marker 只允许字母、数字及少量稳定分隔符。
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", str_cleaned_marker):

        # 阻断不受支持的 marker 字符，避免影响文本替换边界。
        raise ExtractionError(
            "> ERR: [Python] Patch marker contains unsupported characters: "
            f"{marker!r}."
        )

    # 返回给 patch manifest 与 patch fence 分类逻辑复用。
    return str_cleaned_marker

# 计算目标文件在输出目录中的安全落盘路径。
def safe_output_path(out_dir: Path, relative_path: str) -> Path:
    """
    把 manifest 相对路径转换成输出目录内的安全绝对路径。

    :param out_dir: 文件输出根目录。
    :param relative_path: manifest 或 fence info 中声明的相对路径。
    :return: 经过边界校验后的绝对输出路径。
    异常:
        ExtractionError: 当目标路径绝对、越界或包含目录穿越片段时抛出。
    """

    # 先按 manifest 规则规范化相对路径文本。
    str_normalized_path = normalize_manifest_path(relative_path)  # 规范化后的相对路径文本

    # 用 POSIX 语义检查相对路径片段是否合法。
    path_posix = PurePosixPath(str_normalized_path)  # POSIX 语义下的相对路径对象

    # 额外按 Windows 语义检查盘符与绝对路径变体。
    path_windows = PureWindowsPath(str_normalized_path)  # 用于识别 drive 与绝对路径变体的 Windows 路径视图

    # 绝对路径或带盘符路径都不允许进入输出目录拼接。
    if path_posix.is_absolute() or path_windows.is_absolute() or path_windows.drive:

        # 阻断绝对路径，避免模型把文件写出目标目录。
        raise ExtractionError(
            f"> ERR: [Python] Refusing absolute output path {relative_path!r}."
        )

    # 空片段、当前目录与父目录片段都属于不安全路径。
    if any(str_part in ("", ".", "..") for str_part in path_posix.parts):

        # 阻断目录穿越与空片段路径。
        raise ExtractionError(
            f"> ERR: [Python] Refusing unsafe output path {relative_path!r}."
        )

    # 固化输出根目录的真实绝对路径，供后续边界比较使用。
    path_root = out_dir.resolve()  # 输出根目录的绝对路径

    # 把合法相对路径拼到输出根目录，并解析潜在符号链接。
    path_candidate = (path_root / Path(*path_posix.parts)).resolve()  # 候选输出文件绝对路径

    # 再次确认解析后的路径仍留在输出根目录内部。
    try:

        # 仅用于触发 `relative_to` 的边界检查副作用。
        path_candidate.relative_to(path_root)

    # relative_to 失败说明路径逃逸出了输出根目录。
    except ValueError as exc:

        # 保留原有 outside output directory 语义，便于上层定位问题。
        raise ExtractionError(
            "> ERR: [Python] Refusing path outside output directory: "
            f"{relative_path!r}."
        ) from exc

    # 把安全绝对路径交给写盘阶段或 patch 写回阶段继续使用。
    return path_candidate

# 在目标文件的指定 patch marker 区间内替换正文内容。
def _apply_patch_block(path: Path, marker: str, content: str) -> None:
    """
    把 patch 内容写回到目标文件的 marker 区间。

    :param path: patch 目标文件路径。
    :param marker: manifest 与 fence 共同声明的 patch marker。
    :param content: patch fenced code block 的正文内容。
    :return: 无业务返回值；目标文件会被原地改写。
    异常:
        ExtractionError: 当目标文件缺失、marker 数量异常或 begin/end 顺序无效时抛出。
    """

    # patch 只能作用于已经存在的文件。
    if not path.exists():

        # 阻断对不存在文件的 patch 写回。
        raise ExtractionError(
            f"> ERR: [Python] Patch target file does not exist: {path}"
        )

    # 读取目标文件当前文本，准备在 marker 区间内做替换。
    str_file_text = path.read_text(encoding="utf-8")  # patch 目标文件当前文本

    # 先拆成按行列表，便于定位 begin/end marker 的索引。
    list_lines = str_file_text.splitlines()  # 目标文件的按行文本

    # 生成 begin marker 的完整匹配文本。
    str_begin_token = f"HLS-GEN-PATCH-BEGIN {marker}"  # patch 起始标记

    # 生成与 begin 对应的 end marker 匹配文本，供区间尾部定位使用。
    str_end_token = f"HLS-GEN-PATCH-END {marker}"  # patch 结束标记

    # 收集 begin marker 在文件中的全部下标。
    list_begin_indices = [
        int_index  # 当前 begin marker 命中的行号
        for int_index, str_line in enumerate(list_lines)  # 逐行扫描目标文件
        if str_begin_token in str_line  # 只保留包含 begin marker 的行
    ]  # begin marker 的命中下标列表

    # 收集 end marker 在文件中的全部下标，供尾边界校验使用。
    list_end_indices = [
        int_index  # 当前可作为 patch 结束边界的候选行号
        for int_index, str_line in enumerate(list_lines)  # 逐行扫描目标文件以寻找尾边界
        if str_end_token in str_line  # 只保留包含 end marker 的尾标记行
    ]  # 用于定位 patch 结束边界的候选下标列表

    # begin/end marker 都必须且只能出现一次。
    if len(list_begin_indices) != 1 or len(list_end_indices) != 1:

        # 阻断 marker 缺失或重复，避免 patch 覆盖错误区间。
        raise ExtractionError(
            "> ERR: [Python] Patch marker "
            f"{marker!r} must appear exactly once as begin and end markers "
            f"in {path.name}."
        )

    # 记录 begin marker 的唯一下标，后续要保留它之前的所有原始内容。
    int_begin_index = list_begin_indices[0]  # begin marker 的唯一下标

    # 记录 end marker 的唯一下标，后续要保留它及其后的原始内容。
    int_end_index = list_end_indices[0]  # end marker 所在的唯一结束边界下标

    # begin marker 必须位于 end marker 之前。
    if int_begin_index >= int_end_index:

        # 阻断 begin/end 顺序反转的无效 patch 区间。
        raise ExtractionError(
            "> ERR: [Python] Patch marker "
            f"{marker!r} has an invalid begin/end order in {path.name}."
        )

    # 准备把 patch 正文替换到两个 marker 之间。
    list_replacement_lines = content.rstrip().splitlines()  # patch 正文的按行内容

    # 重新拼出更新后的完整文件行序列。
    list_updated_lines = [
        *list_lines[: int_begin_index + 1],  # 保留 begin marker 之前及其所在行
        *list_replacement_lines,  # 插入新的 patch 正文
        *list_lines[int_end_index:],  # 保留 end marker 及其后续内容
    ]  # patch 写回后的完整文件行列表

    # 以统一换行风格回写 patch 目标文件。
    path.write_text("\n".join(list_updated_lines) + "\n", encoding="utf-8")
