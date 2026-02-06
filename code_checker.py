import os
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import re
import hashlib
import time
import pickle

try:
    import requests
except ImportError:
    print("请安装requests库: pip install requests")
    exit(1)


class ViolationType(Enum):
    DEBUG_LOG_USAGE = "DEBUG_LOG_USAGE"
    EMPTY_UPDATE_METHOD = "EMPTY_UPDATE_METHOD"


@dataclass
class Violation:
    line: int
    line_content: str
    rule: str
    description: str
    author: str = ""
    issue: str = ""
    commit_time: str = ""


@dataclass
class FileResult:
    file: str
    violations_count: int
    violations: List[Violation]


@dataclass
class CheckResult:
    check_time: str
    total_violations: int
    total_files: int
    files: List[FileResult]


class GitInfoExtractor:
    REDMINE_URL_PREFIX = "http://soc-redmine.wd.com/issues/"

    @staticmethod
    def is_git_repository(path: Path) -> bool:
        try:
            current_path = path.resolve()
            while current_path.parent != current_path:
                git_dir = current_path / ".git"
                if git_dir.exists():
                    return True
                current_path = current_path.parent
            return False
        except:
            return False

    @staticmethod
    def run_git_command(cmd: List[str], cwd: str) -> Optional[str]:
        """运行Git命令并返回结果"""
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                cwd=cwd,
                env=env,
            )

            if result.returncode != 0:
                return None

            return result.stdout
        except Exception as e:
            print(f"执行Git命令失败: {e}")
            return None

    @staticmethod
    def extract_issue_from_commit_message(commit_hash: str, file_dir: str) -> str:
        """从提交信息中提取issue编号和描述"""
        try:
            cmd = ["git", "show", "--quiet", "--pretty=format:%s", commit_hash]
            commit_msg = GitInfoExtractor.run_git_command(cmd, file_dir)

            if not commit_msg:
                return commit_hash[:8]

            redmine_pattern = r"#(\d+)\s+(.+)"
            match = re.search(redmine_pattern, commit_msg)

            if match:
                issue_num = match.group(1)
                issue_desc = match.group(2)
                return f"#{issue_num} {issue_desc}"
            return commit_msg[:50] + ("..." if len(commit_msg) > 50 else "")

        except Exception as e:
            print(f"提取issue信息失败: {e}")
            return commit_hash[:8]

    @staticmethod
    def get_git_blame_info(file_path: Path, line_number: int) -> Tuple[str, str, str]:
        """
        获取指定行的Git信息

        Args:
            file_path: 文件路径
            line_number: 行号

        Returns:
            (作者, 提交时间, 完整的issue信息)
        """
        try:
            file_dir = str(file_path.parent) or "."

            # 首先检查当前目录是否是git仓库
            check_cmd = ["git", "rev-parse", "--is-inside-work-tree"]
            if not GitInfoExtractor.run_git_command(check_cmd, file_dir):
                return "", "", ""

            # 使用git blame获取行信息
            cmd = [
                "git",
                "blame",
                "-L",
                f"{line_number},{line_number}",
                "--porcelain",
                file_path.name,
            ]

            output = GitInfoExtractor.run_git_command(cmd, file_dir)

            if not output:
                return "", "", ""

            lines = output.strip().split("\n")

            if not lines:
                return "", "", ""

            # 解析git blame输出
            first_line_parts = lines[0].split()
            if len(first_line_parts) < 1:
                return "", "", ""

            commit_hash = first_line_parts[0]

            # 提取信息
            author = None
            author_time = None
            summary = None

            for line in lines[1:]:
                if line.startswith("author "):
                    author = line[7:]
                elif line.startswith("author-time "):
                    author_time = line[12:]
                elif line.startswith("summary "):
                    summary = line[8:]

            if author and author_time:
                try:
                    timestamp = int(author_time)
                    commit_date = datetime.fromtimestamp(timestamp).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                except (ValueError, TypeError):
                    commit_date = ""

                # 提取issue信息
                issue_info = GitInfoExtractor.extract_issue_from_commit_message(
                    commit_hash, file_dir
                )

                # 格式化issue信息
                if issue_info.startswith("#"):
                    issue_match = re.search(r"#(\d+)", issue_info)
                    if issue_match:
                        issue_num = issue_match.group(1)
                        issue_desc = issue_info.replace(f"#{issue_num}", "").strip()
                        issue_info = (
                            f"#{issue_num} {issue_desc} "
                            f"{GitInfoExtractor.REDMINE_URL_PREFIX}{issue_num}"
                        )

                return author, commit_date, issue_info

            return "", "", ""

        except Exception as e:
            print(f"获取Git信息失败: {e}")
            return "", "", ""


