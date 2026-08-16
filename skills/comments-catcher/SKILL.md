---
name: comments-catcher
description: 使用用户现有、已登录的 WebBridge 浏览器会话采集抖音或哔哩哔哩视频的公开评论，支持一级评论、可复现抽样的回复线程、断点续采、博主主页按发布时间新→旧批量采集多个视频和 JSON/CSV 导出；当用户要求获取、导出、抽样或整理抖音/B站评论，或批量拉取某个 UP 主/博主多个视频的评论时使用。
---

# Comments Catcher

使用技能目录内的 `scripts/comments_catcher.py`，不要根据当前工作目录猜测脚本位置。宿主如果提供了已加载 skill 的根目录就使用该目录；否则使用当前 `SKILL.md` 所在目录的同级 `scripts/`。不得全盘搜索脚本，也不得只复制 `SKILL.md`。

## 支持的平台

- 抖音：抖音视频 URL 或 `aweme_id`；默认 WebBridge 会话为 `douyin`。
- B 站：`https://www.bilibili.com/video/BV...`、`av...` 或 BV/av 标识；默认 WebBridge 会话为 `bili-comments`。
- 博主主页批量模式：`--space` 接受 B 站 `https://space.bilibili.com/<mid>` 或抖音 `https://www.douyin.com/user/<sec_uid>` 主页链接，按发布时间新→旧串行采集该博主多个视频的评论，详见下方“博主主页批量流程”。
- 省略 `--platform` 时根据 URL/ID 自动识别；也可显式使用 `--platform douyin` 或 `--platform bilibili`。
- Agent 会通过 WebBridge 自动启动 daemon（若尚未运行）、打开目标视频并等待页面就绪；用户不需要预先打开页面。只有页面要求登录或 CAPTCHA 时，才请用户在同一个可见会话中手动处理。会话名可用 `--session` 覆盖。

## 安全边界

- 只读取用户正常可见的公开页面与公开评论。
- 只复用用户已有的可见 WebBridge 浏览器会话；不得读取、导出、打印或要求用户提供 Cookie、令牌、认证头、Local Storage、Session Storage 或密码。
- 不绕过登录、权限、地区限制、风控或速率限制；不轮换代理、伪造指纹、逆向签名或重放未授权请求。
- 检测到登录失效、CAPTCHA 或明确风控提示时停止，要求用户在同一个浏览器会话中手动处理后再重试；Agent 不点击、识别或求解 CAPTCHA。

## 标准流程

1. 从请求中提取平台、视频 URL/ID、输出路径、一级评论页数上限、等待间隔、随机抖动幅度、回复抽样率和种子。
2. **用户没有明确要求节奏或抽样参数时，禁止在命令行传入 `--delay`、`--jitter-range`、`--sub-rate`、`--seed`**。省略这些参数，采集器才会自动读取技能目录 `config.json` 中的用户配置（文件不存在时才用内置默认）；一旦在命令行写出这些参数，用户的 config.json 配置就会被覆盖。只有用户明确提出要求（例如"间隔放慢到 8 秒"、"要全部回复"）时，才传入对应参数。实现允许的最小基础间隔是 3 秒；用户要求更慢时照做。
3. 正常采集直接调用同一技能目录中的 `comments_catcher.py`；采集器会自动启动 WebBridge、导航到目标视频、等待页面初始化并执行只读验证。不要要求用户预先打开页面。
4. 如果需要分步预检，可先调用 `comments_catcher.py <VIDEO> --platform <platform> --prepare-page`；它会由 Agent 自动打开页面后退出，再继续正式采集。不要在导航前单独把 `--health-check` 当作页面打开步骤。
5. 用绝对脚本路径和参数数组调用采集器。标准调用**只带**平台、会话、输出路径和 `--with-sub`，不带任何节奏/抽样参数：

   ```text
   python <skill-root>/scripts/comments_catcher.py \
     "https://www.bilibili.com/video/BV1qnuq6dEga" \
     --platform bilibili --session bili-comments \
     --output <OUTPUT.json> --with-sub
   ```

   示例刻意省略 `--delay`、`--jitter-range`、`--sub-rate`、`--seed`，这样采集器才会读取 `config.json` 里的用户配置；照抄示例时再自行补上这些参数属于错误用法。仅当用户在本次请求中明确要求覆盖某个值时才追加对应参数。

6. 输出路径已有同一平台、同一视频的恢复文件时，将其视为断点继续；不要覆盖与目标视频不匹配的文件。采集器会校验平台、视频 ID 和已有抽样配置。
7. 交付前读取同一技能目录的 `references/output-schema-v2.json`，确认输出包含 `meta`、`comments`，并检查平台、视频 ID、计数、游标、完成状态、抽样率、种子和恢复字段。

