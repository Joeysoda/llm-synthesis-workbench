# 项目操作约束

处理本项目之前必须先阅读根目录的 `INSTRUCTIONS.md`。

- 日常启动默认使用 `./scripts/docker-up.sh core`，不得自动添加 `--build`。
- Easy Dataset 和 KAQG 只在需要演示或测试时按需启动。
- 只有依赖、Dockerfile 或上游固定版本发生变化时，才使用 `docker-build.sh`。
- 不得在命令、日志、文档或提交中输出模型密钥。
- 未经用户明确确认，不提交或推送 GitHub，不删除 `runtime/`，不修改 `upstream/`。
