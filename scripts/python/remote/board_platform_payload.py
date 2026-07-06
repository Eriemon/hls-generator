"""校验并打包本地 U55C 平台载荷。"""

# 延迟解析类型注解，减少运行期导入依赖
from __future__ import annotations

# 标准库依赖
import json
import os
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# 成功状态供调用方判断平台载荷是否可归档
PASS_STATUS = "passed"  # 平台载荷校验通过状态

# 失败状态供调用方识别缺失文件或元数据问题
FAILED_STATUS = "failed"  # 平台载荷校验失败状态

# 固定 U55C 平台名需要与 .xpfm 文件和 dependency 元数据对齐
U55C_PLATFORM_NAME = "xilinx_u55c_gen3x16_xdma_3_202210_1"  # 官方 U55C 平台标识

# 许可证文件不一定出现在 xpfm 引用中，需额外纳入校验集合
EXTRA_REQUIRED_RELATIVE_PATHS = ("license/LICENSE",)  # xpfm 之外的必需相对路径

# 默认 U55C 载荷根目录解析入口
def default_local_u55c_payload_root() -> Path:
    """
    返回当前工作区可用的本地 U55C 平台载荷目录。

    参数:
        无外部业务参数；函数只读取固定环境变量和当前文件位置。
    返回:
        已展开并解析的 U55C 平台载荷目录路径。
    异常:
        本函数不主动抛出业务异常；路径不存在时交给后续校验函数报告。
    """

    # 读取用户显式指定的 U55C 平台载荷目录
    str_env_override = os.environ.get("ERIE_HLS_U55C_PLATFORM_ROOT")  # 用户覆盖的载荷目录

    # 用户覆盖优先级最高，避免仓库布局推断误导本地验收
    if str_env_override:

        # 返回展开后的用户覆盖路径
        return Path(str_env_override).expanduser().resolve()

    # 定位当前模块文件，用于反推出同级 VitisDeveloper 依赖目录
    path_source_file = Path(__file__).resolve()  # 当前模块绝对路径

    # 沿父目录寻找 Codex Skills 工作区根
    for path_parent in path_source_file.parents:

        # 命中 Skills 根目录后切到 VitisDeveloper 的平台依赖副本
        if path_parent.name == "Skills":

            # 返回同工作区 VitisDeveloper 技能维护的平台载荷目录
            return (
                path_parent
                / "VitisDeveloper"
                / "skills"
                / ".dependencies"
                / "board"
                / "xilinx"
                / "u55c"
            ).resolve()

    # staging 或独立仓库场景没有全局 `Skills` 根时，退回仓库内 `skills/` 同级兄弟目录。
    for path_parent in path_source_file.parents:

        # 命中当前仓库的 `skills/` 分层后，改从仓库根回推同级 VitisDeveloper。
        if path_parent.name == "skills":

            # 返回仓库根同级的 VitisDeveloper 平台载荷目录。
            return (
                path_parent.parent
                / "VitisDeveloper"
                / "skills"
                / ".dependencies"
                / "board"
                / "xilinx"
                / "u55c"
            ).resolve()

    # 独立检出时，回退到当前技能自带的本地 U55C 依赖目录。
    return (path_source_file.parents[2] / ".dependencies" / "board" / "xilinx" / "u55c").resolve()

