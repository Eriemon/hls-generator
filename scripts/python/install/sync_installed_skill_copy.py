#!/usr/bin/env python3
"""同步当前 skill 源码到本机 Codex 已安装副本。

stdout_protocol: json
机器可读 stdout 协议：本 CLI 成功时向 stdout 输出一个完整 JSON 对象，供自动化测试和安装流程读取。
"""

# 启用延迟标注，减少 CLI 启动时的类型解析开销
from __future__ import annotations

# 标准库命令行、JSON、目录复制和路径能力
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 同步安装副本时必须排除的开发期目录和状态文件
EXCLUDED_NAMES = {
    ".git",  # Git 仓库元数据
    ".pytest_cache",  # pytest 本地缓存
    "__pycache__",  # Python 字节码缓存
    "_smoke_runs",  # smoke 临时运行目录
    "reports",  # 本地验证报告目录
    "workflow-state.json",  # 本地工作流状态文件
}  # 安装同步排除名称集合

# 定位当前 skill 源码根目录
def skill_source_root() -> Path:
    """
    返回当前脚本所属的 skill 根目录。

    :param: 当前辅助函数不接收外部业务参数。
    :return: `skills/erie-hls-generator` 的绝对路径。
    """

    # 脚本位于 scripts/python/install 下，向上三层到达 skill 根
    path_source_root = Path(__file__).resolve().parents[3]  # 当前 skill 源码根目录

    # 返回给默认安装同步参数使用
    return path_source_root

# 计算默认安装目标目录
def default_destination() -> Path:
    """
    返回当前 skill 在本机 Codex 安装区的默认路径。

    :param: 当前辅助函数不接收外部业务参数。
    :return: 用户 `.codex/skills` 下同名 skill 目录。
    """

    # 安装副本默认放在用户 Codex skills 目录下
    path_destination = (Path.home() / ".codex" / "skills" / skill_source_root().name).resolve()  # 默认安装目标目录

    # 返回默认同步目标
    return path_destination

# 计算默认备份根目录
def default_backup_root(dest: Path) -> Path:
    """
    返回安装副本备份目录的默认父目录。

    :param dest: 即将被替换的安装目标目录。
    :return: 安装目标目录的父目录。
    """

    # 备份默认与安装目录放在同一父目录下
    path_backup_root = dest.parent.resolve()  # 默认备份根目录

    # 返回给同步流程拼接时间戳备份目录
    return path_backup_root

# 为 copytree 提供排除规则
def _ignore(_root: str, names: list[str]) -> set[str]:
    """
    过滤不应进入安装副本的开发期名称。

    :param _root: `shutil.copytree` 传入的当前扫描目录；本规则不需要使用。
    :param names: 当前目录下的候选文件名和目录名。
    :return: 需要排除的名称集合。
    """

    # 只排除当前层级中命中固定排除集合的名称
    set_ignored_names = {str_name for str_name in names if str_name in EXCLUDED_NAMES}  # copytree 排除名称

    # 返回给 shutil.copytree 的 ignore 回调
    return set_ignored_names

# 校验同步路径边界
def _validate_sync_paths(path_source: Path, path_dest: Path) -> None:
    """
    校验安装同步的源目录和目标目录。

    :param path_source: 已解析的 skill 源码目录。
    :param path_dest: 已解析的安装目标目录。
    :return: 无返回值；发现非法路径时抛出 ValueError。
    :raises ValueError: 源目录不存在、源目标相同或目标不是目录。
    """

    # 源目录必须是存在的 skill 目录
    if not path_source.is_dir():

        # 阻止不存在源目录进入复制流程
        raise ValueError(f"> ERR: [Python] Source skill directory does not exist: {path_source}")

    # 源目录和目标目录不能相同，避免删除当前开发目录
    if path_source == path_dest:

        # 阻止自覆盖导致源码丢失
        raise ValueError("> ERR: [Python] Source and destination skill directories must be different.")

    # 目标若存在，必须是可整体替换的目录
    if path_dest.exists() and not path_dest.is_dir():

        # 阻止用目录同步覆盖普通文件
        raise ValueError(f"> ERR: [Python] Destination exists but is not a directory: {path_dest}")

# 生成本次同步的备份目录
def _backup_path(path_dest: Path, path_backup_root: Path) -> Path:
    """
    生成带时间戳的安装副本备份目录。

    :param path_dest: 即将被替换的安装目标目录。
    :param path_backup_root: 备份目录所在父目录。
    :return: 本次同步使用的备份目录路径。
    """

    # 时间戳确保多次同步不会默认覆盖历史备份
    str_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # 备份目录时间戳

    # 备份目录沿用目标目录名称，方便人工识别来源
    path_backup = path_backup_root / f"{path_dest.name}-backup-{str_timestamp}"  # 本次备份目录

    # 返回给替换流程使用
    return path_backup

