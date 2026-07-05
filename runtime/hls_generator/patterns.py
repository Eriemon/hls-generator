"""HLS 生成模式元数据与规则查询工具。"""

# 延迟注解求值，避免运行时解析递归结构类型。
from __future__ import annotations

# 通用类型用于描述来自用户 spec 的嵌套配置。
from typing import Any

# 集中维护模式元数据字段和 prompt 约束，供需求确认与提示词渲染复用。
PATTERN_RULES: dict[str, dict[str, Any]] = {  # HLS 模式名称到规则定义的映射
    "fir": {  # FIR 滤波器模式规则定义
        "label": "FIR filter family",  # 供提示词和诊断复用的模式显示名
        "metadata_fields": {  # 生成前必须向用户确认的模式元数据问题
            "tap_count": "Confirm the tap count before generating the FIR kernel.",  # FIR 抽头数确认问题
            "coefficient_symmetry": (  # FIR 系数对称性确认问题
                "Confirm whether the FIR coefficients are symmetric, antisymmetric, or unconstrained."  # FIR 系数对称约束确认问题
            ),
            "structure_style": "Confirm the FIR structure style such as direct form, transposed, or systolic.",  # FIR 结构形态确认问题
            "interface_style": (  # FIR 接口形态确认问题
                "Confirm whether the FIR surface is memory-mapped, AXI-Stream, or another interface style."  # FIR 表面接口风格确认问题
            ),
            "ii_target": "Confirm the II target for the FIR sample loop before generation.",  # FIR 采样循环 II 目标确认问题
        },
        "prompt_rules": [  # 注入 prompt 的 FIR 设计约束句集合
            (
                "For FIR patterns, make tap count, coefficient symmetry, structure style, interface style, "
                "and II target explicit before choosing PIPELINE, UNROLL, or DATAFLOW pragmas."
            ),
            (
                "Do not switch between direct-form, transposed, symmetric, or AXI-Stream FIR structures "
                "unless the confirmed metadata supports that structure choice."
            ),
            (
                "Explain how the confirmed tap count, symmetry, and II target shape the local buffer, "
                "multiply-accumulate schedule, and stream or memory interface behavior."
            ),
        ],
    },
    "fft": {  # FFT/DFT 变换家族的模式规则定义
        "label": "FFT/DFT transform family",  # 供模式匹配器识别 FFT/DFT 规格归属的标签
        "metadata_fields": {  # FFT/DFT 方案生成前必须补齐的规格字段
            "point_count": "Confirm the point count before generating the FFT or DFT kernel.",  # 约束 FFT 点数必须先被确认
            "scaling_strategy": (  # 约束缩放策略不能在生成后再临时猜测
                "Confirm the scaling strategy such as per-stage scale, block floating point, or none."  # 明确 FFT 缩放口径
            ),
            "twiddle_representation": (  # 约束旋转因子实现形式必须预先明确
                "Confirm the twiddle representation such as lookup table, fixed-point format, or generated "  # 明确旋转因子来源
                "constants."  # 补全旋转因子实现说明
            ),
            "complex_data_mode": (  # 约束复数数据的输入输出组织方式
                "Confirm the complex input/output representation such as packed IQ, split real-imag arrays, "  # 明确复数数据打包方式
                "or real-input transform mode."  # 补全复数数据模式说明
            ),
            "error_tolerance": "Confirm the acceptable FFT numerical error tolerance or comparison threshold.",  # 约束数值误差验收口径
        },
        "prompt_rules": [  # 约束 FFT 提示词必须显式保留的生成规则
            (  # 强制在注释和 testbench 中保留 FFT 核心规格
                "For FFT and DFT patterns, keep point count, scaling strategy, twiddle representation, "  # 先列出必须显式保留的 FFT 规格
                "complex data mode, and error tolerance explicit in the generated comments and testbench."  # 补全注释与 testbench 保留要求
            ),
            (  # 强制缩放策略只能选择一套一致口径
                "Do not mix block-floating, per-stage scaling, or fixed-point twiddle policies; choose one "  # 强调缩放与旋转因子策略不能混用
                "scaling strategy and keep the error tolerance reviewable."  # 补全误差阈值可审查要求
            ),
            (  # 强制解释旋转因子和调度策略如何由规格推导
                "Explain how twiddle storage, stage scheduling, and complex-data packing follow from the "  # 先限定需要解释的结构来源
                "confirmed FFT metadata instead of inventing a transform structure ad hoc."  # 补全禁止拍脑袋生成结构的约束
            ),
        ],
    },
    "cordic": {  # CORDIC 迭代数学模式规则定义
        "label": "CORDIC iterative math family",  # 标签文本把需求归到 CORDIC 旋转与向量化数学流程
        "metadata_fields": {  # 这些确认项决定常量表、迭代终止和象限修正策略
            "cordic_mode": "Confirm the CORDIC mode such as rotation, vectoring, or sincos generation.",  # 明确 CORDIC 属于旋转、向量化或正余弦生成模式
            "iteration_count": "Confirm the CORDIC iteration count before generation.",  # 约束迭代次数必须先被确认
            "angle_range": "Confirm the supported input angle range or quadrant normalization contract.",  # 明确输入角度范围与象限归一化契约
            "fixed_point_format": "Confirm the fixed-point width and integer-bit format used by the CORDIC datapath.",  # 明确数据通路定点位宽格式
            "error_tolerance": "Confirm the acceptable CORDIC numerical error tolerance.",  # 误差门限会反推位宽选择和需要保留的迭代轮数
        },
        "prompt_rules": [  # prompt 规则要逼出象限处理、shift-add 轨迹与误差解释
            (
                "For CORDIC patterns, make mode, iteration count, angle range, fixed-point format, and error "
                "tolerance explicit before generating lookup constants or shift-add loops."
            ),
            (
                "Do not claim a CORDIC design is valid across quadrants or angle ranges unless the confirmed "
                "angle-range contract explains the normalization or sign-correction strategy."
            ),
            (
                "Explain how the confirmed iteration count and fixed-point format trade off resource usage, "
                "latency, and numerical error tolerance."
            ),
        ],
    },
    "matmul": {  # 矩阵乘法模式规则定义
        "label": "matrix multiply family",  # 标签强调这是矩阵乘内核而不是卷积或扫描类算子
        "metadata_fields": {  # 矩阵乘方案生成前必须补齐的规格字段
            "tile_shape": "Confirm the matrix tile shape or blocking geometry before generation.",  # 明确分块几何形状
            "layout": "Confirm the matrix layout or packing convention used by the compute kernel.",  # 明确矩阵布局与打包方式
            "accumulator_type": "Confirm the accumulation type and growth policy for the matrix compute path.",  # 明确累加器类型与位宽增长策略
            "memory_schedule": "Confirm the memory schedule such as naive, blocked, or load-compute-store dataflow.",  # 明确访存调度方式
            "ii_target": "Confirm the II target for the matrix inner loop or tile loop.",  # 约束关键循环 II 目标
        },
        "prompt_rules": [  # 约束矩阵乘提示词必须显式保留的生成规则
            (
                "For matrix multiply patterns, keep tile shape, layout, accumulator type, memory schedule, "
                "and II target explicit before choosing partition or dataflow pragmas."
            ),
            (
                "Do not mix blocked local-buffer, partitioned tile, and load-compute-store dataflow "
                "strategies without a confirmed memory schedule and tile geometry."
            ),
            (
                "Explain how the confirmed tile shape, layout, and accumulator type constrain local "
                "buffering, loop order, and matrix interface depth values."
            ),
        ],
    },
    "prefix_scan": {  # 前缀扫描模式规则定义
        "label": "prefix scan family",  # 标签把需求固定到前缀累加与块间传播结构
        "metadata_fields": {  # 前缀扫描方案生成前必须补齐的规格字段
            "block_size": "Confirm the block size used by the prefix-scan schedule.",  # 明确扫描分块大小
            "scan_mode": "Confirm whether the prefix scan is inclusive, exclusive, segmented, or another scan mode.",  # 明确 inclusive 或 exclusive 等扫描模式
            "offset_propagation": "Confirm how block offsets or carry values propagate between scan segments.",  # 明确块间偏移或进位传播方式
            "latency_strategy": "Confirm the latency strategy such as sequential chain or blocked parallel scan.",  # 明确时延策略
            "boundary_policy": "Confirm the boundary policy for empty, short, or partial scan ranges.",  # 明确边界数据处理策略
        },
        "prompt_rules": [  # 约束前缀扫描提示词必须显式保留的生成规则
            (
                "For prefix-scan patterns, keep scan mode, block size, offset propagation, latency strategy, "
                "and boundary policy explicit before restructuring the accumulation chain."
            ),
            (
                "Do not claim a blocked scan is correct unless the offset propagation contract between blocks "
                "is explicit and covered by the testbench."
            ),
            (
                "Explain how the confirmed block size and latency strategy shorten or preserve loop-carried "
                "dependencies without hiding boundary behavior."
            ),
        ],
    },
    "rle_axis": {  # AXI-Stream 游程编码模式规则定义
        "label": "AXI-Stream run-length codec family",  # 标签用于识别带帧边界语义的 AXIS RLE 编解码需求
        "metadata_fields": {  # 这些字段约束帧尾、空帧和 run-length 截断行为
            "frame_boundary_mode": "Confirm the frame boundary mode for AXI-Stream packets before generation.",  # 明确帧边界表达方式
            "tlast_policy": "Confirm the TLAST policy for the final encoded or decoded symbol.",  # 明确最终符号的 TLAST 策略
            "run_length_limit": "Confirm the maximum run-length value before splitting or rejecting a run.",  # 明确最大 run-length 上限
            "empty_frame_policy": "Confirm the empty-frame policy instead of guessing how AXI-Stream represents it.",  # 明确空帧编码策略
            "ii_target": "Confirm the II target for the stream processing loop.",  # 约束流处理循环 II 目标
        },
        "prompt_rules": [  # prompt 规则要锁住 TLAST、回压和待输出游程序列的叙述
            (
                "For RLE AXI-Stream patterns, keep frame boundary mode, TLAST policy, run-length limit, "
                "empty-frame policy, and II target explicit before generating stream control logic."
            ),
            (
                "Do not hide TLAST, TKEEP, or TSTRB handling behind generic AXIS language; explain how the "
                "final symbol or pair preserves the frame boundary contract."
            ),
            (
                "Explain how the confirmed run-length limit and empty-frame policy shape split-run behavior, "
                "pending output rules, and stream backpressure assumptions."
            ),
        ],
    },
    "minimal_vitis_pipeline": {  # 最小 Vitis kernel 编译链路模式规则定义
        "label": "minimal Vitis kernel compile/link structure",  # 标签强调这里只覆盖最小 Vitis 编译与链接骨架
        "metadata_fields": {  # 最小 Vitis kernel 方案生成前必须补齐的规格字段
            "compile_link_boundary": (  # 明确 compile 与 link 阶段的职责边界
                "Confirm the compile/link boundary that the minimal Vitis kernel flow must preserve."  # 先把 compile 产物与 link 消费面的交接说清
            ),
            "top_kernel_role": "Confirm the role of the top kernel source in the minimal Vitis project layout.",  # 明确顶层 kernel 源文件承担的角色
            "bundle_naming_rule": (  # 明确下游依赖的 bundle 命名稳定规则
                "Confirm the stable bundle naming rule that downstream Vitis integration should preserve."  # 先锁定 bundle 名称，避免 compile 与 link 约定漂移
            ),
        },
        "prompt_rules": [  # 约束最小 Vitis 链路提示词必须显式保留的生成规则
            (
                "For minimal Vitis kernel patterns, keep the compile/link split clear: do not mix package or "
                "host orchestration into the generated HLS source."
            ),
            (
                "Use stable bundle names and explicit depth values so downstream Vitis compile/link flows can "
                "plan around the generated HLS interface cleanly."
            ),
            (
                "Treat the top kernel source as the primary home of the interface contract and compute logic, "
                "while keeping wider project orchestration out of scope."
            ),
        ],
    },
    "host_kernel_split": {  # kernel 源与辅助头拆分模式规则定义
        "label": "kernel source and helper-header structure",  # 供模式匹配器识别主源文件拆分规格归属的标签
        "metadata_fields": {  # kernel 源拆分方案生成前必须补齐的规格字段
            "kernel_source_boundary": (  # 明确主 kernel 源与辅助头的边界
                "Confirm the boundary between the main kernel source and the supporting helper headers."  # 先分清接口归属文件与工具函数归属文件
            ),
            "helper_header_role": (  # 明确 helper header 应承担的职责范围
                "Confirm what responsibilities belong in helper headers instead of the top kernel source."  # 先圈定哪些辅助逻辑适合沉到 header
            ),
            "hotspot_file_strategy": (  # 明确 pragma 密集热点逻辑的集中策略
                "Confirm how pragma-dense hotspot logic should stay concentrated instead of spreading across "  # 先说明热点 pragma 代码不要被打散
                "every file."  # 让顶层接口文件保持可读边界
            ),
        },
        "prompt_rules": [  # 约束源文件拆分提示词必须显式保留的生成规则
            (
                "For host-kernel split patterns, keep helper header responsibilities distinct from the main "
                "kernel source so the top HLS file still owns the visible interface contract."
            ),
            (
                "Concentrate dense pragma usage in a small number of hotspot helper/source files rather than "
                "scattering complex directives uniformly across all files."
            ),
            (
                "Do not mix package or host orchestration into the generated HLS source even when the wider "
                "project uses separate host or package stages."
            ),
        ],
    },
    "array_partition": {  # ARRAY_PARTITION 模式规则定义
        "label": "local-buffer array partition",  # 标签说明优化关注数组分块并发而不是宽字重塑
        "metadata_fields": {  # 这些问题用来锁定冲突来源、分割维度和分割因子
            "target_buffer": "Confirm the target buffer that needs ARRAY_PARTITION before generation.",  # 明确需要分割的本地缓冲区
            "partition_dim": "Confirm the partition dim that matches the contended access dimension.",  # 明确竞争访问对应的分割维度
            "partition_type": "Confirm the partition type (complete, cyclic, or block) before generation.",  # 明确 complete/cyclic/block 分割方式
            "partition_factor": "Confirm the partition factor required to relieve the parallel access bottleneck.",  # 明确缓解并行瓶颈所需的分割因子
            "contention_reason": "Confirm the memory contention reason that justifies ARRAY_PARTITION.",  # 明确采用 ARRAY_PARTITION 的竞争原因
        },
        "prompt_rules": [  # prompt 规则要把并发冲突证据与 partition 选择绑在一起
            (
                "For ARRAY_PARTITION patterns, treat the directive as a response to a specific parallel "
                "access bottleneck, not as a generic speed hint."
            ),
            (
                "When an outer loop is pipelined, account for the implied inner-loop concurrency and bind the "
                "partition dimension, type, and factor to the accessed dimension identified by "
                "schedule-viewer or equivalent report evidence."
            ),
            (
                "Explain why ARRAY_PARTITION is better than ARRAY_RESHAPE for the confirmed target buffer and "
                "do not combine them on the same variable."
            ),
        ],
    },
    "array_reshape": {  # 用于按相邻访问拓宽局部数组视图的规则块
        "label": "local-buffer array reshape",  # 标签说明优化关注存储视图拓宽而非并发分裂
        "metadata_fields": {  # 这些问题用来锁定拓宽维度、reshape 形式与带宽证据
            "target_buffer": "Confirm the target buffer that needs ARRAY_RESHAPE before generation.",  # 明确需要重塑的本地缓冲区
            "reshape_dim": "Confirm the reshape dim that matches the adjacent access dimension.",  # 明确与相邻访问维度匹配的重塑维度
            "reshape_type": "Confirm the reshape type used for the widened local view.",  # 明确局部视图扩宽所用的重塑类型
            "adjacent_access_reason": "Confirm why adjacent elements are consumed together on the reshaped buffer.",  # 明确相邻元素并行消费的原因
            "bandwidth_bottleneck": (  # 明确需要 report 证据支撑的带宽瓶颈
                "Confirm the bandwidth bottleneck, preferably from schedule-viewer or equivalent report "  # 先说明 reshape 的收益必须有报告证据
                "evidence."  # 避免把 reshape 当成拍脑袋优化
            ),
        },
        "prompt_rules": [  # prompt 规则要围绕相邻访问故事和宽字收益展开
            (
                "For ARRAY_RESHAPE patterns, widen the storage or local word only when adjacent elements are "
                "consumed together and the access pattern justifies it."
            ),
            (
                "When an outer loop is pipelined, account for the implied inner-loop concurrency and use "
                "schedule-viewer or equivalent report evidence to tie ARRAY_RESHAPE to the real load/store "
                "bottleneck."
            ),
            (
                "Explain why ARRAY_RESHAPE is preferred over ARRAY_PARTITION for the confirmed target buffer "
                "and keep the reshape dimension aligned with the adjacent-access story."
            ),
        ],
    },
    "axi4_burst": {  # AXI4 burst/coalesced 访存模式规则定义
        "label": "AXI4 burst/coalesced memory",  # 标签把规格归到 AXI4 连续突发访存场景
        "metadata_fields": {  # 这里要求先说明连续访问条件与 burst 上限
            "burst_max_len": "Confirm the AXI4 maximum burst length for the coalesced memory path.",  # 明确最大 burst 长度
            "coalesced_access": (  # 明确连续合并访问成立的前提
                "Confirm the contiguous/coalesced AXI4 access pattern that makes burst transfers valid."  # 先确认地址访问连续，burst 才有合法依据
            ),
        },
        "prompt_rules": [  # prompt 规则要核对地址顺序、burst 长度和循环次序
            (
                "When the pattern is AXI4 burst/coalescing, keep memory access contiguous, preserve an "
                "explicit burst length, and explain why the chosen loop order enables burst transfers."
            ),
            (
                "Do not claim burst throughput unless the access pattern is sequential enough for the "
                "configured max burst length and the cfg/interface settings match it."
            ),
        ],
    },
    "dataflow": {  # 用于读算写拆阶段与通道规划的规则块
        "label": "read-compute-write dataflow",  # 标签把需求识别成读算写拆分的数据流架构
        "metadata_fields": {  # 这些确认项定义 stage 边界、通道形态和验证门槛
            "stage_boundaries": (  # 明确 read/compute/write 阶段边界
                "Confirm the explicit read/compute/write stage boundaries before generating a DATAFLOW "
                "design."  # 避免 DATAFLOW 生成时把阶段边界混成一个大循环
            ),
            "channel_kind": "Confirm the channel kind inferred or required between DATAFLOW stages.",  # 明确阶段间通道类型
            "channel_depth": "Confirm the FIFO or channel depth needed between DATAFLOW stages.",  # 明确阶段间 FIFO 深度
            "cosim_required": (  # 明确是否要求 cosim 或 dataflow viewer 证据
                "Confirm that co-simulation or dataflow viewer validation is required for this DATAFLOW "
                "design."  # 让吞吐和 FIFO 结论必须绑定验证证据
            ),
        },
        "prompt_rules": [  # prompt 规则要要求 stage 切分、FIFO 深度和 cosim 证据
            (
                "For DATAFLOW patterns, default to an explicit read/compute/write stage split with named "
                "helper regions or functions and clear channel ownership."
            ),
            (
                "Treat DATAFLOW as a task-architecture decision that must preserve stage boundaries, FIFO "
                "depth assumptions, and backpressure behavior."
            ),
            (
                "Do not claim the DATAFLOW design is complete from syntax alone; require "
                "co-simulation/dataflow-viewer style evidence for FIFO sizing, stalls, and throughput "
                "confirmation."
            ),
        ],
    },
    "fixed_point": {  # 定点数值策略模式规则定义
        "label": "fixed-point numeric strategy",  # 标签强调数值契约以定点位宽和误差预算为中心
        "metadata_fields": {  # 定点方案生成前必须补齐的规格字段
            "numeric_range": "Confirm the fixed-point numeric range that the kernel must preserve.",  # 明确必须保持的数值范围
            "integer_bits": "Confirm the number of integer bits required by the fixed-point design.",  # 明确整数位宽要求
            "quantization_mode": "Confirm the fixed-point quantization mode before generation.",  # 明确定点量化模式
            "overflow_mode": "Confirm the fixed-point overflow mode before generation.",  # 明确定点溢出模式
            "error_budget": "Confirm the acceptable fixed-point error budget or tolerance.",  # 明确可接受误差预算
        },
        "prompt_rules": [  # 约束定点提示词必须显式保留的生成规则
            (
                "For fixed-point patterns, make numeric range, integer bits, quantization mode, overflow "
                "mode, and error budget explicit in the generated comments and helper choices."
            ),
            (
                "Do not silently translate a floating-point tutorial idea into fixed-point code without "
                "preserving the confirmed numeric contract and oracle expectations."
            ),
            (
                "Use fixed-point widths as a hardware decision that remains reviewable across device "
                "migration, report review, and regression vectors."
            ),
        ],
    },
    "line_buffer_stencil": {  # 行缓冲 stencil 模式规则定义
        "label": "line-buffer / stencil window",  # 标签把需求归到窗口复用型 stencil 访存结构
        "metadata_fields": {  # 行缓冲 stencil 方案生成前必须补齐的规格字段
            "window_shape": "Confirm the stencil window shape or tap count before generating a line-buffer design.",  # 明确 stencil 窗口形状或 tap 数
            "border_policy": "Confirm the boundary handling policy for the line-buffer stencil.",  # 明确边界处理策略
            "line_buffer_name": "Confirm the intended local line-buffer identifier used by the stencil structure.",  # 明确本地 line buffer 标识符
        },
        "prompt_rules": [  # prompt 规则要说明窗口复用、边界处理与 line buffer 责任
            (
                "For line-buffer/stencil patterns, make the window shape, border policy, and local "
                "line-buffer ownership explicit before optimization."
            ),
            (
                "Use local buffer comments and pragmas to explain how the stencil reuses neighboring samples "
                "without mixing ARRAY_PARTITION and ARRAY_RESHAPE on the same buffer."
            ),
        ],
    },
    "multi_m_axi": {  # 多 m_axi 带宽拆分模式规则定义
        "label": "multi-m_axi bandwidth split",  # 供模式匹配器识别多通道带宽拆分规格归属的标签
        "metadata_fields": {  # 多 m_axi 方案生成前必须补齐的规格字段
            "bundle_map": "Confirm the bundle map for each independent m_axi argument before generation.",  # 明确每个参数绑定的 bundle 映射
            "traffic_independence": "Confirm which traffic groups must stay on independent memory channels.",  # 明确必须独立通道承载的流量分组
            "read_write_concurrency": (  # 明确多 bundle 的并发依据
                "Confirm the intended read/write concurrency that justifies multiple m_axi bundles."  # 先说清多 bundle 对应的并发目标
            ),
        },
        "prompt_rules": [  # 约束多 m_axi 提示词必须显式保留的生成规则
            (
                "For multi-m_axi patterns, make the bundle map explicit and keep each independent traffic "
                "group aligned with its own memory channel."
            ),
            (
                "Do not stop at bundle names alone; explain how the chosen bundle split preserves the "
                "confirmed read/read/write concurrency and arbitration intent."
            ),
            (
                "When multiple masters share no data dependence, preserve distinct bundles and concrete "
                "depths for each channel so co-simulation matches the intended bandwidth split."
            ),
        ],
    },
    "reduction_tree": {  # 归约树模式规则定义
        "label": "reduction / tree accumulation",  # 标签把需求归到树形归约而不是串行累加
        "metadata_fields": {  # 归约树方案生成前必须补齐的规格字段
            "reduction_op": "Confirm the reduction operator before generating a tree-accumulation design.",  # 明确归约运算符
            "accumulator_type": "Confirm the accumulator type and overflow strategy for the reduction tree.",  # 明确累加器类型与溢出策略
            "tree_shape": "Confirm the reduction-tree shape or fan-in policy.",  # 明确树形结构或 fan-in 策略
        },
        "prompt_rules": [  # 约束归约树提示词必须显式保留的生成规则
            (
                "For reduction-tree patterns, keep the accumulator type, reduction operator, and tree shape "
                "explicit so latency/resource tradeoffs stay reviewable."
            ),
            (
                "Do not unroll or reassociate reductions without a confirmed accumulator strategy and "
                "matching vectors for overflow or rounding edges."
            ),
        ],
    },
    "tiled_gemm": {  # 分块 GEMM 模式规则定义
        "label": "tiled GEMM / systolic-like local buffering",  # 标签强调这里是 tile 复用驱动的 GEMM 局部缓冲结构
        "metadata_fields": {  # 分块 GEMM 方案生成前必须补齐的规格字段
            "tile_shape": "Confirm the tile shape used by the GEMM local buffers.",  # 明确局部缓冲使用的 tile 形状
            "accumulator_type": "Confirm the accumulation type used by the GEMM tile compute path.",  # 明确 tile 计算路径的累加器类型
            "layout": "Confirm the matrix layout or packing convention for the tiled GEMM interfaces.",  # 明确接口矩阵布局与打包规则
        },
        "prompt_rules": [  # 约束分块 GEMM 提示词必须显式保留的生成规则
            (
                "For tiled GEMM patterns, explain the tile shape, local buffer roles, and accumulation policy "
                "before adding unroll or partition directives."
            ),
            (
                "Preserve the intended matrix layout and show why the chosen local buffering matches the tile "
                "reuse strategy."
            ),
        ],
    },
    "vector_lane": {  # hls_vector 通道打包模式规则定义
        "label": "hls_vector lane packing",  # 标签把规格定位到 hls_vector lane 打包方案
        "metadata_fields": {  # 这些字段说明 lane 宽度和相邻样本打包语义
            "lane_width": "Confirm the hls_vector lane width before generating packed-lane logic.",  # 明确 lane 宽度
            "pack_intent": "Confirm how adjacent samples are packed into hls_vector lanes.",  # 明确相邻样本的打包意图
        },
        "prompt_rules": [  # prompt 规则要固定 lane 语义和标量边界处理
            (
                "When the pattern is hls_vector lane packing, make the lane width and packing intent explicit "
                "and keep scalar boundary handling visible."
            ),
            "Do not add hls_vector.h unless the lane mapping and packed datapath intent are confirmed.",  # 头文件引入必须服从真实 lane 映射需求
        ],
    },
    "task_graph": {  # hls_task 任务图模式规则定义
        "label": "hls_task task graph",  # 标签把需求归到 hls_task 风格的任务编排
        "metadata_fields": {  # 这些字段界定任务重启、通道深度与责任边界
            "restart_semantics": "Confirm the restart semantics for the hls_task graph.",  # 明确任务图重启语义
            "channel_depth": "Confirm the channel depth needed between hls_task stages.",  # 明确任务图通道深度
            "channel_ownership": "Confirm channel ownership for each task-graph producer/consumer boundary.",  # 明确任务边界上的通道归属
        },
        "prompt_rules": [  # prompt 规则要区分控制编排与数据通路任务
            (
                "For hls_task task graphs, distinguish control-driven orchestration from data-driven stages "
                "and keep restart semantics explicit."
            ),
            "Only introduce hls_task.h when channel ownership, depth, and task boundaries are confirmed.",  # 避免在任务边界未定时提前引入 hls_task 依赖
        ],
    },
    "streamofblocks": {  # 用于 block 级流传递与阶段缓冲的规则块
        "label": "hls_streamofblocks block streaming",  # 标签把规格归到 block 级 stream 传递
        "metadata_fields": {  # 这些字段界定 block 粒度与生产消费归属
            "block_size": "Confirm the stream-of-blocks block size before generation.",  # 明确块流 block 大小
            "block_ownership": "Confirm producer/consumer ownership of each block-stream stage.",  # 明确块流阶段的生产者与消费者归属
        },
        "prompt_rules": [  # prompt 规则要强调块级契约而不是普通 FIFO
            (
                "For hls_streamofblocks patterns, explain the block size and block ownership so buffering "
                "decisions stay reviewable."
            ),
            (
                "Do not treat block streaming as ordinary scalar FIFO traffic; preserve the block-level "
                "contract explicitly."
            ),
        ],
    },
    "directio_freerun": {  # hls_directio 常运行控制模式规则定义
        "label": "hls_directio free-running control",  # 标签强调这是常运行 direct I/O 控制面
        "metadata_fields": {  # 这些字段先锁定常运行前提与控制协议
            "free_running": (  # 明确是否必须保持常运行执行语义
                "Confirm whether the kernel must be free-running before using hls_directio or ap_ctrl_none "
                "style control."  # 先确认控制面是否真要脱离启动事务持续运行
            ),
            "control_protocol": "Confirm the control protocol used by the free-running direct I/O surface.",  # 明确常运行直连接口的控制协议
        },
        "prompt_rules": [  # prompt 规则要守住常运行协议和缺头文件兜底
            (
                "For hls_directio/free-running patterns, keep the control protocol and always-on execution "
                "semantics explicit."
            ),
            (
                "When the target Vitis HLS toolchain does not provide hls_directio.h, keep the same "
                "free-running contract with ap_ctrl_none and standard stream headers instead of inventing "
                "unavailable library dependencies."
            ),
            "Do not claim a free-running kernel unless the control interface and reset behavior are both confirmed.",  # 常运行声明必须同时覆盖控制协议和复位语义
        ],
    },
    "fence_ordering": {  # hls_fence 有序化模式规则定义
        "label": "hls_fence ordering",  # 标签把需求归到显式内存有序化场景
        "metadata_fields": {  # 这些字段说明为什么需要 fence 以及作用边界
            "ordering_reason": "Confirm the memory-ordering reason before generating hls_fence usage.",  # 明确引入 fence 的有序化原因
            "ordering_scope": "Confirm which memory or stream interactions require fence ordering.",  # 明确需要 fence 的交互范围
        },
        "prompt_rules": [  # prompt 规则要把 fence 的危险来源和适用范围讲清楚
            (
                "For hls_fence ordering patterns, explain the ordering reason and scope in comments; fence "
                "usage must be justified by a specific hazard."
            ),
            (
                "Do not include hls_fence.h unless the ordering contract is explicit and narrower "
                "synchronization would be insufficient."
            ),
        ],
    },
}