# dependency 元数据读取辅助函数
def _load_dependency_source_metadata(path_dependency_source: Path) -> tuple[dict[str, Any], list[str]]:
    """
    读取并验证 `.dependency_source.json` 的基础结构。

    参数:
        path_dependency_source: dependency 元数据文件路径。
    返回:
        解析后的元数据字典，以及读取阶段发现的错误列表。
    异常:
        JSON 解析错误会转换成错误文本返回，不向外抛出。
    """

    # 缺省返回值保持和主校验流程的空元数据语义一致。
    dict_dependency_source: dict[str, Any] = {}  # 解析后的 dependency 元数据

    # 收集元数据读取阶段的错误，供主流程统一汇总。
    list_errors: list[str] = []  # dependency 元数据读取错误

    # 缺失元数据文件时直接登记错误，后续平台名仍可回退到期望值。
    if not path_dependency_source.exists():

        # 缺失元数据会削弱平台来源证明，但不阻止后续文件布局检查。
        list_errors.append("missing .dependency_source.json")

        # 文件不存在时不再继续读取。
        return dict_dependency_source, list_errors

    # 文件存在时尝试解析 JSON 文本。
    try:

        # 读取 dependency 元数据的原始 JSON 对象。
        obj_loaded_json: object = json.loads(path_dependency_source.read_text(encoding="utf-8"))  # 解析后的元数据对象

    # JSON 语法损坏时，把异常文本交还给上层报告。
    except json.JSONDecodeError as exc:

        # 记录无法解析的 dependency 元数据错误。
        list_errors.append(f"invalid .dependency_source.json: {exc}")

        # 解析失败时保持空元数据。
        return dict_dependency_source, list_errors

    # 只有 JSON 对象才能承载 platform_name 等字段。
    if not isinstance(obj_loaded_json, dict):

        # 非对象根节点无法提供可信的 dependency 元数据。
        list_errors.append(".dependency_source.json root must be an object")

        # 结构错误时仍保持空元数据。
        return dict_dependency_source, list_errors

    # 结构合法时把元数据交给主校验流程继续使用。
    return obj_loaded_json, list_errors

# 必需载荷文件缺失检查辅助函数
def _missing_required_payload_paths(path_payload_root: Path, list_required_paths: list[str]) -> list[str]:
    """
    返回当前载荷缺失的必需相对路径列表。

    参数:
        path_payload_root: 当前平台载荷根目录。
        list_required_paths: 归档前必须存在的相对路径集合。
    返回:
        当前载荷缺失的必需相对路径列表。
    异常:
        本函数只做文件存在性判断，不主动抛出业务异常。
    """

    # 按顺序登记缺失路径，保持报告输出和必需路径清单一致。
    list_missing_paths: list[str] = []  # 当前载荷缺失的相对路径列表

    # 逐项验证每个必需路径是否已经落在载荷根目录下。
    for str_relative_path in list_required_paths:

        # 缺失文件时保留原始相对路径，供报告和错误聚合复用。
        if not (path_payload_root / str_relative_path).exists():

            # 记录当前缺失的载荷文件相对路径。
            list_missing_paths.append(str_relative_path)

    # 返回供主校验流程复用的缺失路径列表。
    return list_missing_paths

