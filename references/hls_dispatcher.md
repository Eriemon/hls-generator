# HLS Dispatcher Workflow

The HLS dispatcher keeps the existing internal `generate` / `modify` / `explain`
modes for compatibility, but exposes readable-HLS routes that mirror the
readable-generator family at the user-intent level.

## Routes

- `create`: create new readable HLS C/C++ kernel artifacts from a confirmed HLS spec or scaffolded spec.
- `write`: edit existing HLS kernels, pragmas, interfaces, DATAFLOW regions, Vitis configuration, or testbench intent from an explicit behavior contract.
- `review`: analyze existing HLS code, reports, pragmas, interfaces, or HLS-generated RTL-facing evidence without rewriting files.
- `annotate`: perform comment-only HLS rewrites with Chinese semantic comments, mandatory baseline input, and token/AST fingerprint preservation.
- `validate`: run static HLS checks, Vitis csim/cosim/tool evidence, remote acceptance, or board evidence depending on requested readiness.

## Internal Modes

- `generate`: backs the `create` route and produces new HLS artifacts. Required checks: HLS readability gate, validation, optional external Vitis flow when configured.
- `modify`: backs `write` and `annotate`. Comment-only requests require `baseline_path` and run token + AST comment guards. Use a comment rewrite plan before editing comments.
- `explain`: backs `review` and many `validate` preflight classifications. Do not rewrite code unless the user requests modification.

## Check matrix

| Route | Vector artifacts | HLS readability gate | Comment policy | AST comment guard | Naming gate | Comment plan |
|---|---:|---:|---:|---:|---:|---:|
| create | required | required | required | parse-after | required | optional |
| write | refresh when test intent changes | required | required | optional baseline | required | optional |
| annotate | unchanged | required | required | required baseline | required | required |
| review | optional | recommended | recommended | baseline when comparing | recommended | optional |
| validate | required when behavior evidence changes | required before acceptance | required for generated/commented artifacts | required for comment-only validation | required | optional |

## Comment rewrite plan policy

`comment-plan` scripts only locate comments and code regions. They may output semantic context, preserve ranges, remove ranges, and rewrite targets. They must never output ready-to-paste comment text, and the JSON must not contain `suggested_comment`, `template_comment`, or `replacement_text`.
