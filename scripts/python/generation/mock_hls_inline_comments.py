"""复用现有注释渲染器，为治理后的 mock HLS 文本补齐行级中文注释。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 复用已经通过 current-project 门禁的 HLS 行注释渲染器。
from .mock_comment_rendering import _ensure_hls_line_comment_coverage

# 对治理后的 HLS 文本执行统一的行级注释补齐。
def ensure_governed_line_comments(text: str, comment_language: str) -> str:
    """
    为治理后的 HLS 文本补齐行级中文注释覆盖。

    参数:
        text: 已完成命名和 contract 预处理的 HLS 文本，dtype=str，unit=text。
        comment_language: HLS 注释语言标识，dtype=str，unit=dimensionless。

    返回:
        补齐行级中文注释后的 HLS 文本，dtype=str，unit=text。
    """

    # 直接复用现有通过门禁的注释覆盖渲染器，避免重复维护一套规则。
    return _ensure_hls_line_comment_coverage(text, comment_language)
