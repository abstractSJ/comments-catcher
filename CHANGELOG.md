# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 的组织方式，并使用语义化版本号。

## [Unreleased]

### Changed

- 将规范 skill 目录改为 Codex 与 Claude Code 可共用的通用格式。
- 增加 `.codex-plugin/plugin.json` 与 Codex UI 元数据。
- 安装器支持共享目录自动识别、两套原生目录回退和显式目标目录。
- 修复输出 Schema、技能说明、README 与 CI 样例之间的字段漂移。
- 安装时排除 Python 缓存，避免把本地运行产物带入 skill 包。

## [0.3.0] - 2026-08-17

### Added

- 技能目录新增 `config.json` 本地配置，可调 `delay`（请求基础间隔）、`jitter_range`（间隔随机抖动幅度）、`sub_rate`（二级线程抽样比例）与 `seed`（抽样种子），优先级为命令行参数 > config.json > 内置默认。
- 新增 `--config` 指定自定义配置文件路径，新增 `--jitter-range` 命令行参数。
- 采集开始时打印最终生效的节奏、抽样参数与配置来源。

### Changed

- `--delay`、`--sub-rate`、`--seed` 缺省时改为先读取 `config.json`，再回落到内置默认值。
- 安装器与离线自检将 `config.json` 纳入必备文件清单。

## [0.2.0] - 2026-08-16

### Added

- 恢复 B 站视频评论采集，支持 BV/av 标识、公开评论接口、回复线程和断点续采。
- 增加 `--platform auto|douyin|bilibili` 与统一的 v2 输出 Schema。
- 兼容历史抖音 v1 状态文件，并对旧 B 站脚本的基础 `replies` 输出提供迁移读取。
- 普通采集由 Agent 自动启动 WebBridge daemon（必要时）并导航到目标视频；增加 `--reuse-current-page` 作为显式例外。

## [0.1.0] - 2026-08-16

### Added

- Claude Code 插件与插件市场清单。
- `comments-catcher` 技能工作流及中文参考文档。
- 输出结构 v1 JSON Schema。
- Windows、macOS 和 Linux 的本地复制安装器。
- README、安全隐私说明、故障排查与架构文档。
- 用于脚本编译、单元测试、清单校验和条件离线 smoke validation 的 CI 工作流。
