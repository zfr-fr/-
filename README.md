# -
企业级软件开发-新闻管理系统

## Tools

### compare_violations.py

Compare JSON violations against Excel detection results and emit a unified
JSON diff report with file/line content.

Usage:

```bash
python3 scripts/compare_violations.py \
  --json /path/to/report.json \
  --excel-dir /path/to/excel \
  --project-root /path/to/source \
  --output comparison_report.json
```

Optional overrides:

- `--strip-prefix` to remove path prefixes before comparison (repeatable)
- `--path-anchor` to trim to a path anchor like `Assets/` (repeatable)
- `--rule-map` to provide an explicit Excel-to-rule mapping