# 本地平台载荷完整性校验入口
def validate_local_board_platform_payload(
    root: str | Path,
    *,
    expected_platform_name: str = U55C_PLATFORM_NAME,
) -> dict[str, Any]:
    """
    检查本地平台载荷是否包含 U55C 验收所需元数据和引用文件。

    参数:
        root: 待检查的平台载荷根目录，可以是字符串或 Path。
        expected_platform_name: 期望匹配的 Xilinx U55C 平台名称。
    返回:
        包含状态、根目录、平台名、引用路径、缺失路径、字节数和错误列表的报告字典。
    异常:
        JSON 解析错误会被收集到返回报告中，不向外抛出。
    """

    # 标准化调用方传入的平台载荷根目录
    path_payload_root = Path(root).expanduser().resolve()  # 待检查的载荷根目录

    # 收集所有可恢复校验问题，便于一次性反馈给调用方
    list_payload_errors: list[str] = []  # 载荷校验错误列表

    # dependency 元数据记录平台名和来源，是归档前的必要证明
    path_dependency_source = path_payload_root / ".dependency_source.json"  # 依赖来源元数据文件

    # 根目录缺失时继续检查其余字段，返回报告保留完整上下文
    if not path_payload_root.exists():

        # 登记根目录缺失问题
        list_payload_errors.append(f"missing root: {path_payload_root}")

    # 保存 dependency 元数据读取 helper 返回的结构化结果元组。
    tuple_dependency_metadata = _load_dependency_source_metadata(path_dependency_source)  # 元数据读取 helper 的结果元组

    # 单独取出元数据字典，后续还要据此比对 payload 路径与平台声明是否一致。
    dict_dependency_source = tuple_dependency_metadata[0]  # 后续平台一致性校验使用的元数据字典

    # 取出 dependency 元数据读取阶段已经收集到的错误列表。
    list_dependency_errors = tuple_dependency_metadata[1]  # 元数据读取阶段的错误列表

    # 把 dependency 元数据读取阶段的错误并入总报告。
    list_payload_errors.extend(list_dependency_errors)

    # 从元数据读取平台名，缺省时使用期望平台名保证 xpfm 路径可继续检查
    str_platform_name = str(dict_dependency_source.get("platform_name") or expected_platform_name).strip()  # 实际平台名

    # 平台名不一致意味着载荷可能来自错误板卡或错误版本
    if str_platform_name != expected_platform_name:

        # 记录期望值与实际值，帮助定位错误平台包
        list_payload_errors.append(
            f"platform_name mismatch: expected {expected_platform_name}, got {str_platform_name or '<empty>'}"
        )

    # 按平台名定位 xpfm 主描述文件
    path_xpfm = path_payload_root / f"{str_platform_name}.xpfm"  # 平台 xpfm 描述文件

    # xpfm 缺失时无法发现引用文件，只保留空引用集合
    if not path_xpfm.exists():

        # 登记 xpfm 缺失问题
        list_payload_errors.append(f"missing xpfm: {path_xpfm.name}")

        # 缺少 xpfm 时没有可解析的引用文件
        list_referenced_paths: list[str] = []  # xpfm 引用的相对路径

    # xpfm 存在时提取 XSA/SPFM 等平台组成文件
    else:

        # 解析 xpfm 内的相对引用路径
        list_referenced_paths = _xpfm_referenced_relative_paths(path_xpfm, list_payload_errors)  # xpfm 引用路径

    # 合并 xpfm 引用文件和额外约定文件，去重后固定顺序输出
    list_required_paths = _required_relative_paths(list_referenced_paths)  # 载荷必须包含的相对路径

    # 统一收集缺失文件路径，避免主函数继续膨胀分支与循环复杂度。
    list_missing_paths = _missing_required_payload_paths(path_payload_root, list_required_paths)  # 当前载荷缺失的相对文件路径

    # 将每个缺失文件写入错误列表，保留给调用方显示
    for str_relative_path in list_missing_paths:

        # 为当前缺失文件生成可读错误文本
        str_missing_message = f"missing required payload file: {str_relative_path}"  # 缺失文件错误文本

        # 追加缺失文件错误，便于报告展示完整清单
        list_payload_errors.append(str_missing_message)

    # 初始化报告上下文，后续逐项填入已校验字段
    dict_report_context: dict[str, Any] = {}  # 报告组装所需上下文

    # 记录载荷根目录，供报告 helper 统计体积
    dict_report_context["path_payload_root"] = path_payload_root  # 已解析的平台载荷根目录

    # 记录当前载荷声明的平台名
    dict_report_context["str_platform_name"] = str_platform_name  # 当前载荷声明的平台名

    # 记录调用方要求的平台名
    dict_report_context["expected_platform_name"] = expected_platform_name  # 调用方要求的平台名

    # 记录 dependency 元数据文件位置
    dict_report_context["path_dependency_source"] = path_dependency_source  # dependency 元数据路径

    # 记录 dependency 元数据内容
    dict_report_context["dict_dependency_source"] = dict_dependency_source  # dependency 元数据内容

    # 记录平台 xpfm 描述文件位置
    dict_report_context["path_xpfm"] = path_xpfm  # 平台 xpfm 描述文件路径

    # 记录载荷归档前必须存在的相对文件路径
    dict_report_context["list_required_paths"] = list_required_paths  # 必需相对路径清单

    # 记录当前载荷实际缺失的相对文件路径
    dict_report_context["list_missing_paths"] = list_missing_paths  # 缺失相对路径清单

    # 记录前序校验收集到的所有错误
    dict_report_context["list_payload_errors"] = list_payload_errors  # 校验错误清单

    # 交给报告 helper 统一生成对外字段
    return _payload_validation_report(dict_report_context)

