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

Notes:

- Only files under `--project-root` are compared. Entries outside that path
  are ignored in both JSON and Excel data.
- Use `--debug-rule REFLECTION_USAGE` (repeatable, or `--debug-rule *`) to
  print per-rule counts and sample differences. Combine with `--debug-file`
  to focus on a single file name.
- Use `--list-rules` to print rule names and counts for JSON/Excel inputs.
- Use `--debug-scope` to see how many entries were filtered out by
  `--project-root`.
- Use `--debug-excel` to show which sheet contains `ext_attr1` and how many
  entries were parsed. Adjust `--excel-header-scan` if the header row is
  not near the top.
