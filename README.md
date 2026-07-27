# 合成数据工作台

这是一个本机运行的合成数据工具平台，统一接入了 Synthetic Data Kit、Easy Dataset 和 SynLogic。三个上游项目保持独立，平台通过命令行、HTTP API 和 Python 模块调用它们，不复制上游界面，也不修改上游源码。

平台面向实际使用流程组织页面：进入工具、选择项目、上传材料、设置参数、生成、检查样本、导出。项目、文件、任务和结果按工具隔离，不需要在多个“任务中心”之间来回切换。

## 三个工具如何接入

| 工具 | 接入方式 | 平台内提供的功能 |
|---|---|---|
| [Synthetic Data Kit](https://github.com/meta-llama/synthetic-data-kit) | 网关调用原始 CLI | 普通 QA、CoT、文档摘要、已有问答补充 CoT、质量筛选和格式导出 |
| [Easy Dataset](https://github.com/ConardLi/easy-dataset) | 独立 sidecar，网关调用其 HTTP API | 文档问答、数据蒸馏、评估题生成、文本块预览和数据导出 |
| [SynLogic](https://github.com/MiniMax-AI/SynLogic) | 网关加载 Arrow Maze 生成器与原始验证器 | 迷宫生成、答案展示、人工修改和规则复验 |

请求链路如下：

```text
浏览器
  └─ Web（5173）
       └─ Gateway（18000）
            ├─ Synthetic Data Kit CLI
            ├─ Easy Dataset sidecar（1717）
            └─ SynLogic ArrowMazeVerifier
```

Web 只提交项目 ID、资产 ID 和工作流参数。模型密钥只进入 Gateway 进程，不会出现在网页、SQLite、导出文件或 Easy Dataset 数据库中。

## 已完成的功能

### Synthetic Data Kit

- 支持 Markdown、TXT、DOCX、PDF 和 EPUB 文本输入。
- 一次任务自动执行环境检查、文档解析、生成、筛选和结果整理。
- 支持 QA、CoT、摘要和已有问答 CoT 增强。
- CoT 增强输入会在任务创建前检查 JSON 结构，错误文件不会进入生成阶段。
- Judge 筛选后会恢复上游遗漏的 `reasoning` 字段。
- 可导出 JSONL、Alpaca、OpenAI FT 和 ChatML。

### Easy Dataset

- 作为未修改的本地 sidecar 运行。
- 支持文档上传、文本分块预览、单轮问答和多轮对话。
- 支持领域主题蒸馏。
- 评估数据支持判断题、单选题、多选题、简答题和开放题；页面选择的题型和数量会传到上游服务。
- 网关使用本机代理配置 Easy Dataset 的模型接口，真实模型密钥不会写入 sidecar。

### SynLogic

- 支持 Arrow Maze 数量、宽度、高度、预填比例和最大尝试次数。
- 生成后逐条调用原始 `ArrowMazeVerifier`。
- 页面可查看题目网格和标准答案，也可以修改二维答案后重新验证。
- 每次批量生成都会检查标准答案通过，并确认人为破坏的答案会被拒绝。

## 本机运行

### 环境要求

- macOS 或 Linux
- Docker Desktop / Docker Engine
- Git
- DeepSeek 或其他 OpenAI 兼容文本模型的 API Key

当前默认模型配置为：

```text
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
LLM_JUDGE_MODEL=deepseek-v4-pro
```

模型名称需要与实际供应商提供的接口一致。

### 1. 准备配置

```bash
cp .env.example .env
```

编辑 `.env`：

```dotenv
DEEPSEEK_API_KEY=填写本机密钥
LLM_CREDENTIAL_ROTATED=true
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
LLM_JUDGE_MODEL=deepseek-v4-pro
```

`.env` 已加入忽略规则，不会提交到 Git。

### 2. 启动

```bash
./scripts/docker-up.sh
```

启动脚本会先把三个上游项目检出到 `upstream/`，固定在本项目验证过的 commit，然后构建并启动三个容器。`upstream/` 不提交到本仓库。

服务启动后访问：

| 服务 | 地址 |
|---|---|
| 工作台 | http://127.0.0.1:5173 |
| Gateway | http://127.0.0.1:18000 |
| Easy Dataset | http://127.0.0.1:1717 |

检查状态：

```bash
./scripts/health.sh
```

停止服务：

```bash
./scripts/docker-down.sh
```

运行数据、SQLite、日志和导出结果保存在 `runtime/`，重新创建容器不会删除这些文件。

## 最短使用路径

### 从文档生成 CoT

1. 进入 Synthetic Data Kit。
2. 新建项目并上传 Markdown、DOCX 或 PDF。
3. 选择“CoT 思维链”。
4. 首次测试可生成 1 条，并把筛选阈值设为 1。
5. 确认链路正常后再增加数量和筛选阈值。

### 从文档生成问答

1. 进入 Easy Dataset。
2. 新建项目并上传文档。
3. 点击“导入并预览文本块”。
4. 选择单轮、多轮或两者都生成。
5. 生成后直接在当前页面检查和导出。

### 生成并验证 Arrow Maze

1. 进入 SynLogic。
2. 使用默认参数生成 20 条 `5×5` 迷宫。
3. 打开任意样本查看题目和答案。
4. 点击“人为破坏一格”，再次调用原始验证器，结果应被拒绝。

仓库内的 [demo-inputs](demo-inputs/README.md) 提供了可以直接上传的脱敏示例。

## 验证记录

当前版本已经完成以下真实运行：

- Synthetic Data Kit：真实文档解析、CoT 生成、Judge 筛选和 JSONL 导出；CoT 增强结果保留原答案和推理内容。
- Easy Dataset：真实文档上传、分块、问答生成和 CoT；评估数据按“单选题 1 条、简答题 1 条”生成成功。
- SynLogic：`1,000/1,000` 条 Arrow Maze 通过原始验证器；人为破坏的答案被拒绝。

视觉模型没有配置时，图片问答和多模态入口会被禁用，不会生成模拟结果。

## 开发与测试

准备本机开发环境：

```bash
./scripts/setup.sh
```

启动开发服务：

```bash
./scripts/dev.sh
```

运行检查：

```bash
uv run pytest
npm --prefix web run lint
npm --prefix web test
```

目录结构：

```text
gateway/       FastAPI 网关、任务执行器和三个工具适配器
web/           React/Vinext 前端
docker/        三个本机服务的镜像配置
scripts/       上游依赖、启动、停止和健康检查脚本
tests/         后端、导出和工具集成测试
demo-inputs/   可直接用于演示的脱敏输入
docs/          使用、架构和复现实验记录
runtime/       本机运行数据，不提交到 Git
upstream/      固定版本的上游项目，不提交到 Git
```

## 适用范围

- 当前版本是本机、单用户系统，没有登录和多人权限。
- 不执行大模型训练。
- 图片问答需要单独配置视觉模型。
- Easy Dataset 使用 AGPL-3.0，并包含额外品牌条款。本项目只把它作为未修改的本地 sidecar 调用。分发、托管或商用前应单独确认许可要求。

更多资料：

- [快速上手与演示手册](docs/快速上手与演示手册.md)
- [使用说明](docs/使用说明.md)
- [架构说明](docs/架构说明.md)
- [复现实验记录](docs/复现实验记录.md)
