"""提供 HLS 代码可读性门禁的包级导出入口。"""

# 导出报告数据结构，供运行时和测试代码复用。
from .report import HlsGateIssue, HlsGateReport
from .runner import run_hls_readability_gate
from .rewrite_plan import build_hls_comment_rewrite_plan

# 约束包级公共 API，避免外部调用方直接依赖内部模块。
__all__ = ["HlsGateIssue", "HlsGateReport", "run_hls_readability_gate", "build_hls_comment_rewrite_plan"]  # 包级公开导出名称
