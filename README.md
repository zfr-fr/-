# -
企业级软件开发-新闻管理系统

## DeepSeek 代码规范检查脚本

该仓库提供 `deepseek_codecheck.py`，用于将规则、示例和文件内容作为提示词发送到
DeepSeek API，并返回结构化的 JSON 结果。

### 使用方式

1. 设置环境变量：

```
export DEEPSEEK_API_KEY="your_api_key"
```

2. 执行检测（单文件或目录均可）：

```
python deepseek_codecheck.py --rules rules.txt --examples examples.txt path/to/file_or_dir
```

3. 输出到文件（可选）：

```
python deepseek_codecheck.py --rules rules.txt --examples examples.txt path/to/dir --output result.json
```

### 说明

- 默认只扫描常见代码扩展名（`.cs/.py/.js/.ts/...`），可用 `--include-ext` 自定义。
- 会剥离注释内容并尽量忽略未被调用的代码（基于启发式规则，非完整静态分析）。
- Unity 生命周期方法（如 `Update`、`LateUpdate` 等）默认视为已调用。