class CodeChecker:
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        enable_git_info: bool = True,
        redmine_url: str = None,
        use_cache: bool = True,
        cache_dir: str = ".code_check_cache",
        rate_limit_delay: float = 0.1,
    ):
        self.api_key = api_key
        self.model = model
        self.enable_git_info = enable_git_info
        self.api_url = "https://api.deepseek.com/v1/chat/completions"

        if redmine_url:
            GitInfoExtractor.REDMINE_URL_PREFIX = redmine_url

        self.git_extractor = GitInfoExtractor()

        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )

        # 优化参数
        self.use_cache = use_cache
        self.cache_dir = Path(cache_dir)
        self.rate_limit_delay = rate_limit_delay
        # 缓存版本，用于规则变更后强制失效
        self.cache_version = "v2"

        # 初始化缓存
        if self.use_cache:
            self.cache_dir.mkdir(exist_ok=True)
            self.cache_file = self.cache_dir / "file_cache.pkl"
            self._load_cache()

    def _load_cache(self):
        """加载缓存"""
        self.cache = {}
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "rb") as f:
                    self.cache = pickle.load(f)
            except:
                self.cache = {}

    def _save_cache(self):
        """保存缓存"""
        if self.use_cache:
            try:
                with open(self.cache_file, "wb") as f:
                    pickle.dump(self.cache, f)
            except:
                pass

    def _get_file_hash(self, file_path: Path) -> str:
        """计算文件哈希（基于内容和修改时间）"""
        try:
            stat = file_path.stat()
            content_hash = hashlib.md5()
            with open(file_path, "rb") as f:
                content_hash.update(f.read())
            # 结合修改时间
            return f"{self.cache_version}_{content_hash.hexdigest()}_{stat.st_mtime}"
        except:
            return ""

    def _check_from_cache(self, file_path: Path) -> Optional[FileResult]:
        """从缓存中获取检查结果"""
        if not self.use_cache:
            return None

        file_hash = self._get_file_hash(file_path)
        if not file_hash:
            return None

        cache_key = str(file_path)
        if cache_key in self.cache:
            cached_hash, cached_result = self.cache[cache_key]
            if cached_hash == file_hash:
                print(f"使用缓存: {file_path}")
                return cached_result
        return None

    def _save_to_cache(self, file_path: Path, result: FileResult):
        """保存结果到缓存"""
        if not self.use_cache:
            return

        file_hash = self._get_file_hash(file_path)
        if not file_hash:
            return

        cache_key = str(file_path)
        self.cache[cache_key] = (file_hash, result)
        self._save_cache()

    def _build_rules(self) -> str:
        """优化版规则描述，明确区分Debug.Log和logger.InfoFormat"""
        rules_short = """请检查以下C#代码是否违反以下规则，使用原始行号进行报告：

        1. DEBUG_LOG_USAGE - 使用Debug.Log/LogError/LogException等UnityEngine.Debug类的Log函数的调用属于违规，SocLogger的日志调用不违规
        2. NEW_IN_UPDATE_METHOD - 在Update/LateUpdate/FixedUpdate中new引用类型
        3. EMPTY_UPDATE_METHOD - 继承自MonoBehaviour的类中存在空的Update/LateUpdate/FixedUpdate方法
        4. FIND_FUNCTION_CALLS - 仅限以下Find函数调用：
           GameObject.Find / FindGameObjectWithTag / FindGameObjectsWithTag / FindWithTag，
           Transform.Find（包含transform.Find），
           Object.FindObjectOfType / FindObjectsOfType（包含泛型版本），
           Resources.FindObjectsOfTypeAll
           其他函数不属于Find类函数（例如：GetComponent/GetComponents、Object.Destroy等）
        5. FOREACH_ON_SPECIFIC_CONTAINERS - 仅当foreach遍历以下容器类型时违规：
           SortedDictionary / Hashtable / BitArray / Queue / SortedList / ArrayList / Stack
           其他容器（如 List/Dictionary/HashSet 等）不属于违规
        6. GET_COMPONENTS_IN_CHILDREN - 仅GetComponentsInChildren调用（不包含GetComponent/GetComponentInChildren）
        7. GET_COMPONENTS_IN_PARENT - 仅GetComponentsInParent调用（不包含GetComponent/GetComponentInParent）
        8. COMPUTE_BUFFER_GETDATA - ComputeBuffer.GetData调用
        9. TEXTURE_GETPIXELS_CALL - Texture.GetPixels/GetPixels32调用
        10. TEXTASSET_BYTES_USAGE - TextAsset.bytes调用
        11. CAMERA_MAIN_USAGE - Camera.main调用
        12. RENDERER_MATERIAL_GETTER - Renderer.material/materials调用
        13. RENDERER_SHAREDMATERIALS_GETTER - Renderer.sharedMaterials调用
        14. ONGUI_METHOD_PRESENT - OnGUI方法存在
        15. SENDMESSAGE_USAGE - GameObject.SendMessage调用
        16. TEXTURE_SETPIXELS_CALL - Texture.SetPixels调用
        17. HEAP_ALLOCATING_STRING_OPS - 堆内存分配的字符串操作
        18. TAG_PROPERTY_USAGE - .tag属性调用
        19. LINQ_USAGE - Linq函数调用
        20. REFLECTION_USAGE - 反射函数调用
        21. INPUT_TOUCHES_USAGE - Input.touches调用
        22. HEAPLESS_ALTERNATIVE_AVAILABLE - 可用无堆内存分配版本替代

        **重要**：请使用原始行号（每行前面的数字）进行报告，不要使用描述中的行号！
        **重要**：Debug.Log相关规则只适用于Unity的Debug类，不适用于自定义的logger类！

        返回JSON格式：{"violations": [{"line": 1, "line_content": "原始代码内容", "rule": "RULE_NAME", "description": "违规描述"}]}
        如果没有违规，返回：{"violations": []}"""
        return rules_short

    def _parse_editor_condition(self, condition: str) -> Optional[bool]:
        """
        解析UNITY_EDITOR条件

        Returns:
            True  -> 该分支是编辑器代码，需要移除
            False -> 该分支是非编辑器代码，需要保留
            None  -> 条件不包含UNITY_EDITOR
        """
        if "UNITY_EDITOR" not in condition:
            return None

        if re.search(r"!\s*UNITY_EDITOR", condition):
            return False
        if re.search(r"UNITY_EDITOR\s*==\s*false", condition, re.IGNORECASE):
            return False
        if re.search(r"UNITY_EDITOR\s*==\s*0", condition):
            return False
        if re.search(r"UNITY_EDITOR\s*!=\s*true", condition, re.IGNORECASE):
            return False
        if re.search(r"UNITY_EDITOR\s*!=\s*1", condition):
            return False

        return True

    def _remove_editor_code(self, code: str) -> str:
        """
        移除编辑器宏包裹的代码，同时用空行占位保持行号不变
        """
        lines = code.split("\n")
        result_lines: List[str] = []

        conditional_stack: List[Dict[str, Any]] = []

        def is_excluding() -> bool:
            return any(frame["exclude_current"] for frame in conditional_stack)

        for line in lines:
            stripped = line.strip()
            directive_match = re.match(r"#\s*(if|elif|else|endif)\b(.*)", stripped)

            if directive_match:
                directive = directive_match.group(1)
                condition = directive_match.group(2).strip()

                if directive == "if":
                    exclude_branch = self._parse_editor_condition(condition)
                    if exclude_branch is None:
                        conditional_stack.append(
                            {
                                "is_unity": False,
                                "exclude_current": False,
                                "else_exclude": False,
                            }
                        )
                        result_lines.append(line)
                    else:
                        conditional_stack.append(
                            {
                                "is_unity": True,
                                "exclude_current": exclude_branch,
                                "else_exclude": not exclude_branch,
                            }
                        )
                        result_lines.append("")
                    continue

                if directive == "elif":
                    if conditional_stack:
                        top = conditional_stack[-1]
                        if top["is_unity"]:
                            exclude_branch = self._parse_editor_condition(condition)
                            if exclude_branch is None:
                                top["exclude_current"] = False
                            else:
                                top["exclude_current"] = exclude_branch
                            result_lines.append("")
                        else:
                            result_lines.append(line)
                    else:
                        result_lines.append(line)
                    continue

                if directive == "else":
                    if conditional_stack:
                        top = conditional_stack[-1]
                        if top["is_unity"]:
                            top["exclude_current"] = top["else_exclude"]
                            result_lines.append("")
                        else:
                            result_lines.append(line)
                    else:
                        result_lines.append(line)
                    continue

                if directive == "endif":
                    if conditional_stack:
                        top = conditional_stack.pop()
                        result_lines.append("" if top["is_unity"] else line)
                    else:
                        result_lines.append(line)
                    continue

            if is_excluding():
                result_lines.append("")
            else:
                result_lines.append(line)

        return "\n".join(result_lines)

    def _prepare_code_for_ai_with_exact_line_numbers(
        self, code: str
    ) -> Tuple[str, Dict[int, str]]:
        """
        准备代码供AI检查，同时保留原始行号映射
        不再移除空行和注释，保持原始行号准确

        Returns:
            prepared_code: 供AI检查的代码（每行保留原始行号）
            line_mapping: 原始行号到代码内容的映射
        """
        # 移除编辑器代码（用空行占位，保证行号不变）
        code = self._remove_editor_code(code)

        lines = code.split("\n")
        prepared_lines = []
        line_mapping = {}

        for i, line in enumerate(lines, 1):
            line_content = line.rstrip("\n\r")
            # 保留原始行号和内容（包括空行和注释）
            line_mapping[i] = line_content

            # 为AI准备：添加行号前缀，但不修改内容
            prepared_lines.append(f"L{i:4d}: {line_content}")

        return "\n".join(prepared_lines), line_mapping

    def _extract_line_number(self, line_str: str) -> Optional[int]:
        """从字符串中提取行号"""
        try:
            # 处理多种可能的行号格式
            patterns = [
                r"L(\d+):",  # L345: 格式
                r"第(\d+)行",  # 中文格式
                r"line\s*(\d+)",  # line 155
                r"Line\s*(\d+)",  # Line 155
                r"\[Line\s*(\d+)\]",  # [Line 155]
                r"(\d+):",  # 345:
                r"\s(\d+)\s",  # 空格345空格
            ]

            for pattern in patterns:
                match = re.search(pattern, line_str, re.IGNORECASE)
                if match:
                    return int(match.group(1))

            # 如果没有匹配到模式，尝试直接提取数字
            numbers = re.findall(r"\b(\d+)\b", line_str)
            if numbers:
                return int(numbers[0])

            return None
        except:
            return None

    def _parse_api_response(
        self, response_text: str, line_mapping: Dict[int, str]
    ) -> List[Violation]:
        """解析API响应，处理行号映射"""
        try:
            # 首先尝试提取JSON
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)

                violations = []
                for v in data.get("violations", []):
                    # 获取报告的行号
                    reported_line = v.get("line", 0)
                    line_content = v.get("line_content", "")
                    rule = v.get("rule", "")
                    description = v.get("description", "")
                    candidate_content = line_content
                    if reported_line in line_mapping:
                        candidate_content = line_mapping[reported_line]

                    # 先进行误报过滤
                    if rule == "DEBUG_LOG_USAGE":
                        # 检查是否真的是Debug.Log，而不是logger.InfoFormat等
                        debug_patterns = [
                            r"Debug\.(Log|LogError|LogException|LogWarning|Assert)",
                            r"UnityEngine\.Debug\.(Log|LogError|LogException|LogWarning|Assert)",
                            r"print\(",  # Unity的print也等同于Debug.Log
                        ]

                        is_debug_log = False
                        for pattern in debug_patterns:
                            if re.search(pattern, candidate_content, re.IGNORECASE):
                                is_debug_log = True
                                break

                        # 检查是否是允许的日志格式
                        allowed_patterns = [
                            r"logger\.(Info|Error|Warning|Debug)",
                            r"Logger\.(Info|Error|Warning|Debug)",
                            r"Log\.(Info|Error|Warning|Debug)",
                            r"Console\.(WriteLine|Write)",
                            r"Trace\.(WriteLine|Write)",
                            r"System\.Diagnostics\.Debug\.(WriteLine|Write)",
                        ]

                        is_allowed_log = False
                        for pattern in allowed_patterns:
                            if re.search(pattern, candidate_content, re.IGNORECASE):
                                is_allowed_log = True
                                break

                        # 如果是允许的日志格式，跳过这个违规
                        if is_allowed_log:
                            print(f"跳过允许的日志调用: {candidate_content}")
                            continue

                        # 如果不是Debug.Log且不是允许的日志，也不应该报告为DEBUG_LOG_USAGE
                        if not is_debug_log:
                            print(f"跳过非Debug.Log调用: {candidate_content}")
                            continue

                    if rule == "FOREACH_ON_SPECIFIC_CONTAINERS":
                        if "误报" in description or "请忽略" in description:
                            print(f"跳过标记为误报的foreach: {description}")
                            continue

                        allowed_containers = [
                            "SortedDictionary",
                            "Hashtable",
                            "BitArray",
                            "Queue",
                            "SortedList",
                            "ArrayList",
                            "Stack",
                        ]

                        def contains_allowed(text: str) -> bool:
                            return any(
                                re.search(rf"\b{re.escape(name)}\b", text)
                                for name in allowed_containers
                            )

                        if not (
                            contains_allowed(candidate_content)
                            or contains_allowed(description)
                        ):
                            print(f"跳过非指定容器foreach: {candidate_content}")
                            continue

                    if rule == "FIND_FUNCTION_CALLS":
                        find_patterns = [
                            r"\bGameObject\.Find\s*\(",
                            r"\bGameObject\.FindGameObjectWithTag\s*\(",
                            r"\bGameObject\.FindGameObjectsWithTag\s*\(",
                            r"\bGameObject\.FindWithTag\s*\(",
                            r"\bTransform\.Find\s*\(",
                            r"\btransform\.Find\s*\(",
                            r"\bObject\.FindObjectOfType\s*(<|\()",
                            r"\bObject\.FindObjectsOfType\s*(<|\()",
                            r"\bFindObjectOfType\s*(<|\()",
                            r"\bFindObjectsOfType\s*(<|\()",
                            r"\bResources\.FindObjectsOfTypeAll\s*(<|\()",
                        ]

                        if not any(
                            re.search(pattern, candidate_content)
                            for pattern in find_patterns
                        ):
                            print(f"跳过非Find函数调用: {candidate_content}")
                            continue

                    if rule == "GET_COMPONENTS_IN_CHILDREN":
                        if not re.search(
                            r"\bGetComponentsInChildren\s*(<|\()",
                            candidate_content,
                        ):
                            print(f"跳过非GetComponentsInChildren调用: {candidate_content}")
                            continue

                    if rule == "GET_COMPONENTS_IN_PARENT":
                        if not re.search(
                            r"\bGetComponentsInParent\s*(<|\()",
                            candidate_content,
                        ):
                            print(f"跳过非GetComponentsInParent调用: {candidate_content}")
                            continue

                    # 验证行号是否存在映射中
                    if reported_line in line_mapping:
                        # 使用映射中的原始内容
                        actual_content = line_mapping[reported_line]

                        violation = Violation(
                            line=reported_line,
                            line_content=actual_content,
                            rule=rule,
                            description=description,
                        )
                        violations.append(violation)
                    else:
                        # 行号不在映射中，尝试从描述中提取行号
                        extracted_line = self._extract_line_number(description)

                        if extracted_line and extracted_line in line_mapping:
                            # 使用提取的行号
                            actual_content = line_mapping[extracted_line]
                            violation = Violation(
                                line=extracted_line,
                                line_content=actual_content,
                                rule=rule,
                                description=description,
                            )
                            violations.append(violation)
                        else:
                            # 尝试从line_content中提取行号
                            extracted_line = self._extract_line_number(line_content)
                            if extracted_line and extracted_line in line_mapping:
                                actual_content = line_mapping[extracted_line]
                                violation = Violation(
                                    line=extracted_line,
                                    line_content=actual_content,
                                    rule=rule,
                                    description=description,
                                )
                                violations.append(violation)
                            else:
                                # 无法确定行号，但保留违规信息（使用报告的行号）
                                print(
                                    "警告: 无法确定行号映射 for line "
                                    f"{reported_line} with content '{line_content}'"
                                )
                                violation = Violation(
                                    line=reported_line,
                                    line_content=line_content,
                                    rule=rule,
                                    description=description,
                                )
                                violations.append(violation)

                return violations

            print(f"警告: 无法从响应中提取JSON: {response_text[:200]}...")
            return []

        except json.JSONDecodeError as e:
            print(f"JSON解析错误: {e}")
            print(f"响应内容: {response_text}")
            return []

    def _enrich_violation_with_git_info(
        self, file_path: Path, violation: Violation
    ) -> Violation:
        if not self.enable_git_info:
            return violation

        if not self.git_extractor.is_git_repository(file_path):
            return violation

        author, commit_time, issue = self.git_extractor.get_git_blame_info(
            file_path, violation.line
        )

        violation.author = author
        violation.commit_time = commit_time
        violation.issue = issue

        return violation

    def _build_prompt_optimized(
        self, code: str, file_path: str, line_mapping: Dict[int, str]
    ) -> str:
        """构建优化的prompt，确保行号准确"""
        prepared_code, _ = self._prepare_code_for_ai_with_exact_line_numbers(code)

        prompt = f"""请检查以下C#代码文件({file_path})是否符合代码规范。

{self._build_rules()}

重要说明：
1. 请使用每行开头的"LXXX:"中的行号（XXX是数字）进行报告，这是原始文件的准确行号
2. 报告的行号必须是原始文件中的行号，不要使用任何偏移或计算
3. 在"line"字段中填写行号数字，在"line_content"字段中填写原始代码内容
4. 特别注意：DEBUG_LOG_USAGE规则只适用于Unity的Debug类，不适用于自定义的logger类

代码（行号格式为"L行号: 代码内容"）:
{prepared_code}

请严格按照以下JSON格式返回检测结果，确保行号准确：
{{
    "violations": [
        {{
            "line": 155,
            "line_content": "Debug.LogError(\\"错误信息\\");",
            "rule": "DEBUG_LOG_USAGE",
            "description": "使用了Debug.LogError方法输出错误日志"
        }}
    ]
}}

如果没有违规项，则返回：
{{
    "violations": []
}}"""
        return prompt

    def _should_skip_file(self, file_path: Path) -> Tuple[bool, str]:
        """
        判断是否应该跳过文件检测
        返回: (是否跳过, 跳过原因)
        """
        # 只跳过编辑器目录下的文件
        editor_paths = ["Editor", "EditorTools", "EditorWindows", "Gizmos", "EditorResources"]
        for editor_path in editor_paths:
            if editor_path in str(file_path):
                return True, f"在编辑器目录 {editor_path} 中"

        return False, ""

    def check_file(self, file_path: Path) -> FileResult:
        """检查单个文件（带缓存）"""
        # 1. 检查缓存
        cached_result = self._check_from_cache(file_path)
        if cached_result is not None:
            return cached_result

        # 2. 跳过非C#文件
        if file_path.suffix.lower() != ".cs":
            result = FileResult(file=str(file_path), violations_count=0, violations=[])
            self._save_to_cache(file_path, result)
            return result

        # 3. 检查是否需要跳过（仅编辑器目录）
        should_skip, reason = self._should_skip_file(file_path)
        if should_skip:
            print(f"跳过文件 {file_path}: {reason}")
            result = FileResult(file=str(file_path), violations_count=0, violations=[])
            self._save_to_cache(file_path, result)
            return result

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()

            # 准备代码和行号映射（不清理代码，保持原始行号）
            prepared_code, line_mapping = self._prepare_code_for_ai_with_exact_line_numbers(
                code
            )

            prompt = self._build_prompt_optimized(code, str(file_path), line_mapping)

            data = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": """你是代码规范检查工具，检查C#代码并返回JSON结果。请确保：
