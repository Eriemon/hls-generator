# Vitis HLS 2024.2 Script Guide

This reference distills the Vitis HLS scripting material from the imported UG1399 notes into stable rules for this HLS-only skill. Load it when changing Vitis command execution, `.cfg` parsing, Tcl rendering, prompt rules, or validation policy.

## Supported Script Surfaces

- Tcl flow: create/open project, add C/C++ sources and testbench files, configure part/clock, apply `config_*` settings and `set_directive_*` optimization directives, then run `csim_design`, `csynth_design`, optional `cosim_design`, optional `export_design`, and reports.
- `.cfg` flow: keep generated configs in the existing `syn.top`, `syn.file`, `tb.file`, `clock`, `part`, `flow_target` style. The parser also accepts UG-style sections such as `[hls] top/part/clock`, `[files] src/tb`, `[compile]`, `[interface]`, `[directive]`, `[csim]`, `[cosim]`, and `[export]`.
- Command-line flow: prefer the configured runtime tool order. The default policy tries `vitis-run --mode hls --tcl <script>` and falls back to `vitis_hls -f <script>`.
- Unified HLS `open_component` and direct `v++ --mode hls` flows are reference surfaces only for now. Do not replace the stable generated Tcl flow with them unless a future implementation explicitly adds and tests that execution mode.

## Tcl Command Pattern

Use this order when rendering run-local Tcl:

```tcl
open_project -reset -flow_target vitis <project>
set_top <top_function>
add_files <source.cpp>
add_files -tb <testbench.cpp>
open_solution -reset -flow_target vitis <solution>
set_part <part>
create_clock -period <period_ns>
set_clock_uncertainty <uncertainty_ns>
config_compile ...
config_interface ...
config_rtl ...
config_dataflow ...
config_schedule ...
config_csim ...
config_cosim ...
set_directive_pipeline ...
set_directive_array_partition ...
set_directive_dataflow ...
set_directive_interface ...
csim_design ...
csynth_design
cosim_design ...
export_design ...
config_export ...
report_utilization -file ./report/<solution>_utilization.rpt
report_timing -file ./report/<solution>_timing.rpt
report_directive -file ./report/<solution>_directive.rpt
report_dataflow -file ./report/<solution>_dataflow.rpt
report_interface -file ./report/<solution>_interface.rpt
exit
```

`flow_target` must be consistent across project and solution. Use `vitis` for Vitis kernel/XO flows and `vivado` for Vivado IP flows.
Config file paths must be relative artifact paths. Reject absolute paths, drive-qualified paths, empty path segments, `.` segments, and `..` parent traversal before rendering Tcl.
Report filenames should follow the active solution name from runtime configuration instead of assuming `solution1`.

## Accepted Config Keys

The runtime normalizes these keys:

- Top and files: `syn.top`, `syn.file`, `tb.file`, `[hls].top`, `[files].src`, `[files].tb`, `[files].cflags`, `[files].csimflags`.
- Device and clock: `part`, `clock`, `flow_target`, `clock_uncertainty`.
- Compile: `pipeline_loops`, `enable_auto_rewind`, `pipeline_style`, `unsafe_math_optimizations`.
- Interface: `m_axi_addr64`, `m_axi_max_read_burst_length`, `default_slave_interface`.
- RTL: `reset`, `register_all_io`, `module_prefix`, `reset_level`.
- Dataflow: `fifo_depth`, `strict_mode`, `start_fifo_depth`.
- Schedule: `enable_dsp_full_reg`.
- Directives: `pipeline`, `unroll`, `array_partition`, `dataflow`, `interface`, `dependence`, `inline`, `stream`, `aggregate`, `bind_op`, `bind_storage`, `loop_flatten`, `loop_merge`, `loop_tripcount`.
- Simulation/export: `csim.clean`, `csim.argv`, `csim.ldflags`, `cosim.rtl`, `cosim.tool`, `cosim.trace_level`, `cosim.wave_debug`, `cosim.random_stall`, `cosim.enable_tasks_with_m_axi`, `export.format`, `export.rtl`, `export.vendor`, `export.library`, `export.version`, `export.display_name`, `export.vivado_synth_strategy`, `export.ip_xdc_file`.

## Pragma And Interface Rules

- Add `#pragma HLS INTERFACE` for every external argument and for `port=return` when a control interface is requested.
- Use `m_axi` with explicit bundles and concrete `depth` values for AXI4 memory interfaces so C/RTL co-simulation has bounded memory models. Use `axis` and `hls::stream` for AXI4-Stream interfaces. Use `s_axilite` for scalar control ports unless the spec requests a different native/control mode.
- Use `PIPELINE`, `DATAFLOW`, `ARRAY_PARTITION`, `STREAM`, and `DEPENDENCE` only when the access pattern justifies them.
- Do not combine `array_partition` and `array_reshape` on the same variable.
- Keep dataflow regions simple: no recursion, no global-state dependency, and clear FIFO/stream boundaries.
- Do not unroll loops with unresolved runtime bounds unless the code first introduces a fixed static bound.

## Forbidden Or Deprecated Features

Reject these in generated code/config/scripts:

- `config_sdx`
- `set_directive_data_pack`
- old `set_directive_resource`
- `DATA_PACK` pragma
- `hls_linear_algebra.h`
- Dynamic allocation, exceptions, RTTI, unsupported STL containers, and variable-length stack arrays in kernel code.
- Kernel-only synthesizability restrictions must not be blindly applied to C++ testbench files.
- C arbitrary-precision types. Use C++ with `ap_int.h`, `ap_fixed.h`, and `hls_stream.h`.
- Obsolete `-std=c++0x` flags in Vitis HLS 2024.2 Clang-based flows.

## Out-of-Scope Script Surfaces

- Do not generate `open_component` projects until the runtime has a dedicated Unified HLS execution mode and smoke coverage.
- Do not generate `v++ -c`, `v++ -m cosim`, or `v++ -p` command lines as the primary path; keep them documented as future extension points.
- Do not silently pass through unknown `.cfg` sections or `set_directive_*` commands. Add explicit whitelist support and tests first.

## Migration Surface Policy

- Keep Tcl, Python, and unified CLI migration examples as reference material for compatibility reasoning and future extension planning.
- Do not switch the skill's stable execution path away from the current Tcl plus `.cfg` flow unless the runtime adds an explicit mode flag and smoke coverage proves it.
- When documenting migration options, describe roles and tradeoffs rather than copying upstream example names or directory structure.

For floating-point kernels, require an explicit decision about `config_compile -unsafe_math_optimizations`; do not silently enable it.

## Common Diagnostics

- Missing `create_clock` before synthesis causes invalid or misleading synthesis results.
- A static top function or class member cannot be used as `set_top`.
- The same port must not be assigned incompatible interface modes, such as `axis` and `m_axi`.
- Dataflow deadlocks often require reviewing FIFO depth, start propagation, and stream producer/consumer balance.
- II failures commonly come from memory port conflicts or false loop-carried dependencies. Use array partitioning, burst-aware memory layout, or explicit dependence directives only after confirming the access pattern.
- Over-partitioning arrays can explode LUT/FF/BRAM usage; choose complete/block/cyclic partitioning to match the access pattern.