# 记录高级模式默认需要显式引入的 HLS 扩展头文件。
ADVANCED_PATTERN_HEADERS = {  # 高级模式名称到默认头文件列表的映射
    "vector_lane": ["hls_vector.h"],  # 向量通道打包模式默认需要 hls_vector 头
    "task_graph": ["hls_task.h"],  # 任务图模式默认需要 hls_task 头
    "streamofblocks": ["hls_streamofblocks.h"],  # 块流模式默认需要 hls_streamofblocks 头
    "fence_ordering": ["hls_fence.h"],  # 栅栏有序化模式默认需要 hls_fence 头
}

# 汇总高级 HLS 库头，供验证阶段识别受控依赖。
ADVANCED_LIBRARY_HEADERS = {  # 允许由模式规则声明的高级 HLS 头文件集合
    "hls_task.h",  # hls_task 任务图头文件
    "hls_vector.h",  # hls_vector 通道打包头文件
    "hls_streamofblocks.h",  # hls_streamofblocks 块流头文件
    "hls_directio.h",  # hls_directio 直连 IO 头文件
    "hls_fence.h",  # hls_fence 有序化栅栏头文件
}

# 规范化用户 spec 或 profile 中的模式名称。
def canonical_pattern_name(spec_or_profile: dict[str, Any] | None) -> str:
    """
    从 spec 或 HLS profile 中提取统一的模式名称。

    :param spec_or_profile: 完整任务 spec 或已经展开的 HLS profile；缺失时返回空字符串。
    :return: 使用下划线分隔的小写模式名称，供规则表查询使用。
    """

    # 非字典输入没有可解析的 profile 结构。
    if not isinstance(spec_or_profile, dict):

        # 返回空模式名，让调用方自然落到无规则定义分支。
        return ""

    # 优先使用完整 spec 内的 hls_profile，否则把输入本身视为 profile。
    dict_profile: dict[str, Any] = (  # 解析 example_pattern 时使用的 profile 视图
        spec_or_profile.get("hls_profile")  # 直接读取完整 spec 中的 hls_profile 字段
        if isinstance(spec_or_profile.get("hls_profile"), dict)  # 只有完整 spec 提供 dict 形态的 hls_profile 才直接展开
        else spec_or_profile  # 直接传入 profile 时沿用原对象
    )

    # workflow 中的 example_pattern 作为旧调用路径的兼容来源。
    dict_workflow: dict[str, Any] = (  # 兼容读取 workflow 层遗留的 example_pattern 声明
        spec_or_profile.get("workflow")  # 直接读取 workflow 层遗留的模式声明
        if isinstance(spec_or_profile.get("workflow"), dict)  # 只有 workflow 字段本身是字典时才继续取旧入口
        else {}  # 缺少 workflow 字段时回退为空字典
    )

    # 合并两个来源后保留原始用户输入，后续统一做大小写和分隔符规范化。
    str_pattern = dict_profile.get("example_pattern") or dict_workflow.get("example_pattern") or ""  # 原始模式名称

    # 返回规则表使用的规范名称。
    return str(str_pattern).strip().lower().replace("-", "_")

