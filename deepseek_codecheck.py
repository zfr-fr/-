#!/usr/bin/env python3
import argparse
import ast
import datetime as dt
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_CODE_EXTS = {
    ".cs",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".go",
}

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "bin",
    "obj",
    "venv",
    ".venv",
}

C_STYLE_EXTS = {
    ".cs",
    ".js",
    ".ts",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".go",
}

PY_EXTS = {".py"}

UNITY_IMPLICIT_USED = {
    "Awake",
    "Start",
    "Update",
    "LateUpdate",
    "FixedUpdate",
    "OnEnable",
    "OnDisable",
    "OnDestroy",
    "OnApplicationQuit",
    "OnApplicationPause",
    "OnApplicationFocus",
    "OnGUI",
    "OnValidate",
    "Reset",
    "OnTriggerEnter",
    "OnTriggerExit",
    "OnCollisionEnter",
    "OnCollisionExit",
}

GENERIC_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "case",
    "catch",
    "return",
    "new",
    "throw",
    "sizeof",
    "typeof",
    "lock",
    "using",
    "foreach",
    "do",
    "else",
    "try",
    "class",
    "struct",
    "interface",
    "enum",
    "def",
    "lambda",
}

SYSTEM_PROMPT = (
    "You are a code style checker. Analyze the provided source files strictly "
    "against the given rules and examples. Ignore any code that is commented out. "
    "Ignore unused code that is not called anywhere in the provided files, "
    "except for known framework entry points. Use the provided line numbers "
    "to report exact locations. Return ONLY valid JSON using this schema:\n"
    "{\n"
    '  "check_time": "YYYY-MM-DD HH:MM:SS",\n'
    '  "total_violations": 0,\n'
    '  "total_files": 0,\n'
    '  "files": [\n'
    "    {\n"
    '      "file": "path",\n'
    '      "violations_count": 0,\n'
    '      "violations": [\n'
    "        {\n"
    '          "line": 1,\n'
    '          "line_content": "code line",\n'
    '          "rule": "RULE_ID",\n'
    '          "description": "human readable reason",\n'
    '          "Author": "",\n'
    '          "issue": "",\n'
    '          "commit_time": ""\n'
    "        }\n"
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "Do not add any extra keys or explanations."
)


@dataclass
class SourceFile:
    path: str
    cleaned_lines: List[str]
    numbered_text: str


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def is_hidden_path(path: str) -> bool:
    return any(part.startswith(".") for part in path.split(os.sep) if part)


def should_exclude(path: str, exclude_globs: Sequence[str]) -> bool:
    for pattern in exclude_globs:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern):
            return True
    return False


def collect_files(
    inputs: Sequence[str],
    include_exts: Optional[Set[str]],
    exclude_dirs: Set[str],
    exclude_globs: Sequence[str],
    max_bytes: int,
) -> List[str]:
    results: List[str] = []
    for item in inputs:
        if not item:
            continue
        abs_path = os.path.abspath(item)
        if os.path.isfile(abs_path):
            if should_exclude(abs_path, exclude_globs):
                continue
            if include_exts and os.path.splitext(abs_path)[1].lower() not in include_exts:
                continue
            if os.path.getsize(abs_path) > max_bytes:
                continue
            results.append(abs_path)
        elif os.path.isdir(abs_path):
            for root, dirs, files in os.walk(abs_path):
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in exclude_dirs and not d.startswith(".")
                ]
                for name in files:
                    full_path = os.path.join(root, name)
                    if is_hidden_path(full_path):
                        continue
                    if should_exclude(full_path, exclude_globs):
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if include_exts and ext not in include_exts:
                        continue
                    if os.path.getsize(full_path) > max_bytes:
                        continue
                    results.append(full_path)
        else:
            raise FileNotFoundError(f"Path not found: {item}")
    results = sorted(set(results))
    return results


