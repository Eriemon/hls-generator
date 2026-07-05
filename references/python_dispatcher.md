# Python 调度器工作流

## 入口判断

当用户请求包含 `python`、`.py`、`py文件`、`py file`、`python代码设计` 或 `python代码优化` 时，视为 readable-python-generator 可处理的 Python 任务。匹配不区分大小写；任何以 `.py` 结尾的文件路径也视为触发。

## 分类结果

`scripts/python/task_dispatcher/classify_python_task.py` 输出稳定 JSON：

- `triggered`: 是否命中 Python 触发入口。
- `task_type`: `generate`、`modify` 或 `explain`。
- `confidence`: `high`、`medium` 或 `low`。
- `matched_triggers`: 命中的触发词。
- `target_paths`: 请求中的 `.py` 目标路径。
- `recommended_profile`: `library`、`scientific`、`script`、`cli`、`notebook`、`test` 或 `refactor`。
- `required_checks`: 当前分类下必须执行或解释的检查项。
- `check_applicability`: `task_classification`、`comment_quality_gate`、`typed_variable_naming` 当前是否 `available`、`after_code_exists` 或 `not_applicable_until_code_exists`。
- `needs_target_or_code`: 治理类 modify 请求没有真实 `.py` 路径或代码目标时为 `true`。

`test` 只是测试代码的语义 profile，不是豁免 profile；调度器推荐 `test` 后仍必须执行类型、docstring、注释风格、print、硬编码路径、裸 `assert` 和 current-project 门禁。

所有触发后的结果都必须显式包含 `task_classification`、`comment_quality_gate` 和 `typed_variable_naming` 三类检查；如果暂时没有代码目标，不静默省略，而是在 `check_applicability` 中标记不可执行状态。

## 执行矩阵

### generate

先写 intent contract，再生成 Python。生成后至少执行语法检查、注释风格门和 typed variable naming 门。若暂时没有落盘目标，执行器返回 `post_generation_checks`，由 agent 在代码落盘后继续运行。

### modify

先对已有 `.py` 目标应用安全子集变量重命名，再运行现有 quality gate 和变量命名门。若发现注释问题，执行器输出 `comment_rewrite_required` 和 `comment_rewrite_plan`，agent 必须基于真实代码语义重写注释；脚本只负责检测、定位、分类、保留特殊注释清单和复检，不生成模板化注释，也不把固定注释写入目标文件。

结构化注释修复计划包含：

- `preserve_comments`: shebang、编码声明、`noqa`、`type: ignore`、`pragma`、版权/许可证等特殊注释。
- `remove_comment_ranges`: 普通注释中需要移除或重写的行范围。
- `rewrite_targets`: 触发 PG025、PG030、PG031、PG033、PG035、PG036、PG037、PG043 或 PG044 的语义重写目标。

计划中不得出现 `suggested_comment`、`template_comment`、`replacement_text` 或任何可被脚本直接写回源码的建议注释文本字段。添加注释、补注释、批量规范化注释都只产生语义重写目标；最终注释必须由 agent 阅读上下文后手写。

### explain

只做分类和说明，不强制生成或修改代码。若请求中包含真实存在的 `.py` 目标，可以提供只读质量门摘要。

## 安全修复边界

执行器默认调用安全子集 token 级重命名；`--no-write-renames` 可关闭自动落盘，`--write-renames` 保留为显式兼容开关。该操作只替换 AST 确认的同作用域 `NAME` token，不得改写字符串字面量、注释、字典键、JSON 字段、DataFrame 列名、属性名、魔法名、框架固定参数或公开 API 参数。不满足安全边界的建议只进入报告，不强改。
