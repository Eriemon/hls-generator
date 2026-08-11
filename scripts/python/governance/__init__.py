"""治理包装器的包级兼容入口。"""

# 导入模块注册表，用于保留旧版治理入口的兼容别名。
import sys

# 导入规范化后的治理委派实现，避免复制包装逻辑。
from . import skill_tool_delegate

# 保留旧版私有模块导入路径，避免既有调用方因文件规范化而失效。
sys.modules[f"{__name__}._skill_tool_delegate"] = skill_tool_delegate  # 注册旧版模块别名

# 约束包级公开导出名称，保持治理包的最小接口。
__all__ = ["skill_tool_delegate"]  # 包级公开导出名称