# 平台载荷归档入口
def create_platform_archive(payload: dict[str, Any], output_dir: str | Path) -> Path:
    """
    将已通过校验的平台载荷打包成 tar.gz 归档。

    参数:
        payload: validate_local_board_platform_payload 返回的通过校验报告。
        output_dir: 归档文件输出目录。
    返回:
        生成的 tar.gz 归档路径。
    异常:
        ValueError: 当 payload 状态不是 passed 时抛出，避免归档错误载荷。
    """

    # 只有通过校验的载荷允许进入归档步骤
    if payload.get("status") != PASS_STATUS:

        # 阻止失败报告被误打包为可部署平台载荷
        raise ValueError(f"> ERR: [Python] Cannot archive invalid platform payload: {payload.get('errors')}")

    # 解析载荷根目录字段，确保归档时使用绝对路径
    path_payload_root = Path(str(payload["root"])).resolve()  # 已验证的载荷根目录

    # 平台名决定归档顶层目录，需保留原字段语义
    str_platform_name = str(payload["platform_name"])  # 归档内顶层平台目录名

    # 标准化归档输出目录
    path_archive_dir = Path(output_dir).resolve()  # 归档输出目录

    # 确保调用方指定的输出目录存在
    path_archive_dir.mkdir(parents=True, exist_ok=True)

    # 生成固定命名的 gzip tar 包路径
    path_archive = path_archive_dir / f"{str_platform_name}.tar.gz"  # 目标归档路径

    # 旧归档存在时先删除，避免 tarfile 追加或保留过期内容
    if path_archive.exists():

        # 删除同名旧归档以保证输出内容完全来自当前载荷
        path_archive.unlink()

    # 使用低压缩级别缩短本地验收打包时间
    with tarfile.open(path_archive, "w:gz", compresslevel=1) as archive:

        # 按路径排序确保归档内容顺序稳定
        for path_candidate in sorted(path_payload_root.rglob("*")):

            # 目录不作为单独条目写入，文件路径会隐式保留结构
            if not path_candidate.is_file():

                # 跳过目录和特殊文件，继续处理下一个候选路径
                continue

            # 将载荷文件写入平台名顶层目录下，匹配远端解包预期
            archive.add(
                path_candidate,
                arcname=(Path(str_platform_name) / path_candidate.relative_to(path_payload_root)).as_posix(),
            )

    # 返回生成的归档路径给调用方继续上传或记录
    return path_archive

# 本地 U55C 平台载荷准备入口
def prepare_local_u55c_platform_archive(
    output_dir: str | Path,
    *,
    local_root: str | Path | None = None,
) -> dict[str, Any]:
    """
    校验本地 U55C 平台载荷并在通过后生成归档。

    参数:
        output_dir: 归档输出目录。
        local_root: 可选的本地平台载荷根目录；为空时使用默认发现逻辑。
    返回:
        校验报告字典；成功时额外包含 archive_path，失败时 archive_path 为空字符串。
    异常:
        create_platform_archive 的 ValueError 会在校验通过但归档输入异常时向外传播。
    """

    # 选择调用方指定目录或默认 U55C 平台载荷目录
    path_payload_root = Path(local_root).expanduser().resolve() if local_root else default_local_u55c_payload_root()  # 载荷根目录

    # 对载荷目录执行完整本地校验。
    dict_payload = validate_local_board_platform_payload(path_payload_root, expected_platform_name=U55C_PLATFORM_NAME)  # 平台载荷校验报告

    # 校验失败时返回报告并显式标记未生成归档
    if dict_payload["status"] != PASS_STATUS:

        # 返回失败报告，不尝试归档不完整载荷
        return {**dict_payload, "archive_path": ""}

    # 校验通过后创建平台归档
    path_archive_path = create_platform_archive(dict_payload, output_dir)  # 已生成的平台归档路径

    # 返回带归档路径的成功报告
    return {**dict_payload, "archive_path": str(path_archive_path)}

