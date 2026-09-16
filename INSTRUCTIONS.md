# 本机启动与停止说明

这份文件是本项目的启动基准。下次使用或由新的 Codex 任务接手时，先阅读本文件，再执行命令。

## 一、基本原则

1. 日常启动不重新构建镜像，不使用 `docker compose up --build`。
2. 默认只启动 Web 和 Gateway。Synthetic Data Kit、SynLogic、Cleanlab 都由 Gateway 提供，不需要额外容器。
3. Easy Dataset 和 KAQG 只在实际使用时启动，用完立即停止。
4. 首次安装或依赖发生变化时才执行独立构建命令。
5. 密钥只放在本机 `.env`，不得打印、提交或写入日志。
6. 正常停止使用 `docker-stop.sh`，保留容器和缓存；只有需要重建容器时才使用 `docker-down.sh`。

## 二、首次准备

进入项目目录：

```bash
cd '/Users/nihou/Desktop/pku_security/proj3（dataset）/integrated-system'
```

确认 Docker Desktop 已启动：

```bash
docker info >/dev/null
```

首次配置环境变量：

```bash
cp .env.example .env
```

在 `.env` 中填写本机密钥，并确认以下配置：

```dotenv
MINIMAX_API_KEY=填写本机 MiniMax Key
LLM_CREDENTIAL_ROTATED=true
LLM_BASE_URL=https://api.minimaxi.com/v1
LLM_MODEL=MiniMax-M3
LLM_JUDGE_MODEL=MiniMax-M3
```

`.env` 已被 Git 忽略。任何检查命令都不得输出密钥内容。

首次拉取上游并构建全部镜像：

```bash
./scripts/docker-build.sh all
```

这是重操作，可能持续较长时间并短暂占用较高 CPU。构建完成后，日常启动不要再次执行它。

## 三、日常轻量启动

适用于 Synthetic Data Kit、SynLogic 和 Cleanlab：

```bash
./scripts/docker-up.sh core
./scripts/health.sh core
```

访问：

- 平台：<http://127.0.0.1:5173>
- Gateway：<http://127.0.0.1:18000>

这个模式只启动 Web 和 Gateway，是默认推荐方式。

## 四、按需启动 Easy Dataset

```bash
./scripts/docker-up.sh easy
./scripts/health.sh easy
```

访问：

- 平台页面：<http://127.0.0.1:5173/easy-dataset>
- Easy Dataset sidecar：<http://127.0.0.1:1717>

使用结束后只停止 Easy Dataset：

```bash
./scripts/docker-stop.sh easy
```

## 五、按需启动 KAQG

KAQG 会额外启动 worker、Neo4j 和 Mosquitto：

```bash
./scripts/docker-up.sh kaqg
./scripts/health.sh kaqg
```

访问：

- 平台页面：<http://127.0.0.1:5173/kaqg>

KAQG 的 Neo4j、Mosquitto 和 worker 只在 Docker 内部网络开放。使用结束后执行：

```bash
./scripts/docker-stop.sh kaqg
```

## 六、按需启动医疗领域包

医疗领域包会启动内部 Synthea worker。它只生成完全虚构的患者记录，不接收真实病例；完成后会输出 FHIR、CSV、时间线、质量报告和数据卡。

```bash
./scripts/docker-build.sh medical  # 首次或镜像更新后执行一次
./scripts/docker-up.sh medical
./scripts/health.sh medical
```

访问：<http://127.0.0.1:5173/medical>

使用结束后可单独停止该 worker：

```bash
./scripts/docker-stop.sh medical
```

## 七、五个工具全部启动

只有完整演示或联合验收时使用：

```bash
./scripts/docker-up.sh all
./scripts/health.sh all
```

不要把 `all` 作为日常启动方式。

## 八、模型连接验证

服务启动后，可在“设置”页面点击“测试连接”。也可以执行不包含密钥的最小探测：

```bash
curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:18000/api/v2/integrations/text/probe
```

只有返回 `verified` 才能说明文本模型真实可用。环境变量存在、容器健康或端口可访问，都不能替代模型探测。

## 九、停止与清理

停止整个项目，但保留容器和构建缓存：

```bash
./scripts/docker-stop.sh all
```

查看是否仍有本项目容器运行：

```bash
docker compose ps
```

只有需要删除本项目容器和网络时才执行：

```bash
./scripts/docker-down.sh
```

该命令不会删除 `runtime/`，但下次启动需要重新创建容器。

## 十、什么时候重新构建

仅在以下情况重新构建：

- 修改了 Dockerfile、`pyproject.toml`、`uv.lock` 或前端依赖。
- 修改了前端代码，或修改了 Gateway 之外需要复制进镜像的代码。
- 上游固定 commit 发生变化。
- 当前镜像不存在或损坏。

按范围构建：

```bash
./scripts/docker-build.sh core
./scripts/docker-build.sh easy
./scripts/docker-build.sh kaqg
./scripts/docker-build.sh medical
./scripts/docker-build.sh all
```

构建结束后，再执行对应的 `docker-up.sh`，不要把构建和日常启动混在一起。

Gateway 在本机 Compose 中只读挂载 `gateway/` 源码。仅修改 Gateway 后端代码时不需要重新构建镜像，执行下面的低负载重启即可：

```bash
docker compose up -d --no-deps --no-build --force-recreate gateway web
```

## 十一、CPU 占用判断

- 首次构建或生成数据时短暂升高：正常。
- 服务空闲 3～5 分钟后仍持续占满 CPU：不正常，应先检查 `docker stats --no-stream`。
- 日常只需要三个轻量工具时使用 `core`，不要启动 Easy Dataset、Neo4j、KAQG worker 或 Synthea worker。
- 不要因为本项目出现高占用而直接停止其他项目容器；先确认容器名称和端口归属。

## 十二、常见故障

页面拒绝连接：

```bash
./scripts/docker-up.sh core
./scripts/health.sh core
```

提示镜像不存在：

```bash
./scripts/docker-build.sh core
./scripts/docker-up.sh core
```

Easy Dataset 不可达：

```bash
./scripts/docker-up.sh easy
./scripts/health.sh easy
```

KAQG 不可达：

```bash
./scripts/docker-up.sh kaqg
./scripts/health.sh kaqg
```

医疗领域包不可达：

```bash
./scripts/docker-up.sh medical
./scripts/health.sh medical
```

运行记录、SQLite、上传文件、日志和导出结果都在 `runtime/`。排障时不得删除该目录。
