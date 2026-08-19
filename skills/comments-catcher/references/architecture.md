# 架构与可移植路径

## 组件边界

```text
用户请求
   │  抖音 URL/aweme_id 或 B 站 URL/BV/av
   ▼
SKILL.md
   │  平台识别、默认值、安全边界与自动导航要求
   ▼
scripts/comments_catcher.py
   │  自动启动 daemon、导航页面、统一 CLI、分页、抽样、断点与输出
   ├───────────────┐
   ▼               ▼
抖音页面适配       B 站页面适配
   │               │
   ▼               ▼
抖音评论接口       B 站评论接口
   └───────┬───────┘
           ▼
      v2 JSON / CSV
```

采集器首先通过 WebBridge 的 `navigate` 打开目标页面，不要求用户预先打开正确的标签页；只有登录、CAPTCHA 或扩展连接需要人工处理。随后把两个站点的响应规范化为同一组评论字段：`cid`、`text`、`images`、`create_time`、`digg_count`、`reply_comment_total`、`nickname` 等。`images` 保留评论附图的 URL、宽高与多图顺序，文字仍独立保存在 `text`。平台特有的视频标识保存在 `meta.platform`、`meta.video_id` 以及 `aweme_id`/`bvid`/`oid` 中。

## 平台差异

- 抖音使用 `aweme_id` 和 cursor 分页，页面内 fetch 复用网页自身签名能力；默认会话为 `douyin`。
- B 站使用 BV/av 标识，先从当前可见页面的 `window.__INITIAL_STATE__.aid` 读取 `oid`，再在页面内调用 `/x/v2/reply/main` 与 `/x/v2/reply/reply`；默认会话为 `bili-comments`。
- 抖音和 B 站的一级评论分页均由统一采集器保存；B 站一级评论与回复接口的起始页为 1，抖音 cursor 起点为 0。
- 采集器通过可见 WebBridge 会话自动导航并等待页面就绪；不读取 Cookie，不复制认证头，也不自行生成平台签名。

## 规范 skill 目录

```text
skills/comments-catcher/
├── SKILL.md
├── agents/openai.yaml
├── scripts/
│   ├── comments_catcher.py
│   └── smoke_validate.py
└── references/
```

该目录是唯一需要复制的运行单元。仓库根目录中的 `.claude-plugin/` 和 `.codex-plugin/` 只负责各自宿主的插件发现，不应混入 skill 目录。

## 宿主兼容策略

- `SKILL.md` frontmatter 只使用通用的 `name` 与 `description`。
- `agents/openai.yaml` 提供 Codex 的 UI 元数据；Claude Code 会忽略它。
- `.claude-plugin/plugin.json` 和 `.codex-plugin/plugin.json` 是并列适配器；任一宿主都可以只读取自己的清单。
- 采集器不读取 `CLAUDE_PLUGIN_ROOT`、`CLAUDE_SKILL_DIR` 等单一宿主变量。Agent 应使用实际加载的 skill 根目录；安装器则通过固定的相对目录复制资源。

## 安装目录策略

用户级默认候选目录：

```text
Codex:      ~/.agents/skills
Claude:     ~/.claude/skills
```

如果 `.claude/skills` 是指向 `.agents/skills` 的 Junction/符号链接，安装器解析链接后只写入一次。两个目录独立时，`auto` 模式写入两边；需要其他布局时用 `--target-dir` 显式指定。

项目级默认候选目录：

```text
<project>/.agents/skills
<project>/.claude/skills
```

安装器先预检所有目标，未指定 `--force` 时不会覆盖已有 skill；复制到同一父目录下的临时目录并完成文件检查后再移动到正式位置。

## 路径不变量

1. 采集器脚本路径必须来自实际加载的 skill 根目录。
2. 输出路径可以是相对路径，但应由 Agent 解析为明确的目标文件，并避免覆盖无关文件。
3. 不得使用 `pwd`、`Get-Location` 或全盘搜索来猜测 skill 根目录。
4. 路径参数必须作为独立参数传给 Python，兼容空格、中文和其他非 ASCII 字符。