# xpfm 平台描述文件引用解析
def _xpfm_referenced_relative_paths(xpfm_path: Path, errors: list[str]) -> list[str]:
    """
    从 xpfm XML 中提取平台依赖的 XSA/SPFM 相对路径。

    参数:
        xpfm_path: 待解析的 xpfm 描述文件路径。
        errors: 调用方维护的错误列表，解析失败或未发现引用时写入该列表。
    返回:
        xpfm 中声明的 XSA/SPFM 相对路径列表。
    异常:
        XML 解析异常会被写入 errors，不向外抛出。
    """

    # XML 解析失败时把错误并入整体载荷校验报告
    try:

        # 解析平台描述文件以遍历属性引用
        tree = ET.parse(xpfm_path)  # 用于遍历平台文件引用的 XML 树

    # xpfm 语法错误会导致引用列表不可信
    except ET.ParseError as exc:

        # 登记 XML 解析问题
        errors.append(f"invalid xpfm xml: {exc}")

        # 返回空引用集合，交给上层继续生成完整报告
        return []

    # 保存 xpfm 声明的 XSA/SPFM 相对路径
    list_references: list[str] = []  # 平台描述文件引用路径

    # 遍历所有 XML 元素，兼容不同层级的文件引用字段
    for element in tree.iter():

        # 读取可能带命名空间的 path 属性
        str_path_value = _namespaced_attr(element.attrib, "path")  # 文件引用所在目录

        # xpfm 把文件名拆在独立的 name 属性里，这里与 path 属性配对读取。
        str_name_value = _namespaced_attr(element.attrib, "name")  # 文件引用名称

        # 缺少路径或名称时无法组成文件引用
        if not str_path_value or not str_name_value:

            # 跳过非文件引用元素
            continue

        # 只收集远端验收需要的 XSA/SPFM 平台文件
        if not str_name_value.lower().endswith((".xsa", ".spfm")):

            # 跳过 xpfm 中的非平台文件引用
            continue

        # 组合成以 POSIX 分隔符表示的相对路径
        list_references.append((Path(str_path_value) / str_name_value).as_posix())

    # 未发现平台主体引用通常表示 xpfm 结构异常
    if not list_references:

        # 登记 xpfm 未引用 XSA/SPFM 的结构问题
        errors.append("xpfm did not reference any XSA/SPFM payload files")

    # 返回供上层合并额外必需文件的引用集合
    return list_references

# 命名空间属性读取辅助函数
def _namespaced_attr(attributes: dict[str, str], local_name: str) -> str:
    """
    读取 XML 属性字典中可能带命名空间的本地属性名。

    参数:
        attributes: ElementTree 暴露的属性字典。
        local_name: 不含命名空间前缀的属性本地名。
    返回:
        匹配属性的去空白字符串；未命中时返回空字符串。
    异常:
        本函数只处理内存字典，不主动抛出业务异常。
    """

    # 遍历属性键值，兼容普通名和 {namespace}name 两种格式
    for str_attr_key, str_attr_value in attributes.items():

        # 属性键直接匹配或命名空间本地名匹配时命中
        if str_attr_key == local_name or str_attr_key.endswith("}" + local_name):

            # 返回去除首尾空白后的属性值
            return str(str_attr_value).strip()

    # 未找到目标属性时返回空字符串供调用方判空
    return ""

# 目录体积统计辅助函数
def _directory_size(root: Path) -> int:
    """
    统计平台载荷目录内所有普通文件的总字节数。

    参数:
        root: 待统计的目录路径。
    返回:
        目录内普通文件大小之和，单位为字节。
    异常:
        文件系统访问异常由 Path.stat 原样抛出，便于调用方发现权限或竞态问题。
    """

    # 累加普通文件大小，避免目录条目影响载荷体积统计
    int_total_bytes = 0  # 载荷普通文件总字节数

    # 检查载荷目录下每个条目是否贡献实际文件字节
    for path_candidate in root.rglob("*"):

        # 只统计普通文件，忽略目录本身
        if path_candidate.is_file():

            # 将当前文件大小计入平台载荷体积
            int_total_bytes += path_candidate.stat().st_size  # 累加当前载荷文件字节数

    # 返回用于报告展示和远端验收记录的字节总量
    return int_total_bytes

