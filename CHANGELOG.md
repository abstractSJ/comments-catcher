# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 的组织方式，并使用语义化版本号。

## [Unreleased]

### Changed

- 将规范 skill 目录改为 Codex 与 Claude Code 可共用的通用格式。
- 增加 `.codex-plugin/plugin.json` 与 Codex UI 元数据。
- 安装器支持共享目录自动识别、两套原生目录回退和显式目标目录。
- 修复输出 Schema、技能说明、README 与 CI 样例之间的字段漂移。
- 安装时排除 Python 缓存，避免把本地运行产物带入 skill 包。

## [0.5.0] - 2026-08-17

### Added

- 博主主页批量模式：`--space` 接受 B 站 `space.bilibili.com/<mid>` 或抖音 `douyin.com/user/<sec_uid>` 主页，采集器打开主页并滚动收集视频清单（页面默认按发布时间新→旧排列），随后串行逐视频采集评论。
- 批量模式必须显式指定 `--max-videos`（无默认值），由 Agent 先与用户确认要采集的最近视频个数。
- 真实浏览模拟：打开每个视频页后触发静音播放、轻微滚动页面并按 `dwell` 秒停留，模拟用户观看后翻评论的过程。
- 视频级节奏：`config.json` 新增 `video_delay`（相邻视频基础间隔，最低 5.0，默认 20.0）与 `dwell`（页面停留秒数，0–600，默认 15.0），同样支持平台专属小节与 `--video-delay`/`--dwell` 命令行覆盖。
- 两层断点：输出目录的 `video_list.json` 记录每个视频的 done/failed 状态，重跑自动跳过已完成视频；每个视频仍保留页级断点。单个视频失败记录原因后跳过，连续 3 个失败判定为风控或登录失效并中止任务。

## [0.4.0] - 2026-08-17

### Added

- `config.json` 支持 `douyin`/`bilibili` 平台专属小节，只需写该平台要覆盖的键，省略的键沿用顶层通用配置；优先级为命令行参数 > 平台小节 > 顶层通用 > 内置默认。

### Fixed

- 修正 SKILL.md 与参考文档的示例和表述：Agent 在用户未明确要求时不得携带 `--delay`/`--jitter-range`/`--sub-rate`/`--seed`，避免照抄示例参数静默覆盖 config.json 中的用户配置。

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
