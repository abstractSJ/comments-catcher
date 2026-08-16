# comments-catcher

一个可同时供 Codex 与 Claude Code 发现的抖音 / B 站公开评论采集 skill。规范运行单元只有：

```text
skills/comments-catcher/
├── SKILL.md
├── agents/openai.yaml
├── scripts/
│   ├── comments_catcher.py
│   └── smoke_validate.py
└── references/
```

`.claude-plugin/` 和 `.codex-plugin/` 是宿主插件入口；直接复制或运行安装器时，不需要修改 skill 内容，也不依赖当前工作目录。

## 支持平台

- 抖音：视频 URL、`aweme_id`，默认会话 `douyin`。
- B 站：视频 URL、BV 号、av 号，默认会话 `bili-comments`。
- `--platform auto` 会按输入自动识别，也可以显式指定平台。
- Agent 会通过 WebBridge 自动启动 daemon（若未运行）并打开目标视频；只复用浏览器已有登录状态，不接收 Cookie、令牌或认证请求头。

## 为什么 Codex 和 Claude Code 可以共用

skill 的通用格式是一个包含 `SKILL.md` 的目录，frontmatter 只保留 `name` 和 `description`。本项目的采集脚本、引用资料和 `agents/openai.yaml` 都放在同一目录内。

在当前机器上，`C:\Users\1\.claude\skills` 是指向 `C:\Users\1\.agents\skills` 的 Junction，因此两个宿主实际读取的是同一份文件。安装器的 `Auto` 模式会识别这种共享目录并只安装一份；如果目标机器的两个目录彼此独立，则自动安装到两边。

## 前置条件

- Python 3.10 或更高版本。
- 已安装并可连接浏览器的 Kimi WebBridge 扩展；采集器会尝试自动启动 daemon。
- 对应平台的登录状态可用；只有登录失效或出现 CAPTCHA 时才需要用户手动处理。
- 输出目录可写。

项目只复用已有会话，不接收 Cookie、令牌或认证请求头。Agent 会自动导航，登录失效或 CAPTCHA 才需要用户在同一个可见会话中手动处理。

## 安装

### Windows

用户级自动安装：

```powershell
.\install.ps1 -Scope User -Target Auto
```

项目级自动安装：

```powershell
.\install.ps1 -Scope Project -ProjectPath "D:\path\to\project" -Target Auto
```

目标已存在时才使用 `-Force`。安装器只复制本地 `skills/comments-catcher`，不会联网或执行远程脚本。

### macOS / Linux

```bash
chmod +x ./install.sh
./install.sh --scope user --target auto
```

### 直接复制

把完整的 `skills/comments-catcher` 目录复制为以下任一位置：

```text
用户级 Codex：   ~/.agents/skills/comments-catcher
用户级 Claude：  ~/.claude/skills/comments-catcher
项目级 Codex：   <project>/.agents/skills/comments-catcher
项目级 Claude：  <project>/.claude/skills/comments-catcher
```

如果两个宿主目录是同一个 Junction/符号链接，只复制一次即可。不要只复制 `SKILL.md`。

## 使用

正常采集时不需要用户预先打开页面，直接传入视频 URL/ID，采集器会自动导航。需要单独预检时：

```bash
python <skill-root>/scripts/comments_catcher.py \
  "https://www.douyin.com/video/0000000000000000000" \
  --platform douyin --prepare-page
python <skill-root>/scripts/comments_catcher.py \
  "https://www.bilibili.com/video/BV1qnuq6dEga" \
  --platform bilibili --prepare-page
```

抖音：

```bash
python <skill-root>/scripts/comments_catcher.py \
  "https://www.douyin.com/video/0000000000000000000" \
  --platform douyin --output ./outputs/douyin.json \
  --with-sub --delay 5 --sub-rate 0.5 --seed 42
```

B 站：

```bash
python <skill-root>/scripts/comments_catcher.py \
  "https://www.bilibili.com/video/BV1qnuq6dEga" \
  --platform bilibili --session bili-comments \
  --output ./outputs/bilibili.json \
  --with-sub --delay 5 --sub-rate 0.5 --seed 42
```

`<skill-root>` 必须是实际加载的 skill 目录，不要用当前工作目录猜测。完整参数见 [`skills/comments-catcher/references/cli-reference.md`](skills/comments-catcher/references/cli-reference.md)。

## 输出

新输出使用 v2 Schema，包含通用的 `meta.platform`、`meta.video_id`、计数、游标、完成状态、抽样率和种子；抖音额外保存 `aweme_id`，B 站额外保存 `bvid` 和页面读取到的 `oid`。`comments` 是一级评论，`subs` 是可选的回复线程。

`all_count` 是平台接口报告的总数，可能包含一级评论和回复；`main_complete=false` 或 `last_has_more=1` 时，不能把结果描述为全量完成。

## 校验

```bash
python skills/comments-catcher/scripts/smoke_validate.py
python -m unittest discover -s tests -p "test*.py"
```

## 安全边界

- 只采集用户可见的公开评论。
- 不绕过登录、权限、地区限制、风控或速率限制。
- 不轮换代理、伪造指纹、逆向签名或自动处理 CAPTCHA。
- 输出与日志不得包含 Cookie、令牌、认证头或浏览器存储。

详见 [`skills/comments-catcher/references/safety-privacy.md`](skills/comments-catcher/references/safety-privacy.md)。