# 必需相对路径合并辅助函数
def _required_relative_paths(list_referenced_paths: list[str]) -> list[str]:
    """
    合并 xpfm 引用路径和额外约定文件路径。

    参数:
        list_referenced_paths: 从 xpfm 解析出的相对引用路径。
    返回:
        去重、规范化并排序后的必需相对路径列表。
    异常:
        本函数只处理字符串路径，不主动抛出业务异常。
    """

    # 合并 xpfm 引用文件和额外约定文件
    list_all_paths = [*list_referenced_paths, *EXTRA_REQUIRED_RELATIVE_PATHS]  # 原始必需路径集合

    # 将路径统一成 POSIX 相对路径，保证报告跨平台稳定
    set_required_paths = {Path(str_item).as_posix() for str_item in list_all_paths}  # 去重后的必需路径集合

    # 返回排序后的路径列表，便于测试和报告比较
    return sorted(set_required_paths)

# 校验报告组装辅助函数
def _payload_validation_report(dict_report_context: dict[str, Any]) -> dict[str, Any]:
    """
    组装平台载荷校验报告的稳定字段集合。

    参数:
        dict_report_context: validate_local_board_platform_payload 收集出的路径、平台名、元数据和错误清单。
    返回:
        validate_local_board_platform_payload 对外暴露的报告字典。
    异常:
        本函数可能在统计目录体积时透传文件系统访问异常。
    """

    # 从上下文取回载荷根目录，后面要直接写入报告并参与体积统计。
    path_payload_root = dict_report_context["path_payload_root"]  # 后续用于统计体积并回填 root 字段的载荷根目录

    # 单独取回错误清单，后面既要计算 status，也要原样回填 errors。
    list_payload_errors = dict_report_context["list_payload_errors"]  # 已累计的可恢复校验错误文本列表

    # 根目录存在时统计实际载荷体积，否则固定为 0
    int_total_bytes = _directory_size(path_payload_root) if path_payload_root.exists() else 0  # 载荷总字节数

    # 初始化稳定输出字典，后续字段按固定顺序逐个写入。
    dict_report: dict[str, Any] = {}  # 按稳定字段顺序逐步填充的对外报告对象

    # 状态字段反映错误列表是否为空
    dict_report["status"] = PASS_STATUS if not list_payload_errors else FAILED_STATUS  # 载荷整体校验状态

    # 先回填载荷根目录，供归档和后续调试直接复用。
    dict_report["root"] = str(path_payload_root)  # 载荷根目录字符串

    # 平台名字段保留解析结果，便于调用方确认实际载荷归属。
    dict_report["platform_name"] = dict_report_context["str_platform_name"]  # 来自 dependency 元数据或默认值的实际平台名

    # 再写入期望平台名，显式保留平台比对基准。
    dict_report["expected_platform_name"] = dict_report_context["expected_platform_name"]  # 期望平台名

    # dependency 元数据路径用于定位来源文件
    dict_report["dependency_source_path"] = str(dict_report_context["path_dependency_source"])  # 元数据文件路径

    # dependency 元数据内容原样放入报告供上层审计
    dict_report["dependency_source"] = dict_report_context["dict_dependency_source"]  # 元数据对象

    # xpfm 路径用于归档和缺失诊断
    dict_report["xpfm"] = str(dict_report_context["path_xpfm"])  # xpfm 文件路径

    # 必需相对路径列表用于远端解包前检查
    dict_report["required_relative_paths"] = dict_report_context["list_required_paths"]  # 必需文件相对路径

    # 缺失相对路径列表用于失败报告展示
    dict_report["missing_relative_paths"] = dict_report_context["list_missing_paths"]  # 缺失文件相对路径

    # 最后补入总字节数，帮助调用方判断载荷是否明显残缺。
    dict_report["total_bytes"] = int_total_bytes  # 目录存在时统计得到的普通文件总字节数

    # 错误列表保留所有可恢复校验失败原因
    dict_report["errors"] = list_payload_errors  # 可恢复校验错误清单

    # 返回对外稳定报告对象
    return dict_report