def strip_c_like_comments(text: str) -> List[str]:
    out: List[str] = []
    i = 0
    n = len(text)
    in_block = False
    in_line = False
    in_single = False
    in_double = False
    in_verbatim = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if in_line:
            if ch == "\n":
                in_line = False
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue

        if in_block:
            if ch == "*" and nxt == "/":
                out.append(" ")
                out.append(" ")
                i += 2
                in_block = False
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
            continue

        if in_single:
            out.append(ch)
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            out.append(ch)
            if in_verbatim:
                if ch == '"' and nxt == '"':
                    out.append(nxt)
                    i += 2
                    continue
                if ch == '"':
                    in_double = False
                    in_verbatim = False
                i += 1
                continue
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line = True
            out.append(" ")
            out.append(" ")
            i += 2
            continue

        if ch == "/" and nxt == "*":
            in_block = True
            out.append(" ")
            out.append(" ")
            i += 2
            continue

        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue

        if ch == '"':
            if i > 0 and text[i - 1] == "@":
                in_verbatim = True
            in_double = True
            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out).splitlines()


def strip_python_comments(text: str) -> List[str]:
    out: List[str] = []
    i = 0
    n = len(text)
    in_triple_single = False
    in_triple_double = False
    in_single = False
    in_double = False
    while i < n:
        ch = text[i]
        triple = text[i : i + 3]

        if in_triple_single:
            if triple == "'''":
                out.extend("   ")
                i += 3
                in_triple_single = False
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        if in_triple_double:
            if triple == '"""':
                out.extend('   ')
                i += 3
                in_triple_double = False
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        if in_single:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if triple == "'''":
            in_triple_single = True
            out.extend("   ")
            i += 3
            continue
        if triple == '"""':
            in_triple_double = True
            out.extend('   ')
            i += 3
            continue
        if ch == "#":
            out.append(" ")
            i += 1
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "'":
            in_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out).splitlines()


def strip_comments(text: str, ext: str) -> List[str]:
    if ext in C_STYLE_EXTS:
        return strip_c_like_comments(text)
    if ext in PY_EXTS:
        return strip_python_comments(text)
    return text.splitlines()


def extract_python_calls(text: str) -> Set[str]:
    calls: Set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
    return calls


def extract_generic_calls(lines: Sequence[str]) -> Set[str]:
    calls: Set[str] = set()
    call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for line in lines:
        for match in call_re.finditer(line):
            name = match.group(1)
            if name in GENERIC_KEYWORDS:
                continue
            calls.add(name)
    return calls


def extract_csharp_calls(lines: Sequence[str]) -> Set[str]:
    methods = find_csharp_methods(lines)
    signature_lines = {start for _, start, _, _ in methods}
    call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    calls: Set[str] = set()
    for idx, line in enumerate(lines):
        if idx in signature_lines:
            continue
        for match in call_re.finditer(line):
            name = match.group(1)
            if name in GENERIC_KEYWORDS:
                continue
            calls.add(name)
    return calls


def find_csharp_methods(lines: Sequence[str]) -> List[Tuple[str, int, int, int]]:
    methods: List[Tuple[str, int, int, int]] = []
    method_re = re.compile(
        r"^\s*(?:public|private|protected|internal|static|virtual|override|sealed|"
        r"async|extern|new|partial|\s)+\s*[\w<>\[\],]+\s+(\w+)\s*\("
    )
    ctor_re = re.compile(
        r"^\s*(?:public|private|protected|internal|static|partial|\s)+\s*(\w+)\s*\("
    )
    control_re = re.compile(r"^\s*(if|for|while|switch|catch|foreach|using|lock)\b")

    i = 0
    while i < len(lines):
        line = lines[i]
        if control_re.search(line):
            i += 1
            continue
        name = None
        match = method_re.search(line)
        if match:
            name = match.group(1)
        else:
            match = ctor_re.search(line)
            if match:
                name = match.group(1)
        if not name:
            i += 1
            continue

        brace_line = None
        j = i
        while j < len(lines):
            if "{" in lines[j]:
                brace_line = j
                break
            if ";" in lines[j]:
                break
            j += 1
        if brace_line is None:
            i += 1
            continue
        end_line = find_matching_brace(lines, brace_line)
        methods.append((name, i, brace_line, end_line))
        i = end_line + 1
    return methods