# 同步当前 skill 到安装副本
def sync_installed_skill_copy(source: Path, dest: Path, backup_root: Path) -> dict[str, object]:
    """
    用当前源码树替换本机 Codex 已安装 skill 副本。

    :param source: skill 源码目录。
    :param dest: Codex 已安装 skill 目标目录。
    :param backup_root: 旧安装副本的备份父目录。
    :return: 同步结果 JSON 载荷，包含源、目标、备份和排除项。
    :raises ValueError: 路径非法或备份目录已存在。
    """

    # 解析源目录，确保后续比较使用绝对路径
    path_source = source.resolve()  # 绝对源码目录

    # 解析安装目标目录，避免相对路径误判
    path_dest = dest.resolve()  # 绝对安装目标目录

    # 解析备份根目录，便于构造稳定备份路径
    path_backup_root = backup_root.resolve()  # 绝对备份根目录

    # 校验源目录和目标目录的安全边界
    _validate_sync_paths(path_source, path_dest)

    # 一旦开始搬迁旧副本，原目标路径就会腾空，因此备份落点必须先计算出来。
    path_backup = _backup_path(path_dest, path_backup_root)  # 即将承接旧安装副本的时间戳备份目录

    # 复制完成标志会进入 JSON 协议输出
    bool_copied = False  # 是否完成复制

    # 目标目录存在时先移动到备份目录
    if path_dest.exists():

        # 备份目录不允许预先存在，避免覆盖旧备份
        if path_backup.exists():

            # 阻止覆盖已有备份
            raise ValueError(f"> ERR: [Python] Backup path already exists: {path_backup}")

        # 移动旧安装副本到备份目录
        shutil.move(str(path_dest), str(path_backup))

    # 复制源码树到安装目录，并排除开发期产物
    shutil.copytree(path_source, path_dest, ignore=_ignore)

    # 标记复制已经完成，供 JSON 输出明确表达
    bool_copied = True  # 同步复制完成标志

    # 组装机器可读同步结果
    dict_result = {
        "source": str(path_source),  # 同步源目录
        "destination": str(path_dest),  # 安装目标目录
        "backup": str(path_backup) if path_backup.exists() else "",  # 旧安装副本备份目录
        "copied": bool_copied,  # 是否已经复制新副本
        "excluded_names": sorted(EXCLUDED_NAMES),  # 本次同步排除名称
    }  # 安装同步结果载荷

    # 返回给 CLI stdout JSON 协议使用
    return dict_result

# 构建命令行解析器
def _build_parser() -> argparse.ArgumentParser:
    """
    构建安装同步 CLI 的参数解析器。

    :return: 已配置 source、dest 和 backup-root 参数的解析器。
    """

    # 解析器说明该命令会替换本机 Codex 已安装副本
    parser = argparse.ArgumentParser(  # 安装同步参数解析器
        description="Sync the current erie-hls-generator source tree into the local Codex installed copy."  # CLI 主说明文案
    )

    # source 允许测试或人工指定替代源码目录
    parser.add_argument("--source", default=str(skill_source_root()))

    # dest 允许测试或人工指定替代安装目录
    parser.add_argument("--dest", default=str(default_destination()))

    # backup-root 允许调用方将旧副本备份放到独立目录
    parser.add_argument("--backup-root", default="")

    # main 只拿到已经配置完成的解析器对象，不再重复拼接参数结构。
    return parser

# CLI 入口函数
def main(argv: list[str] | None = None) -> int:
    """
    执行安装副本同步 CLI。

    :param argv: 显式传入的命令行参数；为 None 时读取进程参数。
    :return: 进程退出码；同步成功返回 0。
    """

    # 入口函数先取回参数定义结果，避免把默认值和帮助文案散落在执行流程中。
    parser = _build_parser()  # main 入口消费的 CLI 解析器实例

    # args 保存这次调用真正传入的字符串参数值。
    args = parser.parse_args(argv)  # 解析后的 CLI 参数

    # path_source 对应这次要复制出去的源码树根目录。
    path_source = Path(args.source)  # CLI 源码目录参数

    # path_dest 对应本机 Codex 已安装副本的替换目标位置。
    path_dest = Path(args.dest)  # CLI 安装目标参数

    # 空 backup-root 表示使用安装目标父目录
    path_backup_root = (
        Path(args.backup_root)  # 调用方显式指定的备份根目录
        if str(args.backup_root).strip()  # 非空字符串表示调用方主动覆盖默认备份位置
        else default_backup_root(path_dest)  # 未显式指定时沿用安装目录父级作为默认备份根
    )  # CLI 备份根目录参数

    # 执行安装副本同步并获取 JSON 结果
    dict_result = sync_installed_skill_copy(path_source, path_dest, path_backup_root)  # 同步结果载荷

    # 模块已声明 JSON stdout 协议，这里直接写出单个结构化结果对象。
    sys.stdout.write(json.dumps(dict_result, indent=2, ensure_ascii=False) + "\n")

    # 返回成功退出码
    return 0

# 仅直接运行脚本时进入 CLI
if __name__ == "__main__":

    # 将 main 返回码交给 Python 进程
    raise SystemExit(main())
