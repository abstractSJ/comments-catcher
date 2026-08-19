# 故障排查

## 找不到 `comments_catcher.py`

1. 确认宿主加载的是完整的 `comments-catcher` skill 目录。
2. 确认文件位于 `skills/comments-catcher/scripts/comments_catcher.py`。
3. 独立安装时确认复制的是整个 `skills/comments-catcher` 文件夹，而非单独的 `SKILL.md`。
4. 重新运行安装器；安装器会在复制前检查必需文件。

不要从当前工作目录猜测相对路径，也不要从不可信来源单独下载同名脚本。

## 平台识别错误

- 抖音使用视频 URL 或 15 位以上数字 `aweme_id`。
- B 站使用完整视频 URL、`BV...` 或 `av...`；当前无法离线解析没有展开标识的 `b23.tv` 短链，请提供展开后的完整 URL、BV 号或 av 号。无需预先在浏览器中打开页面。
- 自动识别不确定时，显式指定 `--platform douyin` 或 `--platform bilibili`。
- 恢复文件的平台和视频 ID 必须与本次命令一致，不能用 B 站文件续跑抖音任务，反之亦然。

## WebBridge 不可用

普通采集采用固定预热链路：幂等启动 WebBridge daemon，调用 `list_tabs` 轻量探测并有限等待扩展连接，然后才导航页面；不要求用户预先打开页面。首次出现 `no extension connected` 通常是扩展冷启动窗口，采集器会自动重试，Agent 不应立即打断用户。只有有限重试后仍失败，才检查 WebBridge 扩展是否安装并连接到浏览器：

```text
python <skill-root>/scripts/comments_catcher.py <VIDEO> --platform douyin --prepare-page
python <skill-root>/scripts/comments_catcher.py <VIDEO> --platform bilibili --prepare-page
```

如果仍无法连接，才需要用户检查扩展/daemon；不要改用 Cookie 文本、复制认证请求头或新建隐藏配置文件规避登录。

## 会话未登录或页面未就绪

- 抖音默认会话名为 `douyin`，Agent 会自动导航，页面需要是 `douyin.com` 且网页签名组件已经加载。
- B 站默认会话名为 `bili-comments`，Agent 会自动导航，页面需要是 `bilibili.com` 且页面已加载 `window.__INITIAL_STATE__.aid`。
- 如果本地会话名不同，使用 `--session NAME`；只有登录失效时才请用户在同一个可见会话中手动登录。

## 出现 CAPTCHA

立即暂停。由用户本人在同一个浏览器会话中手动完成 CAPTCHA，然后明确告知 Agent 可以重试。Agent 不得点击、识别、求解验证码，也不得调用代理或打码服务。

## 评论结果为空

可能原因：目标视频没有公开评论、页面仍未完成加载、视频不可见/已删除、登录状态失效，或平台页面结构发生变化。先通过现有浏览器会话人工确认页面是否正常可见，不要尝试访问私有接口或逆向签名作为替代方案。

## 请求过快或被限制

保持默认 5 秒或更慢的间隔，减少 `--max-pages`，并稍后重试。出现 429、B 站 `-412`、连续空响应或风控提示时应停止。不得轮换代理、伪造指纹或提高并发来绕过限制。

## 回复线程数量与预期不同

默认只抽样 50% 的一级评论线程。检查输出中的 `meta.sub_rate` 与 `meta.seed`。如果需要 B 站所有命中线程的回复，使用 `--sub-rate 1`；这仍然受平台返回和页面可见性限制。

## 输出未完成

`meta.main_complete=false` 或 `meta.last_has_more=1` 可能表示达到页数上限、仍有下一页、人工中断或遇到可恢复错误。结合 `last_cursor`、`fetched_count` 和 `total_count` 判断，不要把不完整输出描述为全量数据。

## JSON Schema 校验失败

1. 新任务使用同一技能目录中的 `references/output-schema-v2.json`。
2. 历史抖音 v1 文件只用于迁移/识别，不要把 B 站输出写成 `aweme_id`。
3. 确认 `meta.platform`、`meta.video_id` 和 `comments` 存在。
4. 若存在 `subs`，每项必须是二元数组：第一个元素为 `root_cid`，第二个元素为评论数组。
5. 不要手工伪造缺失字段；应修复产生输出的流程后重新采集。

## PowerShell 阻止运行本地脚本

可以在当前进程范围内使用组织允许的执行策略，或直接手工复制技能目录。不要通过下载并执行远程命令绕过策略。若设备由组织管理，请遵循管理员要求。