def find_matching_brace(lines: Sequence[str], start_line: int) -> int:
    depth = 0
    for idx in range(start_line, len(lines)):
        for ch in lines[idx]:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return idx
    return start_line


def mask_unused_python(lines: List[str], raw_text: str, all_calls: Set[str]) -> List[str]:
    try:
        tree = ast.parse(raw_text)
    except SyntaxError:
        return lines
    masked = lines[:]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in all_calls:
                continue
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            start = max(node.lineno - 1, 0)
            end = max(getattr(node, "end_lineno", node.lineno) - 1, start)
            for idx in range(start + 1, end):
                masked[idx] = ""
    return masked


def mask_unused_csharp(lines: List[str], all_calls: Set[str]) -> List[str]:
    masked = lines[:]
    methods = find_csharp_methods(lines)
    for name, _, brace_line, end_line in methods:
        if name in all_calls or name in UNITY_IMPLICIT_USED:
            continue
        if end_line <= brace_line:
            continue
        for idx in range(brace_line + 1, end_line):
            masked[idx] = ""
    return masked


def number_lines(lines: Sequence[str]) -> str:
    output_lines: List[str] = []
    for idx, line in enumerate(lines, start=1):
        if line.strip() == "":
            continue
        output_lines.append(f"{idx}|{line}")
    return "\n".join(output_lines)


def build_prompt(rules: str, examples: str, files: Sequence[SourceFile]) -> str:
    parts: List[str] = []
    parts.append("Rules:")
    parts.append(rules.strip() if rules.strip() else "(none)")
    parts.append("")
    parts.append("Examples:")
    parts.append(examples.strip() if examples.strip() else "(none)")
    parts.append("")
    parts.append("Files:")
    for entry in files:
        parts.append(f"\nFILE: {entry.path}")
        parts.append(entry.numbered_text if entry.numbered_text else "(empty)")
    return "\n".join(parts)


def call_deepseek(
    api_key: str,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    url = "https://api.deepseek.com/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    response = json.loads(body)
    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("API response missing choices.")
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("API response content is empty.")
    return content


def parse_json_response(content: str) -> Dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(content[start : end + 1])


def normalize_result(parsed: Dict, file_paths: Sequence[str]) -> Dict:
    files_out: List[Dict] = []
    file_map: Dict[str, Dict] = {}
    for entry in parsed.get("files", []):
        if isinstance(entry, dict) and entry.get("file"):
            file_map[entry["file"]] = entry

    for path in file_paths:
        item = file_map.get(path, {"file": path, "violations": []})
        violations = item.get("violations", [])
        if not isinstance(violations, list):
            violations = []
        normalized_violations = []
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            line_value = violation.get("line", 0)
            try:
                line_value = int(line_value)
            except (TypeError, ValueError):
                line_value = 0
            normalized_violations.append(
                {
                    "line": line_value,
                    "line_content": violation.get("line_content", ""),
                    "rule": violation.get("rule", ""),
                    "description": violation.get("description", ""),
                    "Author": violation.get("Author", ""),
                    "issue": violation.get("issue", ""),
                    "commit_time": violation.get("commit_time", ""),
                }
            )
        item["violations"] = normalized_violations
        item["violations_count"] = len(normalized_violations)
        files_out.append(item)

    total_violations = sum(f.get("violations_count", 0) for f in files_out)
    result = {
        "check_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_violations": total_violations,
        "total_files": len(files_out),
        "files": files_out,
    }
    return result


def build_sources(
    file_paths: Sequence[str],
    implicit_used: Set[str],
) -> List[SourceFile]:
    sources: List[SourceFile] = []
    cleaned_per_file: Dict[str, List[str]] = {}
    raw_per_file: Dict[str, str] = {}
    all_calls: Set[str] = set(implicit_used)

    for path in file_paths:
        text = read_text_file(path)
        raw_per_file[path] = text
        ext = os.path.splitext(path)[1].lower()
        cleaned_lines = strip_comments(text, ext)
        cleaned_per_file[path] = cleaned_lines

        if ext in PY_EXTS:
            all_calls.update(extract_python_calls(text))
        elif ext == ".cs":
            all_calls.update(extract_csharp_calls(cleaned_lines))
        else:
            all_calls.update(extract_generic_calls(cleaned_lines))

    for path in file_paths:
        ext = os.path.splitext(path)[1].lower()
        cleaned_lines = cleaned_per_file[path]
        if ext in PY_EXTS:
            cleaned_lines = mask_unused_python(
                cleaned_lines, raw_per_file[path], all_calls
            )
        elif ext == ".cs":
            cleaned_lines = mask_unused_csharp(cleaned_lines, all_calls)
        numbered_text = number_lines(cleaned_lines)
        sources.append(
            SourceFile(
                path=path,
                cleaned_lines=cleaned_lines,
                numbered_text=numbered_text,
            )
        )
    return sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check code style violations with DeepSeek API."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="File or directory paths to scan.",
    )
    parser.add_argument(
        "--rules",
        default="",
        help="Rules text or path to a rules file.",
    )
    parser.add_argument(
        "--examples",
        default="",
        help="Examples text or path to an examples file.",
    )
    parser.add_argument(
        "--include-ext",
        default="",
        help="Comma-separated list of file extensions to include.",
    )
    parser.add_argument(
        "--exclude",
        default="",
        help="Comma-separated glob patterns to exclude.",
    )
    parser.add_argument(
        "--model",
        default="deepseek-chat",
        help="DeepSeek model name.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="Sampling temperature.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2000,
        help="Max tokens in API response.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=300_000,
        help="Skip files larger than this size in bytes.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Number of files per API request.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path. If empty, print to stdout.",
    )
    parser.add_argument(
        "--implicit-used",
        default="",
        help="Comma-separated function/method names to treat as used.",
    )
    return parser.parse_args()