1. 报告的行号是原始文件中的准确行号
2. DEBUG_LOG_USAGE规则只适用于Unity的Debug类（Debug.Log, Debug.LogError等）
3. 不要将logger.InfoFormat, logger.ErrorFormat等误报为DEBUG_LOG_USAGE
4. FIND_FUNCTION_CALLS仅限指定的Find函数，不包括GetComponent/GetComponents或Object.Destroy
5. GET_COMPONENTS_IN_CHILDREN/GET_COMPONENTS_IN_PARENT仅限对应函数名调用
6. FOREACH_ON_SPECIFIC_CONTAINERS仅限指定容器类型，List等不违规
7. 行号必须与代码中"LXXX:"格式的行号完全一致""",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "top_p": 1,
                "seed": 42,
                "max_tokens": 4000,
            }

            # 添加速率限制延迟
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

            response = self.session.post(self.api_url, json=data)

            violations = []

            if response.status_code == 200:
                result_data = response.json()
                content = result_data["choices"][0]["message"]["content"]

                # 解析响应，使用行号映射
                violations = self._parse_api_response(content, line_mapping)
            else:
                print(f"API请求失败: {response.status_code}, {response.text}")
                # 请求失败时不缓存结果
                return FileResult(file=str(file_path), violations_count=0, violations=[])

            # 排序并丰富Git信息
            violations.sort(key=lambda v: v.line)

            enriched_violations = []
            for violation in violations:
                enriched_violation = self._enrich_violation_with_git_info(
                    file_path, violation
                )
                enriched_violations.append(enriched_violation)

            file_result = FileResult(
                file=str(file_path),
                violations_count=len(enriched_violations),
                violations=enriched_violations,
            )

            # 保存到缓存
            self._save_to_cache(file_path, file_result)

            return file_result

        except FileNotFoundError:
            print(f"文件不存在: {file_path}")
            # 文件读取失败时不缓存
            return FileResult(file=str(file_path), violations_count=0, violations=[])
        except Exception as e:
            print(f"检查文件 {file_path} 时出错: {e}")
            # 其他异常时不缓存
            return FileResult(file=str(file_path), violations_count=0, violations=[])

    def check_directory(self, dir_path: Path) -> CheckResult:
        """检查目录"""
        cs_files = list(dir_path.rglob("*.cs"))

        if not cs_files:
            print(f"在目录 {dir_path} 中没有找到.cs文件")
            return CheckResult(
                check_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total_violations=0,
                total_files=0,
                files=[],
            )

        # 过滤文件
        filtered_files = []
        skipped_files = []
        cached_files = []

        for file_path in cs_files:
            # 检查缓存
            cached_result = self._check_from_cache(file_path)
            if cached_result is not None:
                cached_files.append(cached_result)
                continue

            # 检查是否需要跳过（仅编辑器目录）
            should_skip, reason = self._should_skip_file(file_path)
            if should_skip:
                skipped_files.append((str(file_path), reason))
                continue

            filtered_files.append(file_path)

        print(
            "总文件数: "
            f"{len(cs_files)}, 跳过: {len(skipped_files)}, 缓存命中: "
            f"{len(cached_files)}, 需要检查: {len(filtered_files)}"
        )

        # 检查需要处理的文件
        files_results = []
        total_violations = 0

        for i, file_path in enumerate(filtered_files, 1):
            print(f"正在检查文件 ({i}/{len(filtered_files)}): {file_path}")

            file_result = self.check_file(file_path)

            # 只收集有违规的文件结果
            if file_result.violations_count > 0:
                files_results.append(file_result)
                total_violations += file_result.violations_count
                print(f"  发现 {file_result.violations_count} 个违规")
            else:
                print("  无违规")

        # 添加缓存中有违规的结果
        for cached_result in cached_files:
            if cached_result.violations_count > 0:
                files_results.append(cached_result)
                total_violations += cached_result.violations_count

        result = CheckResult(
            check_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_violations=total_violations,
            total_files=len(cs_files),  # 记录总文件数（包括无违规的）
            files=files_results,  # 只包含有违规的文件
        )

        return result


def save_result_to_json(result: CheckResult, output_file: str = "code_check_result.json"):
    # 只将有违规的文件写入JSON
    files_with_violations = [f for f in result.files if f.violations_count > 0]

    result_dict = {
        "check_time": result.check_time,
        "total_violations": result.total_violations,
        "total_files": result.total_files,  # 总检查文件数
        "files_with_violations": len(files_with_violations),  # 有违规的文件数
        "files": [
            {
                "file": file_result.file,
                "violations_count": file_result.violations_count,
                "violations": [
                    {
                        "line": violation.line,
                        "line_content": violation.line_content,
                        "rule": violation.rule,
                        "description": violation.description,
                        "Author": violation.author,
                        "issue": violation.issue,
                        "commit_time": violation.commit_time,
                    }
                    for violation in file_result.violations
                ],
            }
            for file_result in files_with_violations  # 只处理有违规的文件
        ],
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result_dict, f, ensure_ascii=False, indent=2)

    print(f"检测结果已保存到: {output_file}")


def print_summary(result: CheckResult):
    files_with_violations = len([f for f in result.files if f.violations_count > 0])

    print(f"\n{'='*60}")
    print("检查完成!")
    print(f"{'='*60}")
    print(f"检查时间: {result.check_time}")
    print(f"检查文件数: {result.total_files}")
    print(f"有违规的文件数: {files_with_violations}")
    print(f"总违规数: {result.total_violations}")
    print(f"{'='*60}")
    print("详细结果已保存到JSON文件中（只包含有违规的文件）")


def main():
    parser = argparse.ArgumentParser(description="代码规范检查工具")
    parser.add_argument("path", help="要检查的文件或目录路径")
    parser.add_argument(
        "-o",
        "--output",
        default="code_check_result.json",
        help="输出JSON文件名 (默认: code_check_result.json)",
    )
    parser.add_argument(
        "-m", "--model", default="deepseek-chat", help="DeepSeek模型名称 (默认: deepseek-chat)"
    )
    parser.add_argument("--no-git", action="store_true", help="禁用Git信息提取")
    parser.add_argument(
        "--redmine-url",
        default="http://soc-redmine.wd.com/issues/",
        help="Redmine地址前缀 (默认: http://soc-redmine.wd.com/issues/)",
    )

    # 新增优化参数
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存")
    parser.add_argument("--cache-dir", default=".code_check_cache", help="缓存目录 (默认: .code_check_cache)")
    parser.add_argument("--rate-limit", type=float, default=0.1, help="API调用延迟秒数 (默认: 0.1)")

    args = parser.parse_args()

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 请设置环境变量 DEEPSEEK_API_KEY")
        print("例如: export DEEPSEEK_API_KEY=your_api_key_here")
        print("或者在代码中直接设置: os.environ['DEEPSEEK_API_KEY'] = 'your_api_key'")
        exit(1)

    path = Path(args.path)
    if not path.exists():
        print(f"错误: 路径 {args.path} 不存在")
        exit(1)

    checker = CodeChecker(
        api_key,
        args.model,
        enable_git_info=not args.no_git,
        redmine_url=args.redmine_url.rstrip("/") + "/",
        use_cache=not args.no_cache,
        cache_dir=args.cache_dir,
        rate_limit_delay=args.rate_limit,
    )

    if path.is_file():
        print(f"开始检查单个文件: {path}")
        file_result = checker.check_file(path)

        if file_result.violations:
            file_result.violations.sort(key=lambda v: v.line)

        result = CheckResult(
            check_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_violations=file_result.violations_count,
            total_files=1,
            files=[file_result] if file_result.violations_count > 0 else [],
        )

    else:
        print(f"开始检查目录: {path}")
        result = checker.check_directory(path)

    save_result_to_json(result, args.output)
    print_summary(result)


if __name__ == "__main__":
    main()
