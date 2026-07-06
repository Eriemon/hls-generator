# HLS Readability Gate Checklist

Run these local static checks before handing HLS artifacts downstream:

```bash
python -m compileall -q runtime integration scripts
python -m scripts.python.cli.hls_generator selfcheck --json
python -m scripts.python.cli.hls_generator readability-gate --target hls --path <hls-dir> --profile kernel --style current-project --json
python -m scripts.python.cli.hls_generator validate --target hls --spec <spec.json> --path <hls-dir> --readiness static --no-external
```

For comment-only changes, always pass the baseline tree:

```bash
python -m scripts.python.cli.hls_generator readability-gate --target hls --path <commented-dir> --baseline-path <baseline-dir> --profile kernel --style current-project --json
python -m scripts.python.cli.hls_generator validate --target hls --spec <spec.json> --path <commented-dir> --baseline-path <baseline-dir> --readiness static --no-external
```

The report must retain these sections in validation metrics: `comment_quality_gate`, `hls_ast_comment_guard`, `hls_naming_gate`, and `readability_gate`.

Use `--fail-on-warning` only when the review stage treats structural warnings, long functions, or multi-line declaration warnings as CI blockers.