# 查询指定 spec 或 profile 对应的模式规则定义。
def pattern_definition(spec_or_profile: dict[str, Any] | None) -> dict[str, Any]:
    """
    返回模式名称对应的规则定义。

    :param spec_or_profile: 完整任务 spec 或已经展开的 HLS profile；缺失时返回空定义。
    :return: 匹配到的模式规则字典；未知模式返回空字典。
    """

    # 使用统一名称查表，避免调用方重复处理大小写和连字符。
    return PATTERN_RULES.get(canonical_pattern_name(spec_or_profile), {})

# 提取模式注入 prompt 的约束句。
def pattern_prompt_rules(spec_or_profile: dict[str, Any] | None) -> list[str]:
    """
    读取指定模式需要追加到 prompt 的设计规则。

    :param spec_or_profile: 完整任务 spec 或已经展开的 HLS profile。
    :return: 可直接注入 prompt 的规则文本列表；未知模式返回空列表。
    """

    # 先取得完整定义，保持 prompt 规则和 metadata 字段来自同一条模式记录。
    dict_definition = pattern_definition(spec_or_profile)  # 当前模式的完整规则定义

    # 返回字符串化副本，隔离配置表内的原始对象。
    return [str(item) for item in dict_definition.get("prompt_rules", [])]

# 找出指定 spec 仍需用户确认的模式元数据。
def pattern_open_questions(spec: dict[str, Any]) -> list[str]:
    """
    根据模式规则列出缺失的元数据确认问题。

    :param spec: 当前 HLS 生成任务 spec，通常包含 hls_profile 和 metadata。
    :return: 需要在生成前向用户确认的问题列表，顺序与规则表保持一致。
    """

    # spec 内的 hls_profile 是模式和 metadata 的主要来源。
    dict_profile: dict[str, Any] = (  # 当前任务用于查模式与 metadata 的 hls_profile 视图
        spec.get("hls_profile")  # 从当前 spec 中读取 hls_profile 字段
        if isinstance(spec.get("hls_profile"), dict)  # 只有 hls_profile 真正是字典时才直接复用
        else {}  # 缺少合法 hls_profile 时回退为空 profile
    )

    # metadata 保存已确认的模式参数，缺失项需要继续追问。
    dict_metadata: dict[str, Any] = (  # 当前模式已被用户确认的 metadata 段
        dict_profile.get("metadata")  # 从 profile 中读取已经确认的 metadata 字段
        if isinstance(dict_profile.get("metadata"), dict)  # 只有 metadata 是字典时才按字段读取确认状态
        else {}  # metadata 不是字典时按“尚未确认任何字段”处理
    )

    # 读取当前模式在规则表中声明的确认字段。
    dict_definition = pattern_definition(spec)  # 当前 spec 对应的模式规则定义

    # metadata_fields 的 key 是字段名，value 是给用户看的确认问题。
    dict_metadata_fields: dict[str, Any] = dict_definition.get("metadata_fields", {})  # 模式要求确认的字段说明

    # 保持问题列表稳定，便于测试和 prompt 快照比较。
    list_questions: list[str] = []  # 尚未满足的确认问题

    # 先按模式规则表检查标准元数据字段。
    for str_key, str_question in dict_metadata_fields.items():

        # 空值表示该字段还没有被用户或上游流程确认。
        if dict_metadata.get(str_key) in (None, "", [], {}):

            # 追加规则表中面向用户的原始确认问题。
            list_questions.append(str(str_question))

    # 额外 required_metadata_fields 用于项目侧临时要求的字段。
    for str_required_key in dict_profile.get("required_metadata_fields", []) or []:

        # 字段名转为字符串后继续按 metadata 键查询。
        str_field = str(str_required_key)  # 额外必填元数据字段名

        # 只有确实缺失时才生成兜底问题。
        if dict_metadata.get(str_field) in (None, "", [], {}):

            # 用户问题使用空格化字段名，避免暴露下划线内部命名。
            str_human_field = str_field.replace("_", " ")  # 面向用户展示的字段名称

            # 兜底问题保留英文协议文本，和现有 prompt 规则保持一致。
            str_question = f"Confirm the {str_human_field} metadata before generation."  # 兜底确认问题

            # 避免标准字段和额外字段产生重复问题。
            if str_question not in list_questions:

                # 保留额外字段声明顺序，便于 prompt 输出稳定。
                list_questions.append(str_question)

    # 返回所有仍需确认的模式问题。
    return list_questions

