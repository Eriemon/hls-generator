"""按职责聚合 mock HLS source 的签名、helper、loop 和 stream 注释改写入口。"""

# 启用延迟注解，避免类型提示在导入阶段提前求值。
from __future__ import annotations

# helper 子模块承接阶段调度、task actor、helper call 和条件分支说明。
from .mock_hls_source_helper_calls import (
    helper_function_call_comment_text,
    if_condition_comment_text,
    is_helper_function_call,
    stage_comment_text,
    task_actor_comment_text,
    task_actor_inline_comment_text,
)

# loop 子模块承接 blocked tile、stream flow 和直通阶段循环说明。
from .mock_hls_source_loops import (
    loop_comment_text,
    loop_comment_text_for_blocked_tile,
    loop_comment_text_for_blocked_tile_output,
    loop_comment_text_for_blocked_tile_progress,
    loop_comment_text_for_direct_stage,
    loop_comment_text_for_stream_flow,
)

# 签名子模块承接函数签名识别、规则表和尾注说明逻辑。
from .mock_hls_source_signatures import (
    function_signature_comment_text,
    function_signature_inline_comment_text,
    function_signature_ranges,
    is_active_signature_fragment,
    is_function_signature_fragment,
)

# 签名子模块继续导出 helper 名称分类与签名正文说明入口。
from .mock_hls_source_signatures import (
    is_generic_flow_helper_function_name,
    is_task_graph_helper_function_name,
    signature_body_entry_comment_text,
)

# 签名子模块继续提供 generic flow 的 header 和 body-entry 规则表入口。
from .mock_hls_source_signatures import (
    signature_generic_flow_body_entry_rules,
    signature_generic_flow_header_rules,
)

# 签名子模块再单独导出 inline 规则表，避免聚合层重新堆回一整片规则入口。
from .mock_hls_source_signatures import (
    signature_generic_flow_inline_rules,
)

# 签名子模块还会继续导出 parameter 和 inline channel 规则入口。
from .mock_hls_source_signatures import (
    signature_generic_flow_parameter_rules,
    signature_header_comment_text,
    signature_inline_channel_comment_text,
    signature_inline_length_comment_text,
)

# 签名子模块最后提供 parameter 规则和 task_graph 签名规则入口。
from .mock_hls_source_signatures import (
    signature_length_comment_text,
    signature_matmul_parameter_rules,
    signature_parameter_comment_text,
    signature_task_graph_actor_parameter_rules,
    signature_task_graph_data_parameter_rules,
    signature_task_graph_parameter_rules,
)

# stream 子模块承接 FIFO 声明、stream 写回和通道映射说明。
from .mock_hls_source_streams import (
    generic_stream_comment_maps,
    stream_declaration_comment_text,
    stream_declaration_inline_comment_text,
    stream_write_comment_text,
)
