# 代码规范检查脚本（支持 Ollama / DeepSeek）

本仓库提供一个基于大模型的 C# 代码规范检测脚本：`code_checker.py`。  
脚本会扫描 `.cs` 文件，调用模型识别违规项，并输出 JSON 结果（含行号、规则、描述、可选 Git blame 信息）。

## 1. 安装依赖

```bash
pip install requests
```

## 2. 使用本地 Ollama（推荐）

### 2.1 启动 Ollama 服务

```bash
ollama serve
```

默认监听：`http://127.0.0.1:11434`

### 2.2 拉取模型（示例）

```bash
ollama pull qwen2.5-coder:14b
```

### 2.3 运行检查

检查目录：

```bash
python code_checker.py /path/to/your/csharp/project \
  --provider ollama \
  --model qwen2.5-coder:14b \
  -o code_check_result.json
```

检查单文件：

```bash
python code_checker.py /path/to/file.cs --provider ollama
```

## 3. 使用 DeepSeek（可选）

```bash
export DEEPSEEK_API_KEY=your_api_key
python code_checker.py /path/to/project --provider deepseek --model deepseek-chat
```

## 4. 常用参数

- `--provider ollama|deepseek`：选择模型后端（默认 `ollama`）
- `--model`：模型名（默认 `ollama=qwen2.5-coder:14b`，`deepseek=deepseek-chat`）
- `--ollama-url`：Ollama 接口地址（默认 `http://127.0.0.1:11434/api/chat`）
- `--request-timeout`：单次请求超时秒数
- `--no-git`：不做 git blame 信息补充
- `--no-cache`：禁用文件结果缓存
- `--cache-dir`：缓存目录
- `-o/--output`：输出 JSON 文件名

## 5. 输出说明

输出默认是 `code_check_result.json`，包含：

- 总文件数、总违规数、误报数
- 有违规的文件列表
- 每条违规的行号、原始代码、规则名、描述
- 可选：作者、提交时间、issue 信息（来自 git blame）