def load_text_argument(value: str) -> str:
    if not value:
        return ""
    if os.path.isfile(value):
        return read_text_file(value)
    return value


def chunked(items: Sequence[str], size: int) -> Iterable[List[str]]:
    if size <= 0:
        size = len(items) or 1
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def main() -> int:
    args = parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("Missing DEEPSEEK_API_KEY environment variable.", file=sys.stderr)
        return 2

    include_exts = None
    if args.include_ext:
        include_exts = {
            ext if ext.startswith(".") else f".{ext}"
            for ext in args.include_ext.split(",")
            if ext.strip()
        }
    else:
        include_exts = set(DEFAULT_CODE_EXTS)

    exclude_globs = [p.strip() for p in args.exclude.split(",") if p.strip()]
    implicit_used = {
        name.strip()
        for name in args.implicit_used.split(",")
        if name.strip()
    }
    implicit_used.update(UNITY_IMPLICIT_USED)

    file_paths = collect_files(
        inputs=args.paths,
        include_exts=include_exts,
        exclude_dirs=DEFAULT_EXCLUDE_DIRS,
        exclude_globs=exclude_globs,
        max_bytes=args.max_bytes,
    )
    if not file_paths:
        print("No files found to scan.", file=sys.stderr)
        return 1

    rules_text = load_text_argument(args.rules)
    examples_text = load_text_argument(args.examples)

    all_results: List[Dict] = []
    for batch in chunked(file_paths, args.batch_size):
        sources = build_sources(batch, implicit_used)
        prompt = build_prompt(rules_text, examples_text, sources)
        content = call_deepseek(
            api_key=api_key,
            model=args.model,
            prompt=prompt,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        )
        parsed = parse_json_response(content)
        normalized = normalize_result(parsed, batch)
        all_results.append(normalized)

    merged_files: List[Dict] = []
    for result in all_results:
        merged_files.extend(result.get("files", []))
    final_result = {
        "check_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_violations": sum(f.get("violations_count", 0) for f in merged_files),
        "total_files": len(merged_files),
        "files": merged_files,
    }

    output_text = json.dumps(final_result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output_text)
        print(f"Wrote results to {args.output}")
    else:
        print(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
