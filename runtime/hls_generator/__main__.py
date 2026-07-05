"""提供 `python -m hls_generator` 的命令行入口。"""

# 导入运行时 CLI 主入口，保持模块执行路径与脚本入口一致。
from .cli import main

# 仅在模块直接执行时启动 CLI，导入时不产生副作用。
if __name__ == "__main__":

    # 把 CLI 返回码转换成模块执行时的进程退出状态。
    raise SystemExit(main())

