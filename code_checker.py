import os
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import re
import hashlib
import time
import pickle

try:
    import requests
except ImportError:
    print("请安装requests库: pip install requests")
    raise SystemExit(1)


class LLMProvider(Enum):
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"


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
    total_violations: int
    total_files: int
    files: List[FileResult]
    false_positive_count: int = 0
    start_time: str = ""
    end_time: str = ""
    duration_seconds: float = 0.0


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
        except Exception:
            return False

    @staticmethod
    def run_git_command(cmd: List[str], cwd: str) -> Optional[str]:
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
        try:
            file_dir = str(file_path.parent) or "."

            check_cmd = ["git", "rev-parse", "--is-inside-work-tree"]
            if not GitInfoExtractor.run_git_command(check_cmd, file_dir):
                return "", "", ""

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

            first_line_parts = lines[0].split()
            if len(first_line_parts) < 1:
                return "", "", ""

            commit_hash = first_line_parts[0]
            author = None
            author_time = None

            for line in lines[1:]:
                if line.startswith("author "):
                    author = line[7:]
                elif line.startswith("author-time "):
                    author_time = line[12:]

            if author and author_time:
                try:
                    timestamp = int(author_time)
                    commit_date = datetime.fromtimestamp(timestamp).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                except (ValueError, TypeError):
                    commit_date = ""

                issue_info = GitInfoExtractor.extract_issue_from_commit_message(
                    commit_hash, file_dir
                )

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
        provider: str = "ollama",
        api_key: Optional[str] = None,
        model: str = "qwen2.5-coder:14b",
        enable_git_info: bool = True,
        redmine_url: Optional[str] = None,
        use_cache: bool = True,
        cache_dir: str = ".code_check_cache",
        rate_limit_delay: float = 0.1,
        deepseek_api_url: str = "https://api.deepseek.com/v1/chat/completions",
        ollama_url: str = "http://127.0.0.1:11434/api/chat",
        request_timeout: float = 120.0,
        ollama_num_predict: int = 4096,
        ollama_keep_alive: str = "5m",
    ):
        self.provider = LLMProvider(provider)
        self.api_key = api_key
        self.model = model
        self.enable_git_info = enable_git_info

        self.deepseek_api_url = deepseek_api_url
        self.ollama_url = ollama_url
        self.request_timeout = request_timeout
        self.ollama_num_predict = ollama_num_predict
        self.ollama_keep_alive = ollama_keep_alive

        if redmine_url:
            GitInfoExtractor.REDMINE_URL_PREFIX = redmine_url

        self.git_extractor = GitInfoExtractor()
        self.session = requests.Session()
        if self.provider == LLMProvider.DEEPSEEK:
            if not self.api_key:
                raise ValueError("DeepSeek模式需要提供API Key")
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )
        else:
            self.session.headers.update({"Content-Type": "application/json"})

        self.use_cache = use_cache
        self.cache_dir = Path(cache_dir)
        self.rate_limit_delay = rate_limit_delay

        if self.use_cache:
            self.cache_dir.mkdir(exist_ok=True)
            self.cache_file = self.cache_dir / "file_cache.pkl"
            self._load_cache()
        else:
            self.cache = {}

    def _load_cache(self) -> None:
        self.cache = {}
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "rb") as f:
                    self.cache = pickle.load(f)
            except Exception:
                self.cache = {}

    def _save_cache(self) -> None:
        if self.use_cache:
            try:
                with open(self.cache_file, "wb") as f:
                    pickle.dump(self.cache, f)
            except Exception:
                pass

    def _get_file_hash(self, file_path: Path) -> str:
        try:
            stat = file_path.stat()
            content_hash = hashlib.md5()
            with open(file_path, "rb") as f:
                content_hash.update(f.read())
            return f"{content_hash.hexdigest()}_{stat.st_mtime}"
        except Exception:
            return ""

    def _check_from_cache(self, file_path: Path) -> Optional[FileResult]:
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

    def _save_to_cache(self, file_path: Path, result: FileResult) -> None:
        if not self.use_cache:
            return

        file_hash = self._get_file_hash(file_path)
        if not file_hash:
            return

        cache_key = str(file_path)
        self.cache[cache_key] = (file_hash, result)
        self._save_cache()

    def _build_rules(self) -> str:
        rules_short = """请检查以下C#代码是否违反以下规则，使用原始行号进行报告：

        1. DEBUG_LOG_USAGE - 使用Debug.Log/LogError/LogException等UnityEngine.Debug类的Log函数的调用属于违规，SocLogger的日志调用不违规
        2. NEW_IN_UPDATE_METHOD - 在Update/LateUpdate/FixedUpdate中new引用类型，导致引用类型被多次实例化的视为违规，但如果是在判断里实例化的基本只new一次或new的次数很少不认为违规
        3. EMPTY_UPDATE_METHOD - 继承自MonoBehaviour的类中存在空的Update/LateUpdate/FixedUpdate方法

        4. FIND_FUNCTION_CALLS - 使用Find类函数，如Find/FindObjectOfType/FindObjectsOfType/FindGameObjectWithTag/FindGameObjectsWithTag/FindObjectsOfTypeAll
        仅限以下Find函数调用：
           调用Find/FindGameObjectWithTag/FindGameObjectsWithTag/FindWithTag且调用者为GameObject，
           调用Find且调用者为Transform类型或其派生类型，
           调用FindObjectOfType/FindObjectsOfType，
           调用FindObjectsOfTypeAll且调用者为Resources类型或其派生类型
        其他情况不属于违规（例如：GetComponent/GetComponents、Object.Destroy等）

        5. FOREACH_ON_SPECIFIC_CONTAINERS - 对SortedDictionary/Hashtable/BitArray/Queue/SortedList/ArrayList/Stack容器进行foreach
        6. GET_COMPONENTS_IN_CHILDREN - 仅GetComponentsInChildren调用
        7. GET_COMPONENTS_IN_PARENT - 仅GetComponentsInParent调用
        8. COMPUTE_BUFFER_GETDATA - ComputeBuffer.GetData调用
        9. TEXTURE_GETPIXELS_CALL - Texture.GetPixels/GetPixels32调用
        10. TEXTASSET_BYTES_USAGE - TextAsset.bytes调用
        11. CAMERA_MAIN_USAGE - 仅Camera.main调用
        12. RENDERER_MATERIAL_GETTER - 对Renderer进行material/materials的获取
        13. RENDERER_SHAREDMATERIALS_GETTER - 对Renderer进行sharedMaterials的获取
        14. ONGUI_METHOD_PRESENT - OnGUI方法存在
        15. SENDMESSAGE_USAGE - 匹配所有.SendMessage(调用且调用者的类型为GameObject或其派生类型
        16. TEXTURE_SETPIXELS_CALL - Texture.SetPixels调用
        17. TAG_PROPERTY_USAGE - .tag属性调用
        18. LINQ_USAGE - Linq相关函数调用

        19. REFLECTION_USAGE - 通过类型元数据在运行时进行查找/遍历/访问的反射相关调用
        以下情况认为属于违规：
        1) 反射查找/遍历：
        Type.GetType / Assembly.GetType(s) / object.GetType()
        Type.GetMethod(s) / GetField(s) / GetProperty(s) / GetConstructor(s) / GetMember(s)
        Type.GetInterfaces / GetNestedTypes / FindMembers 等
        2) 特性查询：
        Attribute.GetCustomAttribute(s) / MemberInfo.GetCustomAttributes
        3) 动态创建/调用：
        Activator.CreateInstance
        MethodInfo.Invoke / PropertyInfo.GetValue/SetValue / FieldInfo.GetValue/SetValue
        Type.InvokeMember / Delegate.CreateDelegate
        4) 反射元数据访问（读取 MemberInfo / PropertyInfo / FieldInfo / ParameterInfo 等属性）：
        例如：FieldInfo.FieldType / FieldInfo.Name / PropertyInfo.PropertyType / ParameterInfo.ParameterType

        20. INPUT_TOUCHES_USAGE - Input.touches调用
        21. HEAPLESS_ALTERNATIVE_AVAILABLE - 可改为无堆内存分配版的函数调用

        **重要**：请使用原始行号（每行前面的数字）进行报告，不要使用描述中的行号！
        **重要**：Debug.Log相关规则只适用于Unity的Debug类，不适用于自定义的logger类！

        返回JSON格式：{"violations": [{"line": 1, "line_content": "原始代码内容", "rule": "RULE_NAME", "description": "违规描述"}]}
        如果没有违规，返回：{"violations": []}"""
        return rules_short

    def _parse_editor_condition(self, condition: str) -> Optional[bool]:
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
                            top["exclude_current"] = bool(exclude_branch)
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
        code = self._remove_editor_code(code)
        lines = code.split("\n")
        prepared_lines = []
        line_mapping = {}

        for i, line in enumerate(lines, 1):
            line_content = line.rstrip("\n\r")
            line_mapping[i] = line_content
            prepared_lines.append(f"L{i:4d}: {line_content}")

        return "\n".join(prepared_lines), line_mapping

    def _extract_line_number(self, line_str: str) -> Optional[int]:
        try:
            patterns = [
                r"L(\d+):",
                r"第(\d+)行",
                r"line\s*(\d+)",
                r"Line\s*(\d+)",
                r"\[Line\s*(\d+)\]",
                r"(\d+):",
                r"\s(\d+)\s",
            ]
            for pattern in patterns:
                match = re.search(pattern, line_str, re.IGNORECASE)
                if match:
                    return int(match.group(1))

            numbers = re.findall(r"\b(\d+)\b", line_str)
            if numbers:
                return int(numbers[0])
            return None
        except Exception:
            return None

    def _parse_api_response(
        self, response_text: str, line_mapping: Dict[int, str]
    ) -> List[Violation]:
        try:
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            json_match = re.search(r"\{[\s\S]*\}", cleaned)
            if not json_match:
                print(f"警告: 无法从响应中提取JSON: {response_text[:200]}...")
                return []

            data = json.loads(json_match.group())
            violations = []
            for v in data.get("violations", []):
                reported_line = v.get("line", 0)
                line_content = v.get("line_content", "")
                rule = v.get("rule", "")
                description = v.get("description", "")

                if rule == "DEBUG_LOG_USAGE":
                    debug_patterns = [
                        r"Debug\.(Log|LogError|LogException|LogWarning|Assert)",
                        r"UnityEngine\.Debug\.(Log|LogError|LogException|LogWarning|Assert)",
                        r"print\(",
                    ]
                    is_debug_log = any(
                        re.search(pattern, line_content, re.IGNORECASE)
                        for pattern in debug_patterns
                    )

                    allowed_patterns = [
                        r"logger\.(Info|Error|Warning|Debug)",
                        r"Logger\.(Info|Error|Warning|Debug)",
                        r"Log\.(Info|Error|Warning|Debug)",
                        r"Console\.(WriteLine|Write)",
                        r"Trace\.(WriteLine|Write)",
                        r"System\.Diagnostics\.Debug\.(WriteLine|Write)",
                    ]
                    is_allowed_log = any(
                        re.search(pattern, line_content, re.IGNORECASE)
                        for pattern in allowed_patterns
                    )

                    if is_allowed_log:
                        print(f"跳过允许的日志调用: {line_content}")
                        continue
                    if not is_debug_log:
                        print(f"跳过非Debug.Log调用: {line_content}")
                        continue

                if reported_line in line_mapping:
                    actual_content = line_mapping[reported_line]
                    violations.append(
                        Violation(
                            line=reported_line,
                            line_content=actual_content,
                            rule=rule,
                            description=description,
                        )
                    )
                    continue

                extracted_line = self._extract_line_number(description)
                if extracted_line and extracted_line in line_mapping:
                    actual_content = line_mapping[extracted_line]
                    violations.append(
                        Violation(
                            line=extracted_line,
                            line_content=actual_content,
                            rule=rule,
                            description=description,
                        )
                    )
                    continue

                extracted_line = self._extract_line_number(line_content)
                if extracted_line and extracted_line in line_mapping:
                    actual_content = line_mapping[extracted_line]
                    violations.append(
                        Violation(
                            line=extracted_line,
                            line_content=actual_content,
                            rule=rule,
                            description=description,
                        )
                    )
                    continue

                print(
                    "警告: 无法确定行号映射 for line "
                    f"{reported_line} with content '{line_content}'"
                )
                violations.append(
                    Violation(
                        line=reported_line,
                        line_content=line_content,
                        rule=rule,
                        description=description,
                    )
                )

            return violations
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

    def _build_prompt_optimized(self, prepared_code: str, file_path: str) -> str:
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
        editor_paths = [
            "Editor",
            "EditorTools",
            "EditorWindows",
            "Gizmos",
            "EditorResources",
        ]
        for editor_path in editor_paths:
            if editor_path in str(file_path):
                return True, f"在编辑器目录 {editor_path} 中"
        return False, ""

    def _build_system_message(self) -> str:
        return (
            "你是代码规范检查工具，检查C#代码并返回JSON结果。请确保："
            "1. 报告的行号是原始文件中的准确行号；"
            "2. DEBUG_LOG_USAGE规则只适用于Unity的Debug类（Debug.Log, Debug.LogError等）；"
            "3. 不要将logger.InfoFormat, logger.ErrorFormat等误报为DEBUG_LOG_USAGE；"
            "4. 行号必须与代码中LXXX:格式的行号完全一致。"
        )

    def _call_model(self, prompt: str) -> Optional[str]:
        if self.rate_limit_delay > 0:
            time.sleep(self.rate_limit_delay)

        system_message = self._build_system_message()

        try:
            if self.provider == LLMProvider.DEEPSEEK:
                payload = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "top_p": 1,
                    "seed": 42,
                    "max_tokens": 4000,
                }
                response = self.session.post(
                    self.deepseek_api_url,
                    json=payload,
                    timeout=self.request_timeout,
                )
                if response.status_code != 200:
                    print(f"DeepSeek请求失败: {response.status_code}, {response.text}")
                    return None

                data = response.json()
                return data["choices"][0]["message"]["content"]

            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0,
                    "top_p": 1,
                    "seed": 42,
                    "num_predict": self.ollama_num_predict,
                },
                "keep_alive": self.ollama_keep_alive,
            }
            response = self.session.post(
                self.ollama_url,
                json=payload,
                timeout=self.request_timeout,
            )
            if response.status_code != 200:
                print(f"Ollama请求失败: {response.status_code}, {response.text}")
                return None

            data = response.json()
            if isinstance(data, dict):
                if "message" in data and isinstance(data["message"], dict):
                    return data["message"].get("content", "")
                if "response" in data:
                    return data.get("response", "")
            print("Ollama响应格式异常")
            return None
        except requests.RequestException as e:
            print(f"模型请求异常: {e}")
            return None
        except Exception as e:
            print(f"调用模型失败: {e}")
            return None

    def check_file(self, file_path: Path) -> FileResult:
        cached_result = self._check_from_cache(file_path)
        if cached_result is not None:
            return cached_result

        if file_path.suffix.lower() != ".cs":
            result = FileResult(file=str(file_path), violations_count=0, violations=[])
            self._save_to_cache(file_path, result)
            return result

        should_skip, reason = self._should_skip_file(file_path)
        if should_skip:
            print(f"跳过文件 {file_path}: {reason}")
            result = FileResult(file=str(file_path), violations_count=0, violations=[])
            self._save_to_cache(file_path, result)
            return result

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()

            prepared_code, line_mapping = self._prepare_code_for_ai_with_exact_line_numbers(
                code
            )
            prompt = self._build_prompt_optimized(prepared_code, str(file_path))
            content = self._call_model(prompt)

            if content is None:
                return FileResult(file=str(file_path), violations_count=0, violations=[])

            violations = self._parse_api_response(content, line_mapping)
            violations.sort(key=lambda v: v.line)

            enriched_violations = []
            for violation in violations:
                enriched_violations.append(
                    self._enrich_violation_with_git_info(file_path, violation)
                )

            file_result = FileResult(
                file=str(file_path),
                violations_count=len(enriched_violations),
                violations=enriched_violations,
            )
            self._save_to_cache(file_path, file_result)
            return file_result

        except FileNotFoundError:
            print(f"文件不存在: {file_path}")
            return FileResult(file=str(file_path), violations_count=0, violations=[])
        except Exception as e:
            print(f"检查文件 {file_path} 时出错: {e}")
            return FileResult(file=str(file_path), violations_count=0, violations=[])

    def check_directory(self, dir_path: Path) -> CheckResult:
        cs_files = list(dir_path.rglob("*.cs"))
        if not cs_files:
            print(f"在目录 {dir_path} 中没有找到.cs文件")
            return CheckResult(total_violations=0, total_files=0, files=[])

        filtered_files = []
        skipped_files = []
        cached_files = []

        for file_path in cs_files:
            cached_result = self._check_from_cache(file_path)
            if cached_result is not None:
                cached_files.append(cached_result)
                continue

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

        files_results = []
        total_violations = 0

        for i, file_path in enumerate(filtered_files, 1):
            print(f"正在检查文件 ({i}/{len(filtered_files)}): {file_path}")
            file_result = self.check_file(file_path)
            if file_result.violations_count > 0:
                files_results.append(file_result)
                total_violations += file_result.violations_count
                print(f"  发现 {file_result.violations_count} 个违规")
            else:
                print("  无违规")

        for cached_result in cached_files:
            if cached_result.violations_count > 0:
                files_results.append(cached_result)
                total_violations += cached_result.violations_count

        return CheckResult(
            total_violations=total_violations,
            total_files=len(cs_files),
            files=files_results,
        )


