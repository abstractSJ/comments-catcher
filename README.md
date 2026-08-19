# comments-catcher

用 Codex 或 Claude Code 采集抖音、B 站视频的公开评论。

支持一级评论、评论附图、按比例抽样回复、断点续采、B 站字幕文稿和 JSON/CSV 导出。

## 快速开始

### 1. 准备环境

只需要准备：

- Python 3.10 或更高版本；
- 已安装 Kimi WebBridge 浏览器扩展/daemon；
- 在浏览器中登录对应的抖音或 B 站账号一次。

不需要 API Key、Cookie、requests、Node.js 或 yt-dlp。

### 2. 下载项目

有 Git：

~~~
git clone https://github.com/abstractSJ/comments-catcher.git
cd comments-catcher
~~~

没有 Git：在 GitHub 页面点击 **Code → Download ZIP**，解压后进入项目目录。

### 3. 安装 skill

Windows PowerShell：

~~~
.\install.ps1 -Scope User -Target Auto
~~~

如果 PowerShell 阻止执行本地脚本：

~~~
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Scope User -Target Auto
~~~

macOS / Linux：

~~~
chmod +x ./install.sh
./install.sh --scope user --target auto
~~~

安装器会把 skill 放到 Codex 和 Claude Code 能发现的位置。安装后重启或刷新对应的 Agent。

### 4. 直接告诉 Agent 要采集什么

不需要用户先打开视频页面，也不需要手动运行 Python。直接发送类似请求：

~~~
请使用 comments-catcher 采集这个 B 站视频的公开评论：
https://www.bilibili.com/video/BVxxxx

保存为 D:/output/bilibili-comments.json，
采集一级评论，并按默认设置抽样回复。
~~~

抖音示例：

~~~
请使用 comments-catcher 采集这个抖音视频的公开评论：
https://www.douyin.com/video/1234567890123456789

保存为 D:/output/douyin-comments.json。
~~~

Agent 会自动启动 WebBridge、打开目标视频、等待页面就绪并开始采集。

### 5. 只有以下情况需要用户介入

- 第一次登录或登录状态失效：在 Agent 打开的可见浏览器页面中登录；
- 页面出现 CAPTCHA：用户手动完成后告诉 Agent 重试；
- WebBridge 扩展未安装、未连接或版本过旧。

其他情况下不需要用户手动打开页面。

## 支持的输入

- 抖音：视频 URL 或 aweme_id；
- B 站：完整视频 URL、BV 号或 av 号；
- b23.tv 短链：请提供展开后的完整 URL、BV 号或 av 号。

平台通常会自动识别，也可以让 Agent 显式指定抖音或 B 站。

## 输出与断点续采

- 默认输出 JSON，可额外要求 CSV；
- 文字与评论附图分别保存在 `text` 和 `images`，多张图片保持平台返回顺序；
- B 站可选附带 `--with-transcript` 输出带时间轴的 JSON/TXT 字幕文稿；
- 使用相同的视频和输出路径再次执行，会从已有进度继续；
- meta.main_complete=false 或 meta.last_has_more=1 时，结果可能还没有采集完；
- 默认抽样部分有回复的一级评论线程；需要全部命中线程时，可以明确要求回复抽样比例为 100%。

## 安全边界

- 只采集用户可见的公开评论；
- 不读取、导出或要求提供 Cookie、Token、密码或认证请求头；
- 不绕过登录、权限、验证码、风控或速率限制；
- 不轮换代理、伪造指纹或逆向平台签名。

## 手动运行脚本（可选）

一般不需要手动运行。排查问题或编写自动化流程时，可以直接调用：

~~~
python <skill-root>/scripts/comments_catcher.py <VIDEO> \
  --output <OUTPUT.json> --with-sub
~~~

节奏与抽样参数（间隔、抖动、抽样率、种子）默认读取技能目录的 config.json，只有需要临时覆盖时才在命令行追加 `--delay`、`--jitter-range`、`--sub-rate`、`--seed`。

完整参数见 skills/comments-catcher/references/cli-reference.md；故障排查见 skills/comments-catcher/references/troubleshooting.md。

## 项目结构

~~~
skills/comments-catcher/
├── SKILL.md                         # Agent 使用说明
├── scripts/comments_catcher.py      # 采集程序
├── scripts/smoke_validate.py        # 安装校验
└── references/                      # 参数、输出格式和故障排查
~~~

根目录的安装器、插件清单和测试文件用于发布与维护；实际被 Agent 加载的是 skills/comments-catcher/。

## 开发者校验

~~~
python skills/comments-catcher/scripts/smoke_validate.py
python -m unittest discover -s tests -p "test*.py"
~~~

## License

MIT
