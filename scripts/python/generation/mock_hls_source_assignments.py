"""按职责聚合 mock HLS source 的声明、赋值和结构字段注释改写入口。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# 上下文子模块承接 struct、函数签名回溯和上下文敏感赋值说明。
from .mock_assignment_context import (
    assignment_or_return_comment_text,
    contextual_assignment_comment_text,
    contextual_assignment_inline_comment_text,
    enclosing_signature_function_name,
)

# 上下文子模块还会继续导出 struct 字段与结构体命名回溯 helper。
from .mock_assignment_context import (
    enclosing_struct_name,
    struct_field_comment_text,
    struct_field_inline_comment_text,
    struct_header_comment_text,
)

# 模式子模块承接语句识别和累计更新说明逻辑。
from .mock_assignment_patterns import (
    accumulation_update_comment_text,
    accumulation_update_inline_comment_text,
    is_accumulation_update_statement,
    is_assignment_statement,
)

# 模式子模块还会继续导出声明说明和左值主标识符抽取 helper。
from .mock_assignment_patterns import (
    assigned_symbol_name,
    declaration_comment_text,
    declaration_inline_comment_text,
    is_local_declaration_statement,
)

# 角色子模块继续维护 AXIS 字段规则与字段级说明逻辑。
from .mock_assignment_roles import (
    assignment_axis_field_comment_text,
    assignment_axis_field_rules,
    assignment_axis_last_field_rules,
    assignment_axis_payload_field_rules,
)

# 角色子模块还负责普通赋值说明和窗口写回语义。
from .mock_assignment_roles import (
    assignment_comment_text,
    assignment_output_comment_text,
)

# 角色子模块继续补充局部状态写入和 stream/state 落点说明。
from .mock_assignment_roles import (
    assignment_local_state_comment_text,
    assignment_stream_or_state_comment_text,
)

# 角色子模块还会继续导出索引槽位和专属 pattern 赋值说明入口。
from .mock_assignment_roles import (
    indexed_slot_role_text,
    slot_lookup_text,
    specialized_assignment_comment_text,
    specialized_assignment_inline_comment_text,
)

# 角色子模块最后承接 inline 尾注说明的主入口。
from .mock_assignment_roles import (
    assignment_inline_axis_field_comment_text,
    assignment_inline_comment_text,
    assignment_inline_local_state_comment_text,
    assignment_inline_output_comment_text,
    assignment_inline_stream_source_comment_text,
)