## 博主主页批量流程

用户给出博主主页（B 站 `space.bilibili.com/<mid>` 或抖音 `douyin.com/user/<sec_uid>`）并要求采集其视频评论时，进入批量模式：

1. **必须先询问用户要采集多少个最近视频**（例如“最近 20 个”），用户给出明确数字前禁止调用采集器；`--max-videos` 没有默认值，采集器缺少该参数会直接报错。用户说“全部”时，告知其请求量与风控风险，请其确认一个上限数字后再执行。
2. 节奏与抽样参数禁令同样适用于批量模式：用户没有明确要求时，禁止在命令行传入 `--delay`、`--jitter-range`、`--sub-rate`、`--seed`、`--video-delay`、`--dwell`，让 `config.json`（含平台小节）生效。
3. 标准调用只带 `--space`、平台、会话、`--max-videos`、`--output-dir` 和 `--with-sub`：

   ```text
   python <skill-root>/scripts/comments_catcher.py \
     --space "https://space.bilibili.com/289706107" \
     --platform bilibili --session bili-comments \
     --max-videos 20 --output-dir <OUTPUT_DIR> --with-sub
   ```

4. 批量模式的行为（向用户说明时按此描述）：采集器打开主页并滚动收集视频清单（页面默认即按发布时间新→旧排列），然后**串行**逐视频处理——打开视频页、触发静音播放并在页面停留 `dwell` 秒模拟真实观看、再采集评论；相邻视频之间按 `video_delay` 秒间隔等待。单个视频失败会记录原因并跳过，连续 3 个失败判定为风控或登录失效并中止任务。
5. 断点分两层：`<OUTPUT_DIR>/video_list.json` 记录每个视频的 done/failed 状态，重跑同一命令自动跳过已完成视频；每个视频的 `<video_id>.json` 仍保留页级断点，某个视频采到一半中断可精确续跑。中途中止后让用户重跑同一条命令即可。
6. `--output`、`--csv`、`--prepare-page`、`--reuse-current-page`、`--subs-only` 仅用于单视频模式，不能与 `--space` 同用。

## 输出检查

当前 v2 输出的 `meta` 字段包括：

`schema_version`、`collector_version`、`platform`、`video_id`、平台专属的 `aweme_id`/`bvid`、可用时的 B 站 `oid`、`all_count`、`fetched_count`、`sub_count`、`total_count`、`last_cursor`、`last_has_more`、`main_complete`、`main_page_count`、`sub_rate`、`seed`、`with_sub`、`sampled_cids`、`skipped_cids`、`sub_done_cids`、`updated_at`。

`comments` 是统一字段的一级评论数组；`subs`（存在时）是 `[[root_cid, comments_array], ...]`。`all_count` 是平台接口报告的总数，可能包含一级评论和回复，不得直接当作已抓取的一级评论数。`main_complete=false` 或 `last_has_more=1` 时，不得把结果描述为全量完成。

交付时报告输出路径、平台、视频 ID、一级评论数、回复数、抽样线程数、完成状态、最终游标、实际抽样率和种子；不得报告认证材料。

## 本地配置

技能目录下的 `config.json` 用于长期调控采集节奏与抽样，支持六个键：`delay`（请求基础间隔秒数，最低 3.0）、`jitter_range`（间隔随机抖动总幅度，0.6 表示 ±30%）、`sub_rate`（二级回复线程抽样比例 0–1）、`seed`（确定性抽样种子）、`video_delay`（批量模式相邻视频的基础间隔秒数，最低 5.0）、`dwell`（批量模式每个视频页面的停留浏览秒数，0 表示不停留）。顶层写两个平台通用的值；`"douyin"` / `"bilibili"` 小节只写该平台需要覆盖的键，省略的键沿用顶层。取值优先级为“命令行参数 > 平台小节 > 顶层通用 > 内置默认”。以 `_` 开头的键是注释，加载时忽略。**命令行参数始终优先于该文件，因此 Agent 调用采集器时默认不得携带上述六个参数，否则用户配置会被静默覆盖**；只有用户明确要求时才传参覆盖，也可用 `--config <PATH>` 指定其他配置文件。采集开始时程序会打印最终生效的参数与配置来源。已有抽样决策的恢复文件要求 `sub_rate` 与 `seed` 与原任务一致，修改它们只对新任务生效。

## 参考资料

- 安装与宿主兼容：`references/setup.md`
- 路径、平台适配与组件边界：`references/architecture.md`
- 命令行参数：`references/cli-reference.md`
- 安全与隐私：`references/safety-privacy.md`
- 故障排查：`references/troubleshooting.md`
- v2 输出约束：`references/output-schema-v2.json`
- v1 输出约束仅用于识别历史抖音结果：`references/output-schema-v1.json`
