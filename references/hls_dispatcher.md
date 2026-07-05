# HLS Dispatcher Workflow

The HLS dispatcher mirrors the readable-python-generator generate / modify / explain split.

## Modes

- `generate`: create new HLS artifacts from a confirmed spec. Required checks: HLS readability gate, validation, optional external Vitis flow when configured.
- `modify`: edit existing HLS artifacts. When the task is comment-only, keep `baseline_path` mandatory and run token + AST comment guards. Use a comment rewrite plan before editing comments.
- `explain`: analyze or explain existing HLS code. Do not rewrite code unless the user requests modification. Run readability gate when the explanation includes quality findings.

## Check matrix

| Scenario | Vector artifacts | HLS readability gate | Comment policy | AST comment guard | Naming gate | Comment plan |
|---|---:|---:|---:|---:|---:|---:|
| generate | required | required | required | parse-after | required | optional |
| modify code | refresh when test intent changes | required | required | optional baseline | required | optional |
| modify comments only | unchanged | required | required | required baseline | required | required |
| explain/review | optional | recommended | recommended | baseline when comparing | recommended | optional |

## Comment rewrite plan policy

`comment-plan` scripts only locate comments and code regions. They may output semantic context, preserve ranges, remove ranges, and rewrite targets. They must never output ready-to-paste comment text, and the JSON must not contain `suggested_comment`, `template_comment`, or `replacement_text`.
