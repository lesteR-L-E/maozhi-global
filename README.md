# 贸智全球桌面端骨架

当前版本是“纯 LLM、无业务工具”的 Electron + React 骨架：左侧为全球地图占位区，右侧为可用的对话面板。Agent Runtime 位于 Electron 主进程，当前已适配 DeepSeek 和 OpenAI Compatible 两种 Chat Completions Provider。

## 开发

```bash
npm install
npm run dev
```

### Python 分析 Worker 初始化

四个业务工具通过 Python Worker 读取本地 DuckDB 演示数据库。首次运行前执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r python/requirements.txt
.venv/bin/python python/build_demo_database.py
```

数据库文件为 `data/maozhi-demo.duckdb`，明确标记为比赛演示模拟数据。可以用 `MAOZHI_DATABASE` 指定其他数据库，用 `PYTHON_BIN` 指定其他 Python 解释器。Worker 协议和算法说明见 [python/README.md](python/README.md)。

首次使用时，在项目根目录复制`.env.example`为`.env`并填写配置。设置中的非空 API Key、Base URL 和 Model 优先；对应字段留空时，主进程才读取`.env`环境变量。设置值仅保存在本机 localStorage，不会写入仓库。

## LLM环境变量

环境变量写在项目根目录的`.env`文件中（不要提交到Git）：

```bash
cp .env.example .env
```

然后编辑`.env`：

```dotenv
DEEPSEEK_API_KEY=sk-你的Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

如果使用 OpenAI Compatible Provider，可配置：

```dotenv
OPENAI_API_KEY=sk-你的Key
OPENAI_BASE_URL=https://api.openai.com
OPENAI_MODEL=gpt-4o-mini
```

通用兜底变量也支持：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。优先级为：Electron设置中的非空值 > Provider专用环境变量 > 通用`LLM_*`变量 > 内置默认值。设置改为空字符串后，重启/下次请求即可回退到环境变量。

保存`.env`后直接运行`npm run dev`即可。不要把真实Key写进`package.json`、源码、提交记录或前端静态文件。正式产品应改用系统密钥链，而不是localStorage。

## 类型检查与构建

```bash
npm run typecheck
npm run build
```

## 打包

```bash
npm run dist
```

`electron-builder` 会按当前构建平台生成对应安装包；正式的 macOS/Windows 构建建议分别在对应 CI Runner 上执行。

## 当前边界

- 贸易、客户、预测工具当前读取固定模拟数据库，不是真实企业数据；
- 正式数据接入时需要替换数据库内容并重新执行数据质量验收；
- 地图目前是本地视觉占位，下一阶段替换为 GeoJSON/SVG 交互地图；
- DeepSeek 请求使用 `/chat/completions`，可通过设置切换到 OpenAI Compatible Base URL；
- 生产环境仍需补充密钥安全存储、流式输出、重试/取消、工具协议和离线兜底。
