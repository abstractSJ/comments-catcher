# 命令行参考

安装后仍可运行 `python <comments_catcher.py 的绝对路径> --help` 查看权威帮助。脚本可从 Codex 或 Claude Code 加载的同一 skill 目录运行。

## 基本语法

```text
python comments_catcher.py [VIDEO] [OPTIONS]
```

`VIDEO` 支持：

- 抖音视频 URL 或 `aweme_id`。
- B 站 `https://www.bilibili.com/video/BV...`、`av...`、BV 号或 av 号。

## 平台与输入参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `VIDEO` | 按操作决定 | 抖音/B 站视频 URL 或平台 ID。 |
| `--platform auto|douyin|bilibili` | `auto` | 自动识别或显式指定平台。健康检查无视频时默认检查抖音。 |
| `--session NAME` | 按平台决定 | WebBridge 会话名；抖音默认 `douyin`，B 站默认 `bili-comments`。也可用 `COMMENTS_CATCHER_SESSION`。 |

## 采集与输出参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--output FILE` | 按平台和视频 ID 命名 | JSON 输出路径。建议使用绝对路径。 |
| `--csv FILE` | 不输出 | 可选 CSV 路径；JSON 仍是完整、可恢复的规范输出。 |
| `--with-sub` | 关闭 | 采集按 `--sub-rate` 抽样命中的二级回复线程。技能工作流默认添加此参数。 |
| `--subs-only` | 关闭 | 复用已有一级评论结果，只补采二级回复。 |
| `--sub-rate RATE` | `0.5` | 二级回复线程抽样比例，范围为 `0` 到 `1`。需要 B 站全部回复时显式使用 `1`。缺省读 `config.json`。 |
| `--seed INTEGER` | `42` | 确定性抽样种子。缺省读 `config.json`。 |
| `--max-pages N` | 不限制 | 本次最多新增采集的一级评论页数；达到上限时 `main_complete` 可能为 `false`。 |
| `--delay SECONDS` | `5.0` | 请求、翻页或批次之间的基础等待秒数；实现规定最低为 `3.0` 秒。缺省读 `config.json`。 |
| `--jitter-range RANGE` | `0.6` | 间隔随机抖动总幅度，`0.6` 表示实际间隔在基础值的 ±30% 内波动；范围 `0.0` 到 `2.0`。缺省读 `config.json`。 |
| `--config FILE` | 技能目录 `config.json` | 自定义本地配置文件路径；文件不存在时报错。 |
| `--include-user-identifiers` | 关闭 | 显式保存稳定 UID 与 IP 属地；默认留空以减少个人数据。 |

## 本地配置文件 config.json

节奏与抽样参数按“命令行参数 > 平台专属小节 > 顶层通用配置 > 内置默认”的优先级合成。未使用 `--config` 时，采集器自动读取技能目录（`scripts/` 上一级）下的 `config.json`；文件不存在时全部使用内置默认。

顶层写两个平台通用的值；`"douyin"` / `"bilibili"` 小节只写该平台需要覆盖的键，省略的键沿用顶层，例如：

```json
{
  "delay": 3.0,
  "sub_rate": 0.5,
  "douyin": { "delay": 5.0 },
  "bilibili": { "sub_rate": 1.0 }
}
```

支持的键（顶层与平台小节相同）：

| 键 | 类型 | 内置默认 | 说明 |
|---|---|---:|---|
| `delay` | 数值 | `5.0` | 请求基础间隔秒数，最低 `3.0`。 |
| `jitter_range` | 数值 | `0.6` | 间隔随机抖动总幅度，`0.6` 表示 ±30%，范围 `0.0` 到 `2.0`。 |
| `sub_rate` | 数值 | `0.5` | 二级回复线程抽样比例，范围 `0` 到 `1`。 |
| `seed` | 整数 | `42` | 确定性抽样种子。 |

以 `_` 开头的键作为注释被忽略；其他未知键、非数值或超出范围的值都会在发起任何请求前报错。`sub_rate` 与 `seed` 修改后不能用于已有抽样决策的恢复文件。

## WebBridge 参数

| 参数 | 说明 |
|---|---|
| `--daemon-url URL` | WebBridge daemon URL，也可通过 `COMMENTS_CATCHER_DAEMON_URL` 设置；只允许 loopback 地址。 |
| `--health-check` | 只检查 daemon、当前目标平台页面与验证状态，不导航、不采集评论。 |
| `--prepare-page` | 由 Agent 打开目标视频并等待页面就绪后退出；普通采集默认自动执行同样步骤。 |
| `--reuse-current-page` | 显式复用当前会话页面，不自动导航；仅用于已确认页面正确的场景。 |
| `-h, --help` | 显示帮助。 |
| `--version` | 显示采集器版本。 |

## 示例

示例刻意不带 `--delay`、`--jitter-range`、`--sub-rate`、`--seed`，使 `config.json` 中的用户配置生效；仅当需要临时覆盖配置时才追加这些参数。

抖音：

```bash
python ./skills/comments-catcher/scripts/comments_catcher.py \
  "https://www.douyin.com/video/0000000000000000000" \
  --platform douyin --output ./douyin-comments.json \
  --with-sub
```

B 站：

```bash
python ./skills/comments-catcher/scripts/comments_catcher.py \
  "https://www.bilibili.com/video/BV1qnuq6dEga" \
  --platform bilibili --session bili-comments \
  --output ./bilibili-comments.json \
  --with-sub
```

单独打开并预检两个平台页面：

```bash
python ./skills/comments-catcher/scripts/comments_catcher.py \
  "https://www.douyin.com/video/0000000000000000000" \
  --platform douyin --prepare-page
python ./skills/comments-catcher/scripts/comments_catcher.py \
  "https://www.bilibili.com/video/BV1qnuq6dEga" \
  --platform bilibili --prepare-page
```

正常采集时 Agent 会自动导航，不需要用户预先打开页面。`--reuse-current-page` 只应在确认当前会话页面正确时使用；任何导航都不得用于隐藏页面、绕过登录或自动处理 CAPTCHA。页面要求验证时，由用户在同一个可见浏览器会话中手动完成。

## 输出与恢复

输出为 UTF-8 JSON，结构由 `output-schema-v2.json` 定义。`meta` 必须包含 `platform`、`video_id` 以及分页、计数、抽样和完成状态字段；抖音额外保存 `aweme_id`，B 站额外保存 `bvid`，页面可读时保存 `oid`。

已有同一平台、同一视频的 JSON 会作为断点恢复文件继续使用；不同平台、视频或抽样配置不会被静默覆盖。`main_complete=false` 或 `last_has_more=1` 表示结果不应被描述为全量完成。

实现不得提供代理轮换、指纹伪造、签名逆向、Cookie 导入/导出或 CAPTCHA 自动化参数。任何输出和日志都不得包含 Cookie、令牌、认证头或浏览器存储。
