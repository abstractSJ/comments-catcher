# 安装与前置条件

## 通用目录约定

规范 skill 是完整的 `skills/comments-catcher` 文件夹。宿主只需要发现其中的 `SKILL.md`；脚本与引用文件通过该文件夹的相对路径绑定，因此不依赖启动命令的当前工作目录。

当前机器的用户级目录关系是：

```text
~/.claude/skills  ->  ~/.agents/skills
```

这是共享目录，不需要安装两份。安装器会解析已有的 Junction/符号链接；当目标机器没有共享链接时，`auto` 模式会把 skill 安装到 `.agents/skills` 和 `.claude/skills` 两个原生目录，保证两个宿主都能发现。

## 前置条件

- Python 3.10 或更高版本。
- 已安装并可由 Agent 使用的 Kimi WebBridge 扩展/daemon；采集器会在每次任务开始时幂等启动 daemon，并在导航前等待扩展完成连接。
- 对应平台的浏览器登录状态可用；Agent 会自动打开目标抖音或 B 站视频，只有登录失效时才需要用户手动登录。
- 输出目录具有写权限。

默认会话名：抖音为 `douyin`，B 站为 `bili-comments`。如果本地会话名称不同，使用 `--session NAME` 或 `COMMENTS_CATCHER_SESSION` 指定。

本技能不会替用户登录，不接收账号密码，不读取或导出 Cookie，也不会自动处理 CAPTCHA。首次需要登录时，Agent 可以先导航到页面，再请用户在同一个可见会话中手动完成登录。

## 安装器

Windows PowerShell 5.1 或更高版本：

```powershell
.\install.ps1 -Scope User -Target Auto
```

PowerShell 选项：

| 选项 | 说明 |
|---|---|
| `-Scope User|Project` | 用户级或项目级安装。 |
| `-ProjectPath PATH` | 项目级安装的项目根目录。 |
| `-Target Auto|Codex|Claude|Both` | 自动、Codex 原生目录、Claude 原生目录或两边。 |
| `-TargetDir PATH` | 直接指定 skill 父目录，优先级最高。 |
| `-Force` | 明确允许替换已有 `comments-catcher` 目录。 |

macOS / Linux：

```bash
chmod +x ./install.sh
./install.sh --scope user --target auto
```

Shell 安装器支持同名选项的长格式：`--scope`、`--project-dir`、`--target`、`--target-dir` 和 `--force`。

## 安装后检查

目标目录至少应包含：

```text
comments-catcher/
├── SKILL.md
├── agents/openai.yaml
├── scripts/
│   ├── comments_catcher.py
│   └── smoke_validate.py
└── references/
    ├── architecture.md
    ├── cli-reference.md
    ├── output-schema-v1.json
    ├── output-schema-v2.json
    ├── safety-privacy.md
    ├── setup.md
    └── troubleshooting.md
```

安装后可从任意工作目录运行：

```text
python <installed-skill-root>/scripts/smoke_validate.py
```

正常采集时不需要用户预先打开页面；直接传入视频 URL/ID。采集器会先启动 daemon、用 `list_tabs` 轻量探测等待扩展连接，再自动导航。首次收到 `no extension connected` 会在内部有限重试，不应立即要求用户排查。需要单独预检时运行：

```text
python <installed-skill-root>/scripts/comments_catcher.py <VIDEO> --platform douyin --prepare-page
python <installed-skill-root>/scripts/comments_catcher.py <VIDEO> --platform bilibili --prepare-page
```

如果 daemon 仍无法连接或浏览器扩展未就绪，再检查 WebBridge 安装；不要改用 Cookie 或复制认证请求头。