def save_result_to_json(
    result: CheckResult, output_file: str = "code_check_result.json"
) -> None:
    files_with_violations = [f for f in result.files if f.violations_count > 0]

    false_positive_count = 0
    for file_result in files_with_violations:
        for violation in file_result.violations:
            if "误报" in (violation.description or ""):
                false_positive_count += 1
    result.false_positive_count = false_positive_count

    result_dict = {
        "start_time": result.start_time,
        "end_time": result.end_time,
        "duration_seconds": result.duration_seconds,
        "total_violations": result.total_violations,
        "false_positive_count": false_positive_count,
        "total_files": result.total_files,
        "files_with_violations": len(files_with_violations),
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
            for file_result in files_with_violations
        ],
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result_dict, f, ensure_ascii=False, indent=2)

    print(f"检测结果已保存到: {output_file}")


def print_summary(result: CheckResult) -> None:
    files_with_violations = len([f for f in result.files if f.violations_count > 0])

    print(f"\n{'=' * 60}")
    print("检查完成!")
    print(f"{'=' * 60}")
    print(f"开始时间: {result.start_time}")
    print(f"结束时间: {result.end_time}")
    print(f"总耗时: {result.duration_seconds:.2f} 秒")
    print(f"检查文件数: {result.total_files}")
    print(f"有违规的文件数: {files_with_violations}")
    print(f"总违规数: {result.total_violations}")
    print(f"误报数: {result.false_positive_count}")
    print(f"{'=' * 60}")
    print("详细结果已保存到JSON文件中（只包含有违规的文件）")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="代码规范检查工具（支持DeepSeek/Ollama）")
    parser.add_argument("path", help="要检查的文件或目录路径")
    parser.add_argument(
        "-o",
        "--output",
        default="code_check_result.json",
        help="输出JSON文件名 (默认: code_check_result.json)",
    )
    parser.add_argument(
        "--provider",
        choices=[item.value for item in LLMProvider],
        default=LLMProvider.OLLAMA.value,
        help="模型提供方: ollama 或 deepseek (默认: ollama)",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="模型名称（默认: ollama=qwen2.5-coder:14b, deepseek=deepseek-chat）",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="DeepSeek API Key（可选，不填时读取环境变量DEEPSEEK_API_KEY）",
    )
    parser.add_argument(
        "--deepseek-url",
        default="https://api.deepseek.com/v1/chat/completions",
        help="DeepSeek API地址",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434/api/chat",
        help="Ollama Chat API地址",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=120.0,
        help="单次模型请求超时秒数 (默认: 120)",
    )
    parser.add_argument("--no-git", action="store_true", help="禁用Git信息提取")
    parser.add_argument(
        "--redmine-url",
        default="http://soc-redmine.wd.com/issues/",
        help="Redmine地址前缀 (默认: http://soc-redmine.wd.com/issues/)",
    )
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存")
    parser.add_argument(
        "--cache-dir", default=".code_check_cache", help="缓存目录 (默认: .code_check_cache)"
    )
    parser.add_argument(
        "--rate-limit", type=float, default=0.1, help="API调用延迟秒数 (默认: 0.1)"
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=4096,
        help="Ollama num_predict 参数 (默认: 4096)",
    )
    parser.add_argument(
        "--ollama-keep-alive",
        default="5m",
        help="Ollama keep_alive 参数 (默认: 5m)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    start_timestamp = time.time()
    start_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    provider = LLMProvider(args.provider)
    model = args.model
    if not model:
        model = (
            "qwen2.5-coder:14b"
            if provider == LLMProvider.OLLAMA
            else "deepseek-chat"
        )

    api_key = args.api_key or os.getenv("DEEPSEEK_API_KEY")
    if provider == LLMProvider.DEEPSEEK and not api_key:
        print("错误: DeepSeek模式下请设置 --api-key 或 DEEPSEEK_API_KEY")
        raise SystemExit(1)

    path = Path(args.path)
    if not path.exists():
        print(f"错误: 路径 {args.path} 不存在")
        raise SystemExit(1)

    checker = CodeChecker(
        provider=provider.value,
        api_key=api_key,
        model=model,
        enable_git_info=not args.no_git,
        redmine_url=args.redmine_url.rstrip("/") + "/",
        use_cache=not args.no_cache,
        cache_dir=args.cache_dir,
        rate_limit_delay=args.rate_limit,
        deepseek_api_url=args.deepseek_url,
        ollama_url=args.ollama_url,
        request_timeout=args.request_timeout,
        ollama_num_predict=args.ollama_num_predict,
        ollama_keep_alive=args.ollama_keep_alive,
    )

    print(f"当前Provider: {provider.value}, Model: {model}")

    if path.is_file():
        print(f"开始检查单个文件: {path}")
        file_result = checker.check_file(path)
        if file_result.violations:
            file_result.violations.sort(key=lambda v: v.line)
        result = CheckResult(
            total_violations=file_result.violations_count,
            total_files=1,
            files=[file_result] if file_result.violations_count > 0 else [],
        )
    else:
        print(f"开始检查目录: {path}")
        result = checker.check_directory(path)

    end_timestamp = time.time()
    end_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result.start_time = start_time_str
    result.end_time = end_time_str
    result.duration_seconds = round(end_timestamp - start_timestamp, 3)

    save_result_to_json(result, args.output)
    print_summary(result)


if __name__ == "__main__":
    main()