# 汇总指定模式需要携带的 HLS 头文件。
def required_pattern_headers(spec_or_profile: dict[str, Any] | None) -> list[str]:
    """
    返回模式和 profile 明确要求的 HLS 头文件。

    :param spec_or_profile: 完整任务 spec 或已经展开的 HLS profile；缺失时只返回空列表。
    :return: 按默认模式头文件和 profile 追加头文件合并后的去重列表。
    """

    # 优先提取完整 spec 中已展开的 hls_profile；直接传入 profile 时沿用原对象。
    dict_profile: dict[str, Any] = (  # 读取 required_headers 时使用的 profile 视图
        spec_or_profile.get("hls_profile")  # 从完整 spec 中抽取 required_headers 所属的 hls_profile
        if isinstance(spec_or_profile, dict) and isinstance(spec_or_profile.get("hls_profile"), dict)  # 仅在完整 spec 携带合法 hls_profile 时展开
        else spec_or_profile or {}  # 直接传入 profile 时沿用原对象，缺失时退回空字典
    )

    # 先复制规则表里登记的默认高级头文件，避免后续追加时修改共享配置。
    list_required: list[str] = list(  # 当前模式默认头文件的可变副本
        ADVANCED_PATTERN_HEADERS.get(canonical_pattern_name(dict_profile), [])  # 规则表中登记的默认高级头文件序列
    )

    # 只有字典 profile 才可能带有额外 required_headers 配置。
    if isinstance(dict_profile, dict):

        # 逐项合并调用方显式声明的头文件。
        for str_header_item in dict_profile.get("required_headers", []) or []:

            # 头文件名去除首尾空白后再判断是否有效。
            str_header = str(str_header_item).strip()  # profile 显式要求的头文件名

            # 非空且未出现过的头文件才追加，保持返回列表稳定去重。
            if str_header and str_header not in list_required:

                # 追加 profile 侧声明的补充头文件。
                list_required.append(str_header)

    # 返回调用方用于 include 检查或 mock 渲染的头文件列表。
    return list_required
