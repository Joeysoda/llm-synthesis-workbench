# 合成数据工作台

这是一个本机运行的数据生成与质量检查平台，统一接入 Synthetic Data Kit、Easy Dataset、SynLogic、KAQG 和 Cleanlab。上游项目保持独立，平台通过命令行、HTTP API 或 Python 模块调用，不复制上游界面，也不要求用户手动拼接多段命令。

平台面向实际使用流程组织页面：进入工具、选择项目、上传材料、设置参数、生成、检查样本、导出。项目、文件、任务和结果按工具隔离，不需要在多个“任务中心”之间来回切换。

## 五个工具如何接入

| 工具 | 接入方式 | 平台内提供的功能 |
|---|---|---|
| [Synthetic Data Kit](https://github.com/meta-llama/synthetic-data-kit) | 网关调用原始 CLI | 普通 QA、CoT、文档摘要、已有问答补充 CoT、质量筛选和格式导出 |
| [Easy Dataset](https://github.com/ConardLi/easy-dataset) | 独立 sidecar，网关调用其 HTTP API | 文档问答、数据蒸馏、评估题生成、文本块预览和数据导出 |
| [SynLogic](https://github.com/MiniMax-AI/SynLogic) | 网关加载 Arrow Maze 生成器与原始验证器 | 迷宫生成、答案展示、人工修改和规则复验 |
| [KAQG](https://github.com/mfshiu/kaqg) | 固定版本源码 + 独立 worker + Neo4j/MQTT sidecar | PDF 知识抽取、图谱快照、分难度单选题生成和评估 |
| [Cleanlab](https://github.com/cleanlab/cleanlab) | 网关内本机 Python 依赖 | 中文分类数据的错标签、异常和近重复检查，以及人工审核 |

请求链路如下：

```text
浏览器
  └─ Web（5173）
       └─ Gateway（18000）
            ├─ Synthetic Data Kit CLI
            ├─ Easy Dataset sidecar（1717）
            ├─ SynLogic ArrowMazeVerifier
            ├─ KAQG worker ─ Neo4j / Mosquitto
            └─ Cleanlab 2.9.0
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

### KAQG

- 只接收带文本层的 PDF，并在运行前检查模型、worker、Neo4j 与 MQTT。
- 一次任务完成 PDF 解析、知识图谱构建、低中高难度单选题生成、评分和产物整理。
- 页面展示节点、关系、章节、抽取事实、四个选项、答案、来源证据和评估分数。
- 导出图谱 JSON、题目 JSONL 和完整实验记录。

### Cleanlab

- 支持 20～5000 条单标签中文分类数据，输入为 CSV、JSON 或 JSONL。
- 使用字符级 TF-IDF、分层交叉验证逻辑回归、TruncatedSVD 与 Cleanlab 检查错标签、异常和近重复。
- 也可导入已完成的平台任务；无分类标签时只运行有数据依据的异常与重复检查。
- 建议标签不会自动写回。用户可以接受建议、保留原标签或填写人工标签，原标签始终保留在血缘和审计记录中。

## 本机运行

完整、可复用的启动规则见 [INSTRUCTIONS.md](./INSTRUCTIONS.md)。日常默认使用轻量模式，不重新构建镜像，也不启动暂时不用的 sidecar。

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

### 2. 首次构建

```bash
./scripts/docker-build.sh all
```

构建脚本会先把四个源码型上游项目检出到 `upstream/` 并固定到已验证 commit；Cleanlab 由 Python 锁文件安装。首次构建可能短暂占用较高 CPU，完成后不需要在每次启动时重复执行。`upstream/` 不提交到本仓库。

### 3. 日常启动

默认只启动 Web 和 Gateway，可使用 Synthetic Data Kit、SynLogic 和 Cleanlab：

```bash
./scripts/docker-up.sh core
./scripts/health.sh core
```

按需启动：

```bash
./scripts/docker-up.sh easy   # 增加 Easy Dataset
./scripts/docker-up.sh kaqg   # 增加 KAQG、Neo4j 和 Mosquitto
./scripts/docker-up.sh all    # 完整演示时启动全部服务
```

服务启动后访问：

| 服务 | 地址 |
|---|---|
| 工作台 | http://127.0.0.1:5173 |
| Gateway | http://127.0.0.1:18000 |
| Easy Dataset | http://127.0.0.1:1717 |

日常停止服务并保留容器缓存：

```bash
./scripts/docker-stop.sh all
```

只有需要删除容器和网络时才执行 `./scripts/docker-down.sh`。运行数据、SQLite、日志和导出结果保存在 `runtime/`，停止或重新创建容器不会删除这些文件。

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

### 从 PDF 生成知识图谱试题

1. 进入 KAQG，新建独立项目。
2. 上传 `demo-inputs/kaqg/数据平台运维与质量规范.pdf`。
3. 领域填写“数据平台运维规范”，低、中、高难度各设 3 题。
4. 完成后检查图谱摘要、题目证据与三类下载产物。

### 检查分类数据质量

1. 进入 Cleanlab，新建独立项目。
2. 上传 `demo-inputs/cleanlab/数据质量工单.csv`，列名保持 `id / text / label`。
3. 运行自动训练检查，在结果中分别测试“接受建议、保留原标签、手动修改”。
4. 下载清洗后的 CSV/JSONL 和包含全部分数的审计报告。

仓库内的 [demo-inputs](demo-inputs/README.md) 提供了可以直接上传的脱敏示例。

## 验证记录

当前版本已经完成以下真实运行：

- Synthetic Data Kit：真实文档解析、CoT 生成、Judge 筛选和 JSONL 导出；CoT 增强结果保留原答案和推理内容。
- Easy Dataset：真实文档上传、分块、问答生成和 CoT；评估数据按“单选题 1 条、简答题 1 条”生成成功。
- SynLogic：`1,000/1,000` 条 Arrow Maze 通过原始验证器；人为破坏的答案被拒绝。
- Cleanlab：80 条中文工单完成本机实测；6/8 个植入错标签进入最可疑前 12，4/4 个领域外异常进入最低分前 8，6/6 个植入重复样本全部检出。
- KAQG：适配器、固定 worker、三项 sidecar、PDF 测试包和自动化契约已完成；真实 9 题模型闭环以本机 Docker 与有效文本模型连接为最终验收条件，结果会如实记录。

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
gateway/       FastAPI 网关、任务执行器和五个工具适配器
web/           React/Vinext 前端
kaqg_worker/   固定 KAQG 运行环境的本机 worker
docker/        Web、网关和各 sidecar 的镜像配置
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
- Cleanlab 2.9.0 使用 Apache-2.0。
- KAQG 当前固定 commit 为 `aa80de0082d5c217bbcb887ba386c2a85518c7d6`。其 README 声明 MIT，但该 commit 缺少实际 `LICENSE` 文件，因此本仓库不复制 KAQG 源码，只保留拉取脚本、适配器和版本说明；公开分发前仍需单独确认许可。

更多资料：

- [快速上手与演示手册](docs/快速上手与演示手册.md)
- [使用说明](docs/使用说明.md)
- [架构说明](docs/架构说明.md)
- [复现实验记录](docs/复现实验记录.md)
