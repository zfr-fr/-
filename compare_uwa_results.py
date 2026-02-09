import argparse
import csv
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import openpyxl
except ImportError:
    print("请先安装依赖: pip install openpyxl")
    raise


@dataclass
class ViolationRecord:
    file: str
    line: Optional[int]
    rule: str
    description: str
    source: str
    sheet: str = ""
    row: Optional[int] = None


def normalize_path(path: str) -> str:
    if not path:
        return ""
    text = str(path).strip().replace("\\", "/")
    text = re.sub(r"^[a-zA-Z]:/", "", text)
    text = re.sub(r"/+", "/", text)
    return text.lower()


def normalize_rule(rule: str) -> str:
    if not rule:
        return ""
    return re.sub(r"\s+", "", str(rule).strip().upper())


def normalize_desc(desc: str) -> str:
    if not desc:
        return ""
    text = str(desc).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。,;；:：\-_/()（）\[\]{}<>\"'`~!@#$%^&*+=|\\]", "", text)
    return text


def parse_line_number(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.search(r"\d+", str(value))
    if match:
        return int(match.group(0))
    return None


def load_rule_map(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {normalize_rule(k): normalize_rule(v) for k, v in data.items()}


def detect_header(
    sheet, max_rows: int, header_aliases: Dict[str, List[str]]
) -> Tuple[Optional[int], Dict[str, int]]:
    best_row = None
    best_map = {}
    best_score = 0

    for row_idx in range(1, min(max_rows, sheet.max_row) + 1):
        row_cells = list(sheet[row_idx])
        col_map = {}
        score = 0

        for col_idx, cell in enumerate(row_cells, 1):
            value = "" if cell.value is None else str(cell.value).strip().lower()
            for key, aliases in header_aliases.items():
                if any(alias in value for alias in aliases):
                    if key not in col_map:
                        col_map[key] = col_idx
                        score += 1

        has_file = "file" in col_map
        has_line = "line" in col_map
        if score > best_score and (has_file and has_line):
            best_score = score
            best_row = row_idx
            best_map = col_map

    return best_row, best_map


def collect_uwa_records(
    files: List[Path],
    header_row_override: Optional[int],
    column_override: Dict[str, Optional[int]],
    sheet_name: Optional[str],
    max_header_scan: int,
) -> List[ViolationRecord]:
    header_aliases = {
        "file": ["file", "path", "文件", "文件名", "文件路径", "脚本", "资源", "pathname"],
        "line": ["line", "line number", "行号", "行", "行数"],
        "rule": ["rule", "规则", "类型", "违规类型", "问题类型", "分类", "检查项"],
        "desc": ["description", "desc", "说明", "描述", "问题", "详情", "原因", "content"],
    }

    records: List[ViolationRecord] = []
    for file_path in files:
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        sheets = [wb[sheet_name]] if sheet_name and sheet_name in wb.sheetnames else wb.worksheets

        for sheet in sheets:
            header_row = header_row_override
            col_map = {}

            if header_row is None:
                header_row, col_map = detect_header(sheet, max_header_scan, header_aliases)
            else:
                col_map = {}

            for key, col_idx in column_override.items():
                if col_idx:
                    col_map[key] = col_idx

            if not header_row or "file" not in col_map or "line" not in col_map:
                continue

            for row_idx in range(header_row + 1, sheet.max_row + 1):
                row = sheet[row_idx]

                def cell_value(col_key: str) -> str:
                    col = col_map.get(col_key)
                    if not col:
                        return ""
                    if col - 1 >= len(row):
                        return ""
                    value = row[col - 1].value
                    return "" if value is None else str(value).strip()

                file_val = cell_value("file")
                if not file_val:
                    continue

                line_val = cell_value("line")
                rule_val = cell_value("rule")
                desc_val = cell_value("desc")

                record = ViolationRecord(
                    file=file_val,
                    line=parse_line_number(line_val),
                    rule=rule_val,
                    description=desc_val,
                    source=str(file_path),
                    sheet=sheet.title,
                    row=row_idx,
                )
                records.append(record)

    return records


def collect_local_records(path: Path) -> List[ViolationRecord]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records: List[ViolationRecord] = []
    for file_result in data.get("files", []):
        file_path = file_result.get("file", "")
        for v in file_result.get("violations", []):
            records.append(
                ViolationRecord(
                    file=file_path,
                    line=v.get("line"),
                    rule=v.get("rule", ""),
                    description=v.get("description", ""),
                    source="local_json",
                )
            )
    return records


def match_file_candidates(
    local_path: str, uwa_paths: Dict[str, List[ViolationRecord]], mode: str
) -> List[str]:
    local_norm = normalize_path(local_path)
    local_base = os.path.basename(local_norm)

    candidates = []
    for uwa_path in uwa_paths.keys():
        uwa_norm = normalize_path(uwa_path)
        uwa_base = os.path.basename(uwa_norm)

        if mode == "exact":
            if local_norm == uwa_norm:
                candidates.append(uwa_path)
        elif mode == "basename":
            if local_base and local_base == uwa_base:
                candidates.append(uwa_path)
        else:
            if local_norm and (uwa_norm.endswith(local_norm) or local_norm.endswith(uwa_norm)):
                candidates.append(uwa_path)
            elif local_base and local_base == uwa_base:
                candidates.append(uwa_path)

    return candidates


def is_desc_match(local_desc: str, uwa_desc: str) -> bool:
    local_norm = normalize_desc(local_desc)
    uwa_norm = normalize_desc(uwa_desc)
    if not local_norm or not uwa_norm:
        return True
    return local_norm in uwa_norm or uwa_norm in local_norm


def build_output_csv(path: Path, rows: List[Dict[str, str]]):
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="对比UWA Excel结果和本地JSON结果")
    parser.add_argument("--uwa-dir", help="UWA Excel目录")
    parser.add_argument("--uwa-files", nargs="*", help="UWA Excel文件列表")
    parser.add_argument("--local-json", required=True, help="本地检测JSON路径")
    parser.add_argument("-o", "--output", default="uwa_compare_result.json", help="输出JSON文件")
    parser.add_argument("--line-tolerance", type=int, default=3, help="行号容差(默认3)")
    parser.add_argument("--match-by", choices=["rule", "rule+desc"], default="rule")
    parser.add_argument("--rule-map", help="规则映射JSON文件")
    parser.add_argument("--file-match", choices=["exact", "suffix", "basename"], default="suffix")
    parser.add_argument("--sheet", help="只读取指定sheet")
    parser.add_argument("--max-header-scan", type=int, default=10)
    parser.add_argument("--header-row", type=int, help="固定表头行(1-based)")
    parser.add_argument("--file-col", type=int, help="文件列(1-based)")
    parser.add_argument("--line-col", type=int, help="行号列(1-based)")
    parser.add_argument("--rule-col", type=int, help="规则列(1-based)")
    parser.add_argument("--desc-col", type=int, help="描述列(1-based)")
    parser.add_argument("--export-csv", action="store_true", help="额外导出CSV")

    args = parser.parse_args()

    uwa_files: List[Path] = []
    if args.uwa_files:
        uwa_files.extend(Path(p) for p in args.uwa_files)
    if args.uwa_dir:
        for root, _, files in os.walk(args.uwa_dir):
            for name in files:
                if name.lower().endswith((".xlsx", ".xlsm")):
                    uwa_files.append(Path(root) / name)

    if not uwa_files:
        print("未找到UWA Excel文件")
        return

    column_override = {
        "file": args.file_col,
        "line": args.line_col,
        "rule": args.rule_col,
        "desc": args.desc_col,
    }

    rule_map = load_rule_map(args.rule_map)

    uwa_records = collect_uwa_records(
        uwa_files,
        args.header_row,
        column_override,
        args.sheet,
        args.max_header_scan,
    )
    local_records = collect_local_records(Path(args.local_json))

    uwa_by_file: Dict[str, List[ViolationRecord]] = {}
    for record in uwa_records:
        uwa_by_file.setdefault(record.file, []).append(record)

    uwa_matched = set()
    matches = []
    only_local = []
    ambiguous = []

    for local in local_records:
        local_rule = normalize_rule(local.rule)
        if local_rule in rule_map:
            local_rule = rule_map[local_rule]

        candidates = match_file_candidates(local.file, uwa_by_file, args.file_match)

        if len(candidates) > 1:
            ambiguous.append(
                {
                    "file": local.file,
                    "rule": local.rule,
                    "line": local.line,
                    "candidates": candidates,
                }
            )

        best_match = None
        best_diff = None
        best_uwa = None

        for candidate_file in candidates:
            for uwa in uwa_by_file.get(candidate_file, []):
                uwa_rule = normalize_rule(uwa.rule)
                if uwa_rule in rule_map:
                    uwa_rule = rule_map[uwa_rule]

                if local_rule and uwa_rule and local_rule != uwa_rule:
                    continue

                if args.match_by == "rule+desc" and not is_desc_match(
                    local.description, uwa.description
                ):
                    continue

                if local.line is None or uwa.line is None:
                    continue

                diff = abs(local.line - uwa.line)
                if diff <= args.line_tolerance:
                    if best_diff is None or diff < best_diff:
                        best_diff = diff
                        best_match = candidate_file
                        best_uwa = uwa

        if best_uwa:
            uwa_matched.add(id(best_uwa))
            matches.append(
                {
                    "file": local.file,
                    "rule": local.rule,
                    "local_line": local.line,
                    "uwa_line": best_uwa.line,
                    "line_diff": best_diff,
                    "uwa_file": best_match,
                    "uwa_rule": best_uwa.rule,
                }
            )
        else:
            only_local.append(
                {
                    "file": local.file,
                    "rule": local.rule,
                    "line": local.line,
                    "description": local.description,
                }
            )

    only_uwa = []
    for record in uwa_records:
        if id(record) not in uwa_matched:
            only_uwa.append(
                {
                    "file": record.file,
                    "rule": record.rule,
                    "line": record.line,
                    "description": record.description,
                    "source": record.source,
                    "sheet": record.sheet,
                    "row": record.row,
                }
            )

    output = {
        "summary": {
            "local_total": len(local_records),
            "uwa_total": len(uwa_records),
            "matched": len(matches),
            "only_local": len(only_local),
            "only_uwa": len(only_uwa),
            "ambiguous": len(ambiguous),
        },
        "matches": matches,
        "only_local": only_local,
        "only_uwa": only_uwa,
        "ambiguous": ambiguous,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    if args.export_csv:
        base = Path(args.output).with_suffix("")
        build_output_csv(base.with_name(base.name + "_matches.csv"), matches)
        build_output_csv(base.with_name(base.name + "_only_local.csv"), only_local)
        build_output_csv(base.with_name(base.name + "_only_uwa.csv"), only_uwa)

    print(f"对比完成，结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
