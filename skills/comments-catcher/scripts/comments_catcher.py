#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comments Catcher：通过 Kimi WebBridge 采集抖音或 B 站视频评论。

本程序复用用户本机可见浏览器中的抖音或 B 站页面与现有登录会话，通过本地 WebBridge
在页面上下文内请求评论数据。程序只做串行、保守的读取操作；当页面出现可见人机
验证时会暂停并等待用户手动完成，不会尝试识别、点击、规避或绕过验证。

运行环境：Python 3.10+，仅使用标准库。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

COLLECTOR_VERSION = "0.5.0"
SCHEMA_VERSION = "2.0.0"
PLATFORM_AUTO = "auto"
PLATFORM_DOUYIN = "douyin"
PLATFORM_BILIBILI = "bilibili"
SUPPORTED_PLATFORMS = {PLATFORM_DOUYIN, PLATFORM_BILIBILI}
DOUYIN_HOSTS = {"www.douyin.com", "douyin.com"}
BILIBILI_HOSTS = {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}
DEFAULT_DAEMON_URL = "http://127.0.0.1:10086/command"
DEFAULT_SESSIONS = {
    PLATFORM_DOUYIN: "douyin",
    PLATFORM_BILIBILI: "bili-comments",
}
DEFAULT_DELAY = 5.0
MIN_DELAY = 3.0
DEFAULT_JITTER_RANGE = 0.6
# 抖动总幅度的上限；超过 2.0 时最小间隔会低于 0，失去限速意义。
MAX_JITTER_RANGE = 2.0
DEFAULT_SUB_RATE = 0.5
DEFAULT_SEED = 42
# 批量模式（--space）的视频级节奏：批量任务请求总量随视频数线性放大，
# 视频之间的间隔是比页级 delay 更主要的风控保护层。
DEFAULT_VIDEO_DELAY = 20.0
MIN_VIDEO_DELAY = 5.0
# 每个视频页面的停留浏览秒数；0 表示不停留，上限防止误配置成超长挂起。
DEFAULT_DWELL = 15.0
MAX_DWELL = 600.0
# 主页连续滚动多少轮都没有新视频出现时认为清单已经到底。
SPACE_LIST_STABLE_SCROLLS = 3
# 连续多少个视频失败就中止整个批量任务：单点失败通常是视频本身问题，
# 连续失败更可能是风控或登录失效，继续重试只会放大风险。
MAX_CONSECUTIVE_VIDEO_FAILURES = 3
SPACE_MANIFEST_NAME = "video_list.json"
SPACE_MANIFEST_SCHEMA = 1

# 技能目录（scripts/ 的上一级）下的 config.json 是默认本地配置文件；
# 用 __file__ 定位而不是当前工作目录，保证从任意目录调用都读到同一份配置。
SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = SKILL_ROOT / "config.json"

EXIT_OK = 0
EXIT_ARGUMENT = 2
EXIT_DAEMON = 3
EXIT_PAGE = 4
EXIT_MANUAL_ACTION = 5
EXIT_COLLECTION = 6


class ConfigError(ValueError):
    """命令行参数、恢复文件或本地配置不符合运行约束。"""


class DaemonUnavailable(RuntimeError):
    """无法连接本机 Kimi WebBridge daemon。"""


class PageNotReady(RuntimeError):
    """浏览器会话不存在、页面不是抖音，或页面 SDK 尚未就绪。"""


class ManualActionRequired(RuntimeError):
    """页面需要用户手动完成登录或人机验证。"""


class CollectionError(RuntimeError):
    """评论接口或本地持久化在重试后仍无法完成。"""


def start_webbridge_daemon() -> bool:
    """按 WebBridge 官方路径尝试启动 daemon；已运行时通常是幂等操作。"""
    if os.name == "nt":
        executable = Path.home() / ".kimi-webbridge" / "bin" / "kimi-webbridge.exe"
    else:
        executable = Path.home() / ".kimi-webbridge" / "bin" / "kimi-webbridge"
    if not executable.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(executable), "start"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


@dataclass(frozen=True)
class CollectorConfig:
    """
    采集器运行参数。

    参数：
        platform: 目标平台，支持 douyin 或 bilibili。
        daemon_url: 本机 WebBridge HTTP 命令地址，仅允许 loopback 主机。
        session: WebBridge 会话名，同一次任务必须保持一致。
        request_timeout: Python 到 daemon 的单次请求超时秒数。
        js_timeout_ms: 页面内 fetch 的兜底超时毫秒数。
        max_retry: 评论接口失败后的最大尝试次数。
        retry_base_delay: 指数退避的基准秒数。
        delay: 相邻评论请求之间的基础间隔秒数。
        jitter_range: 间隔随机抖动总幅度；0.6 表示 ±30%。
        video_delay: 批量模式下相邻两个视频之间的基础间隔秒数。
        dwell: 批量模式下每个视频页面的停留浏览秒数。
        page_size: 每页请求条数，保持与网页常用页大小一致。
        captcha_wait_seconds: 可见验证弹窗允许人工处理的最长等待时间。
        include_user_identifiers: 是否保存稳定用户 ID 与 IP 属地字段。
    """

    platform: str = PLATFORM_DOUYIN
    daemon_url: str = DEFAULT_DAEMON_URL
    session: str = DEFAULT_SESSIONS[PLATFORM_DOUYIN]
    request_timeout: float = 60.0
    js_timeout_ms: int = 25_000
    max_retry: int = 5
    retry_base_delay: float = 3.0
    delay: float = DEFAULT_DELAY
    jitter_range: float = DEFAULT_JITTER_RANGE
    video_delay: float = DEFAULT_VIDEO_DELAY
    dwell: float = DEFAULT_DWELL
    page_size: int = 20
    captcha_wait_seconds: int = 180
    include_user_identifiers: bool = False

    def validate(self) -> None:
        """在发生任何网络访问前验证所有配置，避免错误参数产生无意义请求。"""
        if self.platform not in SUPPORTED_PLATFORMS:
            raise ConfigError(f"platform 必须是 {', '.join(sorted(SUPPORTED_PLATFORMS))}")
        parsed = urllib.parse.urlparse(self.daemon_url)
        if parsed.scheme not in {"http", "https"}:
            raise ConfigError("daemon URL 只允许 http/https")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("daemon URL 必须指向本机 loopback 地址，禁止连接远程浏览器控制服务")
        if not self.session.strip():
            raise ConfigError("WebBridge session 不能为空")
        if not math.isfinite(self.delay) or self.delay < MIN_DELAY:
            raise ConfigError(f"请求间隔必须是有限数且不小于 {MIN_DELAY:.1f} 秒")
        if not math.isfinite(self.jitter_range) or not 0.0 <= self.jitter_range <= MAX_JITTER_RANGE:
            raise ConfigError(
                f"jitter_range 必须是 0.0 到 {MAX_JITTER_RANGE:.1f} 之间的有限数"
            )
        if not math.isfinite(self.video_delay) or self.video_delay < MIN_VIDEO_DELAY:
            raise ConfigError(f"视频间隔必须是不小于 {MIN_VIDEO_DELAY:.1f} 秒的有限数")
        if not math.isfinite(self.dwell) or not 0.0 <= self.dwell <= MAX_DWELL:
            raise ConfigError(f"页面停留时长必须是 0 到 {MAX_DWELL:.0f} 秒之间的有限数")
        if self.max_retry < 1:
            raise ConfigError("max_retry 必须至少为 1")
        if self.page_size < 1 or self.page_size > 50:
            raise ConfigError("page_size 必须在 1 到 50 之间")
        if self.captcha_wait_seconds < 5:
            raise ConfigError("captcha_wait_seconds 必须至少为 5 秒")


class BridgeLike(Protocol):
    """采集器依赖的最小 WebBridge 接口，便于使用合成数据进行离线测试。"""

    def fetch_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """请求一页评论接口数据。"""

    def wait_captcha_clear(self) -> None:
        """确认页面没有可见验证；若有则等待用户手动处理。"""

    def main_cursor_start(self) -> int:
        """返回目标平台一级评论分页的初始游标。"""

    def sub_cursor_start(self) -> int:
        """返回目标平台二级评论分页的初始游标。"""


class WebBridgeClient:
    """Kimi WebBridge 本地 daemon 客户端及页面状态管理器。"""

    # 从主页 DOM 中提取视频卡片链接。B 站与抖音主页默认都按发布时间新→旧排序，
    # 因此 Map 的插入顺序（即 DOM 顺序）就是采集顺序，无需解析相对时间文本。
    VIDEO_CARDS_JS = r"""
    (() => {
      const map = new Map();
      for (const a of document.querySelectorAll('a[href*="/video/"]')) {
        const href = (a.href || '').split('?')[0];
        if (!href) continue;
        const title = (a.getAttribute('title') || a.textContent || '').trim();
        const prev = map.get(href);
        // 同一视频常有封面与标题两个锚点；优先保留非空标题的那一条。
        if (!prev || (!prev.title && title)) {
          map.set(href, {href, title: title.slice(0, 200)});
        }
      }
      return [...map.values()];
    })()
    """

    # 模拟真实用户浏览：静音触发播放（浏览器自动播放策略通常只允许静音的脚本化
    # 播放），随后轻微下滚，模拟用户看完开头后向评论区移动视线。不点击任何按钮、
    # 不修改播放以外的页面状态。
    SIMULATE_BROWSE_JS = r"""
    (() => {
      const video = document.querySelector('video');
      let play_requested = false;
      if (video) {
        video.muted = true;
        try {
          const p = video.play();
          if (p && typeof p.catch === 'function') p.catch(() => {});
          play_requested = true;
        } catch (error) {}
      }
      window.scrollTo(0, Math.min(600, Math.max(0, document.body.scrollHeight - window.innerHeight)));
      return {has_video: Boolean(video), play_requested};
    })()
    """

    CAPTCHA_CHECK_JS = r"""
    (() => {
      const visible = (el) => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 50 && r.height > 50 && s.visibility !== 'hidden' && s.display !== 'none';
      };
      const frame = Array.from(document.querySelectorAll('iframe')).find((f) =>
        /rc-verify|verifycenter|captcha|nocaptcha|yidun/i.test(f.src || '') && visible(f));
      if (frame) return {state: 'visible', kind: 'iframe'};
      const node = Array.from(document.querySelectorAll(
        '[class*=captcha],[class*=yidun],[class*=verify],[id*=captcha],[id*=verify]'
      )).find(visible);
      if (node) return {state: 'visible', kind: 'element'};
      return {state: 'clear'};
    })()
    """

    def __init__(self, config: CollectorConfig):
        self.config = config
        # B 站评论接口使用 aid(oid)，必须从当前可见页面读取，不能从外部请求猜测。
        self.oid: int | None = None
        self._daemon_start_attempted = False

    def _send_once(self, request: urllib.request.Request) -> dict[str, Any]:
        """发送一次 HTTP 请求；连接失败交由上层决定是否启动 daemon。"""
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            # HTTP 错误说明本机 daemon 已经响应；例如“session 没有 tab”应归类为
            # 页面/会话未就绪，让上层继续给出导航或人工登录提示，而不是误报 daemon 离线。
            raise PageNotReady(f"WebBridge HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError):
            raise
        except json.JSONDecodeError as exc:
            raise DaemonUnavailable("WebBridge 返回了无法解析的响应") from exc

    def send(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """向本机 daemon 发送命令；连接拒绝时先自动启动一次 daemon。"""
        body = json.dumps(
            {"action": action, "args": args, "session": self.config.session},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.config.daemon_url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            return self._send_once(request)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if self._daemon_start_attempted:
                raise DaemonUnavailable(f"无法连接 Kimi WebBridge daemon: {exc}") from exc
            self._daemon_start_attempted = True
            if not start_webbridge_daemon():
                raise DaemonUnavailable(f"无法连接 Kimi WebBridge daemon: {exc}") from exc
            time.sleep(1)
            try:
                return self._send_once(request)
            except (urllib.error.URLError, TimeoutError, OSError) as retry_exc:
                raise DaemonUnavailable(
                    f"无法连接 Kimi WebBridge daemon（已尝试自动启动）：{retry_exc}"
                ) from retry_exc

    def eval_js(self, code: str) -> Any:
        """在当前会话页面内执行 JavaScript，并统一解析 JSON 字符串结果。"""
        response = self.send("evaluate", {"code": code})
        if not response.get("ok"):
            message = response.get("error", {}).get("message", "evaluate 失败")
            raise PageNotReady(str(message))
        value = response.get("data", {}).get("value")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def page_status(self) -> dict[str, Any]:
        """读取平台所需的最小页面状态，不读取 Cookie、HTML 或用户资料。"""
        if self.config.platform == PLATFORM_BILIBILI:
            code = r"""
            (() => {
              const state = window.__INITIAL_STATE__ || {};
              return {
                host: location.hostname,
                url: location.href,
                page_ready: Boolean(state.aid),
                oid: state.aid || null,
                bvid: state.bvid || null
              };
            })()
            """
            error_message = "无法读取 B 站页面状态"
        else:
            code = r"""
            (() => ({
              host: location.hostname,
              url: location.href,
              signer_ready: typeof window.byted_acrawler === 'object'
            }))()
            """
            error_message = "无法读取抖音页面状态"
        result = self.eval_js(code)
        if not isinstance(result, dict):
            raise PageNotReady(error_message)
        return result

    def main_cursor_start(self) -> int:
        """返回一级评论 API 的首个游标；B 站分页从 1 开始，抖音从 0 开始。"""
        return 1 if self.config.platform == PLATFORM_BILIBILI else 0

    def sub_cursor_start(self) -> int:
        """返回二级评论 API 的首个游标；B 站使用 pn=1。"""
        return 1 if self.config.platform == PLATFORM_BILIBILI else 0

    def captcha_state(self) -> str:
        """
        返回验证状态：clear、visible 或 check_failed。

        检测失败与“存在验证码”是两种不同故障。区分三态可以避免浏览器会话断开时
        被误报为验证码，从而让用户在一个不存在的页面上徒劳等待。
        """
        try:
            result = self.eval_js(self.CAPTCHA_CHECK_JS)
            if isinstance(result, dict) and result.get("state") in {"clear", "visible"}:
                return str(result["state"])
            return "check_failed"
        except (DaemonUnavailable, PageNotReady):
            return "check_failed"

    def wait_captcha_clear(self) -> None:
        """若页面出现可见验证则停止数据请求，等待用户在浏览器中手动完成。"""
        state = self.captcha_state()
        if state == "clear":
            return
        if state == "check_failed":
            raise PageNotReady("无法检查页面验证状态，请确认 WebBridge 会话与抖音页面仍然可用")

        print(
            "[需要人工处理] 检测到可见人机验证。请在浏览器中手动完成，程序暂停请求并等待。",
            flush=True,
        )
        waited = 0
        while waited < self.config.captcha_wait_seconds:
            time.sleep(5)
            waited += 5
            state = self.captcha_state()
            if state == "clear":
                print(f"[验证已完成] 等待 {waited} 秒后继续")
                return
            if state == "check_failed":
                raise PageNotReady("等待验证期间浏览器会话不可用")
        raise ManualActionRequired(
            f"{self.config.captcha_wait_seconds} 秒内未完成可见验证；进度已保存，可稍后续跑"
        )

    def check_page_ready(self, video_id: str | None = None) -> dict[str, Any]:
        """确认当前会话指向目标平台页面，并缓存 B 站评论所需的 aid。"""
        status = self.page_status()
        host = status.get("host")
        if self.config.platform == PLATFORM_BILIBILI:
            if host not in BILIBILI_HOSTS:
                raise PageNotReady(f"当前会话不是 B 站页面（host={host!r}）")
            if not status.get("page_ready") or not status.get("oid"):
                raise PageNotReady("B 站页面尚未完成初始化，请稍候或刷新页面")
            try:
                self.oid = int(status["oid"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PageNotReady("无法从 B 站页面读取视频 aid") from exc
        else:
            if host not in DOUYIN_HOSTS:
                raise PageNotReady(f"当前会话不是抖音页面（host={host!r}）")
            if not status.get("signer_ready"):
                raise PageNotReady("抖音页面尚未完成初始化，请稍候或刷新页面")
        return status

    def health_check(self) -> tuple[dict[str, Any], int]:
        """执行不采集评论的健康检查，并返回机器可读结果与稳定退出码。"""
        result: dict[str, Any] = {
            "ok": False,
            "daemon_reachable": False,
            "platform": self.config.platform,
            "session": self.config.session,
            "page_ready": False,
            "host": None,
            "signer_ready": False,
            "page_data_ready": False,
            "captcha_state": "check_failed",
        }
        try:
            status = self.page_status()
            result["daemon_reachable"] = True
            result["host"] = status.get("host")
            if self.config.platform == PLATFORM_BILIBILI:
                result["page_data_ready"] = bool(status.get("page_ready"))
                result["signer_ready"] = result["page_data_ready"]
                result["page_ready"] = (
                    status.get("host") in BILIBILI_HOSTS
                    and result["page_data_ready"]
                    and bool(status.get("oid"))
                )
            else:
                result["signer_ready"] = bool(status.get("signer_ready"))
                result["page_data_ready"] = result["signer_ready"]
                result["page_ready"] = (
                    status.get("host") in DOUYIN_HOSTS
                    and result["signer_ready"]
                )
            result["captcha_state"] = self.captcha_state()
            if result["captcha_state"] == "visible":
                result["error"] = "需要在可见浏览器中手动完成人机验证"
                return result, EXIT_MANUAL_ACTION
            if not result["page_ready"] or result["captcha_state"] == "check_failed":
                platform_name = "B 站" if self.config.platform == PLATFORM_BILIBILI else "抖音"
                result["error"] = f"浏览器会话或{platform_name}页面未就绪"
                return result, EXIT_PAGE
            result["ok"] = True
            return result, EXIT_OK
        except DaemonUnavailable as exc:
            result["error"] = str(exc)
            return result, EXIT_DAEMON
        except PageNotReady as exc:
            result["daemon_reachable"] = True
            result["error"] = str(exc)
            return result, EXIT_PAGE

    def prepare_page(self, video_id: str, wait_seconds: int = 30) -> dict[str, Any]:
        """
        在当前 WebBridge 会话中打开目标视频页面并等待页面初始化。

        本方法只执行可见页面导航，不填写登录表单、不操作验证码。若页面要求登录或
        验证，调用方应提示用户在浏览器里手动处理。
        """
        if self.config.platform == PLATFORM_BILIBILI:
            url = f"https://www.bilibili.com/video/{video_id}"
        else:
            url = f"https://www.douyin.com/video/{video_id}"
        response = self.send(
            "navigate",
            {"url": url, "newTab": False, "group_title": "Comments Catcher"},
        )
        if not response.get("ok"):
            message = response.get("error", {}).get("message", "navigate 失败")
            raise PageNotReady(str(message))

        deadline = time.monotonic() + wait_seconds
        last_error = "页面初始化超时"
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                status = self.check_page_ready(video_id)
                captcha = self.captcha_state()
                if captcha == "visible":
                    raise ManualActionRequired("页面已打开，但需要用户手动完成人机验证")
                if captcha == "check_failed":
                    last_error = "无法检查页面验证状态"
                    continue
                return status
            except PageNotReady as exc:
                last_error = str(exc)
        raise PageNotReady(last_error)

    def prepare_space_page(self, space_url: str, wait_seconds: int = 30) -> None:
        """
        在当前 WebBridge 会话中打开博主主页并等待站点加载。

        主页不是视频页，没有 B 站 aid 或抖音签名就绪标志可读，因此这里只校验
        页面域名落在目标平台，再叠加验证弹窗检查。
        """
        response = self.send(
            "navigate",
            {"url": space_url, "newTab": False, "group_title": "Comments Catcher"},
        )
        if not response.get("ok"):
            message = response.get("error", {}).get("message", "navigate 失败")
            raise PageNotReady(str(message))

        expected_hosts = (
            BILIBILI_HOSTS if self.config.platform == PLATFORM_BILIBILI else DOUYIN_HOSTS
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                host = self.eval_js("location.hostname")
            except PageNotReady:
                continue
            if host not in expected_hosts:
                continue
            captcha = self.captcha_state()
            if captcha == "visible":
                raise ManualActionRequired("主页已打开，但需要用户手动完成人机验证")
            if captcha == "check_failed":
                continue
            return
        raise PageNotReady("博主主页加载超时，请确认主页链接可公开访问")

    def _scroll_to_bottom(self) -> None:
        """滚动主页触发懒加载；滚动失败不致命，下一轮收集会再次尝试。"""
        try:
            self.eval_js("window.scrollTo(0, document.body.scrollHeight)")
        except PageNotReady:
            pass

    def collect_video_list(self, max_videos: int, sleeper=time.sleep) -> list[dict[str, str]]:
        """
        在当前主页上边滚动边收集视频清单，按页面顺序（新→旧）返回。

        主页视频列表是懒加载的：只有滚动到底部才会渲染更多卡片。连续
        ``SPACE_LIST_STABLE_SCROLLS`` 轮滚动都没有新视频时认为清单已经到底。
        """
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        stable_rounds = 0
        while len(items) < max_videos and stable_rounds < SPACE_LIST_STABLE_SCROLLS:
            cards = self.eval_js(self.VIDEO_CARDS_JS)
            if not isinstance(cards, list):
                cards = []
            added = 0
            for card in cards:
                if not isinstance(card, dict):
                    continue
                video_id = parse_video_card_href(
                    str(card.get("href") or ""), self.config.platform
                )
                if not video_id or video_id in seen:
                    continue
                seen.add(video_id)
                items.append(
                    {"video_id": video_id, "title": str(card.get("title") or "")}
                )
                added += 1
                if len(items) >= max_videos:
                    break
            stable_rounds = stable_rounds + 1 if added == 0 else 0
            if len(items) >= max_videos:
                break
            self._scroll_to_bottom()
            rate_sleep(self.config, sleeper=sleeper)
        return items

    def simulate_browse(self) -> dict[str, Any]:
        """触发视频静音播放并轻微下滚，返回页面反馈供日志记录。"""
        result = self.eval_js(self.SIMULATE_BROWSE_JS)
        return result if isinstance(result, dict) else {}

    def fetch_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """在页面上下文请求一页评论，并返回统一的精简字段。"""
        if self.config.platform == PLATFORM_BILIBILI:
            return self._fetch_bilibili_json(endpoint, params)

        url = build_douyin_url(endpoint, params)
        include_identifiers = "true" if self.config.include_user_identifiers else "false"
        code = f"""
        (async () => {{
          const url = {json.dumps(url, ensure_ascii=False)};
          const guard = new Promise((resolve) => setTimeout(
            () => resolve({{__timeout: true}}), {self.config.js_timeout_ms}
          ));
          const request = fetch(url, {{credentials: 'include'}})
            .then(async (response) => ({{http_status: response.status, body: await response.json()}}))
            .catch((error) => ({{__fetch_error: String(error)}}));
          const wrapped = await Promise.race([request, guard]);
          if (wrapped.__timeout) return {{code: 'js_timeout', error: 'page fetch timeout'}};
          if (wrapped.__fetch_error) return {{code: 'fetch_error', error: wrapped.__fetch_error}};
          const data = wrapped.body || {{}};
          if (data.status_code !== 0) return {{
            code: data.status_code,
            error: data.status_msg || ('status_code=' + data.status_code),
            http_status: wrapped.http_status
          }};
          const includeIdentifiers = {include_identifiers};
          const comments = (data.comments || []).map((comment) => ({{
            cid: String(comment.cid),
            text: comment.text || '',
            create_time: comment.create_time || 0,
            digg_count: comment.digg_count || 0,
            reply_comment_total: comment.reply_comment_total || 0,
            nickname: (comment.user && comment.user.nickname) || '',
            uid: includeIdentifiers && comment.user ? String(comment.user.uid || '') : null,
            ip_label: includeIdentifiers ? (comment.ip_label || null) : null,
            level: comment.level || 0,
            is_hot: comment.is_hot || 0,
            reply_id: comment.reply_id || null,
            root_comment_id: comment.root_comment_id || null,
            is_author_digged: comment.is_author_digged || 0
          }}));
          return {{
            code: 0,
            comments,
            cursor: data.cursor != null ? Number(data.cursor) : 0,
            has_more: data.has_more ? 1 : 0,
            total: data.total != null ? Number(data.total) : 0
          }};
        }})()
        """
        result = self.eval_js(code)
        if not isinstance(result, dict):
            raise CollectionError("评论接口返回结构异常")
        return result

    def _fetch_bilibili_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """在 B 站页面内请求评论 API，避免把 Cookie 或认证头带出浏览器。"""
        if self.oid is None:
            raise PageNotReady("尚未从 B 站页面读取视频 aid")

        include_identifiers = "true" if self.config.include_user_identifiers else "false"
        page_size = self.config.page_size
        if endpoint == "comment/list":
            next_page = int(params.get("next", self.main_cursor_start()))
            url = build_bilibili_url(
                "x/v2/reply/main",
                {"type": 1, "oid": self.oid, "mode": 3, "next": next_page},
            )
            page_code = f"""
            const payload = data.data || {{}};
            const cursor = payload.cursor || {{}};
            const comments = Array.isArray(payload.replies) ? payload.replies : [];
            return {{
              code: 0,
              comments: comments.map(normalizeReply),
              cursor: Number(cursor.next != null ? cursor.next : {next_page}),
              has_more: cursor.is_end ? 0 : 1,
              total: Number(cursor.all_count || 0)
            }};
            """
        elif endpoint == "comment/list/reply":
            root_cid = str(params.get("root", ""))
            page_number = int(params.get("pn", self.sub_cursor_start()))
            url = build_bilibili_url(
                "x/v2/reply/reply",
                {
                    "type": 1,
                    "oid": self.oid,
                    "root": root_cid,
                    "ps": page_size,
                    "pn": page_number,
                },
            )
            page_code = f"""
            const payload = data.data || {{}};
            const page = payload.page || {{}};
            const cursor = payload.cursor || {{}};
            const comments = Array.isArray(payload.replies) ? payload.replies : [];
            const hasMore = cursor.is_end != null
              ? !cursor.is_end
              : comments.length >= {page_size};
            return {{
              code: 0,
              comments: comments.map(normalizeReply),
              cursor: {page_number} + 1,
              has_more: hasMore ? 1 : 0,
              total: Number(page.count || 0)
            }};
            """
        else:
            raise ConfigError(f"B 站不支持评论接口：{endpoint}")

        code = f"""
        (async () => {{
          const url = {json.dumps(url, ensure_ascii=False)};
          const guard = new Promise((resolve) => setTimeout(
            () => resolve({{__timeout: true}}), {self.config.js_timeout_ms}
          ));
          const request = fetch(url, {{credentials: 'include'}})
            .then(async (response) => ({{http_status: response.status, body: await response.json()}}))
            .catch((error) => ({{__fetch_error: String(error)}}));
          const wrapped = await Promise.race([request, guard]);
          if (wrapped.__timeout) return {{code: 'js_timeout', error: 'page fetch timeout'}};
          if (wrapped.__fetch_error) return {{code: 'fetch_error', error: wrapped.__fetch_error}};
          const data = wrapped.body || {{}};
          if (Number(data.code) !== 0) return {{
            code: data.code,
            error: data.message || ('code=' + data.code),
            http_status: wrapped.http_status
          }};
          const includeIdentifiers = {include_identifiers};
          const normalizeReply = (reply) => {{
            const member = reply.member || {{}};
            const content = reply.content || {{}};
            const control = reply.reply_control || {{}};
            return {{
              cid: String(reply.rpid),
              text: content.message || '',
              create_time: reply.ctime || 0,
              digg_count: reply.like || 0,
              reply_comment_total: reply.rcount || 0,
              nickname: member.uname || '',
              uid: includeIdentifiers && member.mid != null ? String(member.mid) : null,
              ip_label: includeIdentifiers ? (control.location || null) : null,
              level: (member.level_info || {{}}).current_level || 0,
              is_hot: 0,
              reply_id: reply.parent ? String(reply.parent) : null,
              root_comment_id: reply.root ? String(reply.root) : null,
              is_author_digged: 0
            }};
          }};
          {page_code}
        }})()
        """
        result = self.eval_js(code)
        if not isinstance(result, dict):
            raise CollectionError("B 站评论接口返回结构异常")
        return result


def utc_now_iso() -> str:
    """返回带时区的 ISO 8601 UTC 时间，便于跨平台解析。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def detect_platform(value: str | None, requested: str = PLATFORM_AUTO) -> str:
    """根据输入 URL/ID 推断平台；显式 ``--platform`` 优先于自动判断。"""
    if requested not in {PLATFORM_AUTO, *SUPPORTED_PLATFORMS}:
        raise ConfigError("--platform 必须是 auto、douyin 或 bilibili")
    if requested != PLATFORM_AUTO:
        return requested
    if not value:
        # 无视频参数时健康检查默认沿用历史行为，检查抖音会话。
        return PLATFORM_DOUYIN

    lowered = value.strip().lower()
    if (
        "bilibili.com" in lowered
        or "b23.tv" in lowered
        or re.search(r"(?i)(?:^|[/\s])bv[0-9a-z]{10}(?:[/\s?]|$)", value)
        or re.search(r"(?i)(?:^|[/\s])av\d+(?:[/\s?]|$)", value)
    ):
        return PLATFORM_BILIBILI
    if "douyin.com" in lowered or re.fullmatch(r"\d{15,}", value.strip()):
        return PLATFORM_DOUYIN
    raise ConfigError("无法自动识别平台，请使用 --platform douyin 或 --platform bilibili")


def extract_douyin_id(value: str) -> str:
    """从纯数字或抖音 URL 中提取 aweme_id。"""
    value = value.strip()
    if re.fullmatch(r"\d{15,}", value):
        return value

    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("modal_id", "aweme_id", "item_id", "vid"):
        candidates = query.get(key, [])
        if candidates and re.fullmatch(r"\d{15,}", candidates[0]):
            return candidates[0]

    path_match = re.search(r"/video/(\d{15,})", parsed.path)
    if path_match:
        return path_match.group(1)

    fallback = re.search(r"(?<!\d)(\d{15,})(?!\d)", value)
    if fallback:
        return fallback.group(1)
    raise ConfigError(f"无法从输入中解析抖音视频 ID: {value}")


def extract_bilibili_id(value: str) -> str:
    """从 B 站 BV/av 标识或视频 URL 中提取统一 video_id。"""
    value = value.strip()
    bvid_match = re.search(r"(?i)BV[0-9A-Za-z]{10}", value)
    if bvid_match:
        raw = bvid_match.group(0)
        return "BV" + raw[2:]

    avid_match = re.search(r"(?i)(?:^|[/\s?=&])av(\d+)(?:[/\s?&#]|$)", value)
    if avid_match:
        return f"av{avid_match.group(1)}"
    if re.fullmatch(r"\d+", value):
        return f"av{value}"
    raise ConfigError(
        f"无法从输入中解析 B 站视频 ID: {value}；请提供 BV 号、av 号或完整 B 站视频 URL"
    )


def extract_video_id(value: str, platform: str) -> str:
    """按平台解析目标视频 ID。"""
    if platform == PLATFORM_DOUYIN:
        return extract_douyin_id(value)
    if platform == PLATFORM_BILIBILI:
        return extract_bilibili_id(value)
    raise ConfigError(f"不支持的平台：{platform}")


def extract_space_target(value: str, platform: str) -> tuple[str, str]:
    """
    解析博主主页输入，返回（博主 ID, 规范化主页 URL）。

    B 站接受 ``space.bilibili.com/<mid>``（允许带无关查询参数）或裸 mid 数字；
    抖音接受 ``douyin.com/user/<sec_uid>``。规范化 URL 直接指向视频列表页，
    避免跟踪参数或默认 Tab 差异影响卡片收集。
    """
    value = value.strip()
    if platform == PLATFORM_BILIBILI:
        match = re.search(r"space\.bilibili\.com/(\d+)", value)
        if match:
            mid = match.group(1)
        elif re.fullmatch(r"\d+", value):
            mid = value
        else:
            raise ConfigError(
                f"无法从输入中解析 B 站博主 ID: {value}；"
                "请提供 space.bilibili.com/<mid> 主页链接或 mid 数字"
            )
        return mid, f"https://space.bilibili.com/{mid}/video"

    if platform == PLATFORM_DOUYIN:
        match = re.search(r"douyin\.com/user/([A-Za-z0-9_-]+)", value)
        if not match:
            raise ConfigError(
                f"无法从输入中解析抖音博主主页: {value}；"
                "请提供 douyin.com/user/<sec_uid> 主页链接"
            )
        sec_uid = match.group(1)
        return sec_uid, f"https://www.douyin.com/user/{sec_uid}"

    raise ConfigError(f"不支持的平台：{platform}")


def parse_video_card_href(href: str, platform: str) -> str | None:
    """
    从主页视频卡片链接中解析视频 ID；不是视频链接时返回 None。

    抖音图文等内容使用 /note/ 路径，不在评论采集范围内，自然被这里过滤掉。
    """
    if platform == PLATFORM_BILIBILI:
        match = re.search(r"(?i)/video/(BV[0-9A-Za-z]{10})", href)
        if match:
            raw = match.group(1)
            return "BV" + raw[2:]
        return None
    match = re.search(r"/video/(\d{15,})", href)
    return match.group(1) if match else None


def extract_aweme_id(value: str) -> str:
    """兼容旧调用方的抖音 ID 解析函数。"""
    return extract_douyin_id(value)


def validate_sub_rate(value: float) -> float:
    """验证二级回复线程抽样比例，拒绝 NaN、无穷和范围外数值。"""
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ConfigError("--sub-rate 必须是 0.0 到 1.0 之间的有限数")
    return value


# config.json 中允许的用户可调键；未知键直接报错，避免拼写错误的配置被静默忽略。
CONFIG_FILE_KEYS = {
    "delay": "请求基础间隔秒数",
    "jitter_range": "间隔随机抖动总幅度",
    "sub_rate": "二级回复线程抽样比例",
    "seed": "确定性抽样种子",
    "video_delay": "批量模式下相邻视频之间的基础间隔秒数",
    "dwell": "批量模式下每个视频页面的停留浏览秒数",
}


def _validate_config_entry(key: str, value: Any, context: str) -> Any:
    """
    校验单个配置键值并返回规范化结果。

    所有校验在任何网络请求之前完成；bool 会被显式拒绝，因为它是 int 的子类，
    不拦截会把 ``"delay": true`` 悄悄当成 1 秒使用。
    """
    if key not in CONFIG_FILE_KEYS:
        allowed = ", ".join(sorted(CONFIG_FILE_KEYS))
        raise ConfigError(f"{context}包含不支持的键 {key!r}；允许的键：{allowed}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"配置项 {key} 必须是数值，当前为 {value!r}")
    if key == "seed":
        if isinstance(value, float) and not value.is_integer():
            raise ConfigError("配置项 seed 必须是整数")
        value = int(value)
    return value


def load_config_file(path: Path) -> dict[str, Any]:
    """
    读取技能本地 config.json，支持顶层通用键与 douyin/bilibili 平台专属小节。

    顶层写通用配置；与平台同名的小节（``"douyin": {...}``）只写需要对该平台
    覆盖的键。任何解析或类型错误都必须在产生网络请求之前暴露，因此这里直接
    抛出 ConfigError 而不是回退默认值。

    参数：
        path: config.json 的路径，调用方需先确认文件存在。
    返回：
        字典；平台小节键映射到子字典，其余为 int/float 标量。
        以 "_" 开头的注释键（含小节内部）会被忽略。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取配置文件 {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件 {path} 的顶层必须是 JSON 对象")

    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if key in SUPPORTED_PLATFORMS:
            if not isinstance(value, dict):
                raise ConfigError(f"平台小节 {key!r} 必须是 JSON 对象")
            section: dict[str, Any] = {}
            for sub_key, sub_value in value.items():
                if sub_key.startswith("_"):
                    continue
                section[sub_key] = _validate_config_entry(
                    sub_key, sub_value, f"平台小节 {key!r} "
                )
            values[key] = section
            continue
        values[key] = _validate_config_entry(key, value, "配置文件")
    return values


def resolve_config_path(explicit: str | None) -> Path | None:
    """
    确定本次运行的配置文件路径。

    ``--config`` 显式指定的文件必须存在；未指定时回落到技能目录的默认
    config.json，两者都没有则返回 None，表示全部使用内置默认值。
    """
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ConfigError(f"--config 指定的配置文件不存在: {path}")
        return path
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    return None


def merge_platform_config(file_values: dict[str, Any], platform: str) -> dict[str, Any]:
    """
    把顶层通用配置与指定平台的小节合并；平台小节中的键覆盖顶层同名键。

    参数：
        file_values: load_config_file 的返回结果。
        platform: 目标平台（douyin 或 bilibili）。
    返回：
        只含标量键的字典，供 _resolve_option 查询。
    """
    merged = {k: v for k, v in file_values.items() if k not in SUPPORTED_PLATFORMS}
    section = file_values.get(platform)
    if isinstance(section, dict):
        merged.update(section)
    return merged


def _resolve_option(cli_value, file_values: dict[str, Any], key: str, default):
    """按“命令行参数 > 平台小节 > 顶层通用配置 > 内置默认”的优先级解析单个参数。"""
    if cli_value is not None:
        return cli_value
    if key in file_values:
        return file_values[key]
    return default


def validate_output_path(path: Path) -> None:
    """验证输出路径不是目录，并确保父目录存在。"""
    if path.exists() and path.is_dir():
        raise ConfigError(f"输出路径不能是目录: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def build_douyin_url(endpoint: str, params: dict[str, Any]) -> str:
    """组合抖音 Web 评论接口公共参数与业务参数，并进行标准 URL 编码。"""
    common: dict[str, Any] = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "platform": "PC",
        "downlink": "10",
    }
    common.update(params)
    query = urllib.parse.urlencode(common, doseq=True)
    return f"https://www.douyin.com/aweme/v1/web/{endpoint}/?{query}"


def build_bilibili_url(endpoint: str, params: dict[str, Any]) -> str:
    """组合 B 站公开评论接口 URL；认证仍由页面内 fetch 携带。"""
    query = urllib.parse.urlencode(params, doseq=True)
    return f"https://api.bilibili.com/{endpoint}?{query}"


def build_url(endpoint: str, params: dict[str, Any]) -> str:
    """兼容旧调用方的抖音 URL 构造函数。"""
    return build_douyin_url(endpoint, params)


def jittered_sleep(
    seconds: float,
    config: CollectorConfig,
    sleeper=time.sleep,
    random_fn=random.random,
) -> None:
    """按统一抖动规则等待指定秒数；视频级间隔与页面停留复用页级抖动配置。"""
    factor = 1.0 + (random_fn() - 0.5) * config.jitter_range
    sleeper(seconds * factor)


def rate_sleep(config: CollectorConfig, sleeper=time.sleep, random_fn=random.random) -> None:
    """按基础间隔与随机抖动串行等待，避免对站点造成突发负载。"""
    jittered_sleep(config.delay, config, sleeper, random_fn)


def request_with_retry(
    operation,
    config: CollectorConfig,
    description: str,
    sleeper=time.sleep,
) -> dict[str, Any]:
    """
    对评论页请求执行有限次数指数退避。

    最后一次失败后直接抛错而不再休眠，因为此时已经没有下一次尝试；这既能缩短故障
    反馈时间，也避免用户误以为程序仍会继续工作。
    """
    last_error = "未知错误"
    for attempt in range(1, config.max_retry + 1):
        try:
            data = operation()
            if data.get("code") == 0:
                return data
            last_error = str(data.get("error", f"code={data.get('code')}"))
        except (CollectionError, PageNotReady, DaemonUnavailable) as exc:
            last_error = str(exc)

        if attempt == config.max_retry:
            break
        delay = config.retry_base_delay * (2 ** (attempt - 1))
        print(f"[重试] {description} 失败：{last_error}；{delay:.0f}s 后重试 {attempt}/{config.max_retry}")
        sleeper(delay)
    raise CollectionError(f"{description} 重试 {config.max_retry} 次仍失败：{last_error}")


def new_state(
    video_id: str,
    sub_rate: float,
    seed: int,
    with_sub: bool,
    platform: str = PLATFORM_DOUYIN,
) -> dict[str, Any]:
    """创建带平台信息的版本化采集状态。"""
    if platform not in SUPPORTED_PLATFORMS:
        raise ConfigError(f"不支持的平台：{platform}")
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "platform": platform,
        "video_id": video_id,
        "all_count": 0,
        "fetched_count": 0,
        "sub_count": 0,
        "total_count": 0,
        "last_cursor": 1 if platform == PLATFORM_BILIBILI else 0,
        "last_has_more": 1,
        "main_complete": False,
        "main_page_count": 0,
        "sub_rate": sub_rate,
        "seed": seed,
        "with_sub": with_sub,
        "sampled_cids": [],
        "skipped_cids": [],
        "sub_done_cids": [],
        "updated_at": utc_now_iso(),
    }
    if platform == PLATFORM_DOUYIN:
        meta["aweme_id"] = video_id
    else:
        meta["bvid"] = video_id
    return {"meta": meta, "comments": [], "subs": {}}


def _normalize_legacy_bilibili_comment(comment: dict[str, Any]) -> dict[str, Any]:
    """把旧 B 站脚本的 replies 字段转换为当前统一评论字段。"""
    if "cid" in comment:
        return dict(comment)
    return {
        "cid": str(comment.get("rpid", "")),
        "text": str(comment.get("message", "")),
        "create_time": comment.get("ctime", 0),
        "digg_count": comment.get("like", 0),
        "reply_comment_total": comment.get("rcount", 0),
        "nickname": comment.get("uname", ""),
        "uid": str(comment["mid"]) if comment.get("mid") is not None else None,
        "ip_label": None,
        "level": comment.get("level", 0),
        "is_hot": 0,
        "reply_id": str(comment["parent"]) if comment.get("parent") else None,
        "root_comment_id": str(comment["root"]) if comment.get("root") else None,
        "is_author_digged": 0,
    }


def load_state(path: Path) -> dict[str, Any] | None:
    """读取恢复文件，兼容旧抖音 v1 与旧 B 站脚本的基础输出。"""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取恢复文件 {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("meta"), dict):
        raise ConfigError("恢复文件必须包含对象类型的 meta")
    meta = raw["meta"]
    if "comments" not in raw and isinstance(raw.get("replies"), list):
        # 旧 B 站脚本使用 replies，并且在 skill 打包前位于项目根目录。
        raw["comments"] = [
            _normalize_legacy_bilibili_comment(item)
            for item in raw["replies"]
            if isinstance(item, dict)
        ]
        meta.setdefault("platform", PLATFORM_BILIBILI)
        meta.setdefault("video_id", meta.get("bvid"))
        meta.setdefault("bvid", meta.get("video_id"))
        meta.setdefault("last_cursor", int(meta.get("last_page", 1) or 1))
        meta.setdefault("last_has_more", 1)
        meta.setdefault("main_complete", False)
        meta.setdefault("main_page_count", 0)
        meta.setdefault("sub_rate", 1.0)
        meta.setdefault("seed", DEFAULT_SEED)
        meta.setdefault("with_sub", bool(raw.get("subs")))
        meta.setdefault("sampled_cids", [])
        meta.setdefault("skipped_cids", [])
        meta.setdefault("sub_done_cids", [])
    if not isinstance(raw.get("comments", []), list):
        raise ConfigError("恢复文件 comments 必须是数组")
    raw["comments"] = raw.get("comments", [])

    platform = str(meta.get("platform") or (
        PLATFORM_BILIBILI if meta.get("bvid") else PLATFORM_DOUYIN
    ))
    video_id = str(meta.get("video_id") or meta.get("aweme_id") or meta.get("bvid") or "")
    if platform not in SUPPORTED_PLATFORMS or not video_id:
        raise ConfigError("恢复文件缺少有效的 platform/video_id")
    meta["platform"] = platform
    meta["video_id"] = video_id
    if platform == PLATFORM_DOUYIN:
        meta.setdefault("aweme_id", video_id)
    else:
        meta.setdefault("bvid", video_id)
    meta.setdefault("schema_version", "1.0.0")
    meta.setdefault("collector_version", COLLECTOR_VERSION)
    meta.setdefault("last_cursor", 1 if platform == PLATFORM_BILIBILI else 0)
    meta.setdefault("last_has_more", 1)
    meta.setdefault("main_complete", False)
    meta.setdefault("main_page_count", 0)
    meta.setdefault("sub_rate", DEFAULT_SUB_RATE)
    meta.setdefault("seed", DEFAULT_SEED)
    meta.setdefault("with_sub", False)
    meta.setdefault("sampled_cids", [])
    meta.setdefault("skipped_cids", [])
    meta.setdefault("sub_done_cids", [])
    subs = raw.get("subs", [])
    if isinstance(subs, list):
        try:
            raw["subs"] = {
                str(root): [
                    _normalize_legacy_bilibili_comment(item)
                    if isinstance(item, dict)
                    else item
                    for item in items
                ]
                for root, items in subs
            }
        except (TypeError, ValueError) as exc:
            raise ConfigError("恢复文件 subs 格式无效") from exc
    elif isinstance(subs, dict):
        raw["subs"] = {
            str(root): [
                _normalize_legacy_bilibili_comment(item)
                if isinstance(item, dict)
                else item
                for item in items
            ]
            for root, items in subs.items()
        }
    else:
        raise ConfigError("恢复文件 subs 必须是二元数组或对象")
    return raw


def validate_resume_state(
    state: dict[str, Any],
    video_id: str,
    sub_rate: float,
    seed: int,
    platform: str | None = None,
) -> None:
    """验证恢复文件与本次平台、视频和抽样配置兼容。"""
    meta = state["meta"]
    state_platform = str(meta.get("platform") or (
        PLATFORM_BILIBILI if meta.get("bvid") else PLATFORM_DOUYIN
    ))
    expected_platform = platform or state_platform
    if state_platform != expected_platform:
        raise ConfigError(
            f"恢复文件属于平台 {state_platform}，不能用于 {expected_platform}"
        )
    state_video_id = str(meta.get("video_id") or meta.get("aweme_id") or meta.get("bvid"))
    if state_video_id != video_id:
        raise ConfigError(
            f"恢复文件属于视频 {state_video_id}，不能用于视频 {video_id}"
        )
    schema = str(meta.get("schema_version", SCHEMA_VERSION))
    if schema.split(".")[0] not in {"1", SCHEMA_VERSION.split(".")[0]}:
        raise ConfigError(f"恢复文件 schema_version={schema} 与当前版本不兼容")
    decisions_exist = bool(meta.get("sampled_cids") or meta.get("skipped_cids"))
    if decisions_exist:
        old_rate = float(meta.get("sub_rate", sub_rate))
        old_seed = int(meta.get("seed", seed))
        if old_rate != sub_rate or old_seed != seed:
            raise ConfigError(
                "恢复文件已有二级评论抽样决策，--sub-rate/--seed 必须与原任务一致"
            )


def serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    """把内部状态转换为稳定、可读且向后兼容的磁盘结构。"""
    meta = dict(state["meta"])
    platform = str(meta.get("platform", PLATFORM_DOUYIN))
    video_id = str(meta.get("video_id") or meta.get("aweme_id") or meta.get("bvid") or "")
    if platform not in SUPPORTED_PLATFORMS or not video_id:
        raise ConfigError("采集状态缺少有效的 platform/video_id")
    comments = list(state.get("comments", []))
    subs_map: dict[str, list[dict[str, Any]]] = state.get("subs", {})
    sub_total = sum(len(items) for items in subs_map.values())
    meta.update(
        {
            "schema_version": SCHEMA_VERSION,
            "collector_version": COLLECTOR_VERSION,
            "platform": platform,
            "video_id": video_id,
            "fetched_count": len(comments),
            "sub_count": sub_total,
            "total_count": len(comments) + sub_total,
            "sampled_cids": sorted(set(map(str, meta.get("sampled_cids", [])))),
            "skipped_cids": sorted(set(map(str, meta.get("skipped_cids", [])))),
            "sub_done_cids": sorted(set(map(str, meta.get("sub_done_cids", [])))),
            "updated_at": utc_now_iso(),
        }
    )
    if platform == PLATFORM_DOUYIN:
        meta.setdefault("aweme_id", video_id)
    else:
        meta.setdefault("bvid", video_id)
    return {
        "meta": meta,
        "comments": comments,
        "subs": [[root, subs_map[root]] for root in sorted(subs_map)],
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    """使用同目录临时文件与 ``os.replace`` 原子保存进度。"""
    validate_output_path(path)
    document = serialize_state(state)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise CollectionError(f"保存进度失败: {exc}") from exc


def new_space_manifest(platform: str, user_id: str, space_url: str) -> dict[str, Any]:
    """创建博主主页批量任务的视频清单（队列层断点）。"""
    return {
        "schema": SPACE_MANIFEST_SCHEMA,
        "platform": platform,
        "user_id": user_id,
        "space_url": space_url,
        "updated_at": utc_now_iso(),
        "videos": [],
    }


def load_space_manifest(path: Path) -> dict[str, Any] | None:
    """读取视频清单；文件不存在时返回 None，格式错误时 fail-fast。"""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取视频清单 {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("videos"), list):
        raise ConfigError(f"视频清单 {path} 格式无效：缺少 videos 数组")
    return raw


def merge_space_videos(
    manifest: dict[str, Any],
    items: list[dict[str, str]],
    max_videos: int,
) -> dict[str, Any]:
    """
    把主页最新视频清单合并进任务队列。

    主页顺序（新→旧）是队列顺序的唯一事实来源；已存在条目的
    status/comments/subs/error 全部保留，使重跑同一命令可以跳过已完成视频。
    超出 ``max_videos`` 的旧条目被移出队列（其已采集的 JSON 文件不受影响）。
    """
    existing: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("videos", []):
        if isinstance(entry, dict) and entry.get("video_id"):
            existing[str(entry["video_id"])] = entry

    merged: list[dict[str, Any]] = []
    for item in items[:max_videos]:
        video_id = str(item["video_id"])
        previous = existing.get(video_id)
        if previous:
            entry = dict(previous)
            if item.get("title") and not entry.get("title"):
                entry["title"] = str(item["title"])
            entry.setdefault("status", "pending")
            entry.setdefault("comments", 0)
            entry.setdefault("subs", 0)
            entry.setdefault("error", None)
        else:
            entry = {
                "video_id": video_id,
                "title": str(item.get("title") or ""),
                "status": "pending",
                "comments": 0,
                "subs": 0,
                "error": None,
            }
        merged.append(entry)
    manifest["videos"] = merged
    return manifest


def save_space_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """原子保存视频清单，保证进程中断时不会留下写了一半的队列。"""
    manifest["updated_at"] = utc_now_iso()
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        raise CollectionError(f"保存视频清单失败: {exc}") from exc


class CommentsCollector:
    """双平台一级评论与按线程抽样的二级评论采集流程。"""

    def __init__(
        self,
        bridge: BridgeLike,
        config: CollectorConfig,
        output_path: Path,
        state: dict[str, Any],
    ):
        self.bridge = bridge
        self.config = config
        self.output_path = output_path
        self.state = state

    @property
    def video_id(self) -> str:
        """返回当前状态绑定的平台视频 ID。"""
        meta = self.state["meta"]
        return str(meta.get("video_id") or meta.get("aweme_id") or meta.get("bvid"))

    @property
    def aweme_id(self) -> str:
        """兼容旧测试与调用方的抖音 ID 属性。"""
        return self.video_id

    def _main_params(self, cursor: int) -> dict[str, Any]:
        if self.state["meta"].get("platform") == PLATFORM_BILIBILI:
            return {
                "next": cursor,
                "ps": self.config.page_size,
                "mode": 3,
            }
        return {
            "aweme_id": self.aweme_id,
            "cursor": cursor,
            "count": self.config.page_size,
            "item_type": 0,
            "insert_ids": "",
            "rcFT": "",
        }

    def _sub_params(self, root_cid: str, cursor: int) -> dict[str, Any]:
        if self.state["meta"].get("platform") == PLATFORM_BILIBILI:
            return {
                "root": root_cid,
                "pn": cursor,
                "ps": self.config.page_size,
            }
        return {
            "item_id": self.aweme_id,
            "comment_id": root_cid,
            "cursor": cursor,
            "count": self.config.page_size,
            "item_type": 0,
            "insert_ids": "",
            "rcFT": "",
        }

    def fetch_main(self, max_pages: int | None = None) -> None:
        """按 cursor 采集一级评论；每页完成后立即保存可恢复状态。"""
        meta = self.state["meta"]
        if meta.get("main_complete"):
            print(f"[复用] 一级评论已完成，共 {len(self.state['comments'])} 条")
            return

        default_cursor = self._cursor_start(sub=False)
        cursor = int(meta.get("last_cursor", default_cursor))
        has_more = int(meta.get("last_has_more", 1))
        pages_this_run = 0
        seen = {str(comment.get("cid")) for comment in self.state["comments"]}
        print(f"[一级评论] 从 cursor={cursor} 继续")

        while has_more:
            if max_pages is not None and pages_this_run >= max_pages:
                print(f"[限量停止] 本次已采集 {pages_this_run} 页，进度可续跑")
                break
            if pages_this_run and pages_this_run % 5 == 0:
                self.bridge.wait_captcha_clear()

            data = request_with_retry(
                lambda: self.bridge.fetch_json("comment/list", self._main_params(cursor)),
                self.config,
                f"一级评论 cursor={cursor}",
            )
            for comment in data.get("comments", []):
                cid = str(comment.get("cid"))
                if cid and cid not in seen:
                    self.state["comments"].append(comment)
                    seen.add(cid)

            cursor = int(data.get("cursor", cursor))
            has_more = int(bool(data.get("has_more", 0)))
            pages_this_run += 1
            meta["last_cursor"] = cursor
            meta["last_has_more"] = has_more
            meta["all_count"] = int(data.get("total", meta.get("all_count", 0)))
            meta["main_page_count"] = int(meta.get("main_page_count", 0)) + 1
            meta["main_complete"] = not bool(has_more)
            save_state(self.output_path, self.state)
            print(
                f"  [一级进度] 本次第 {pages_this_run} 页，累计 {len(self.state['comments'])} 条，"
                f"接口总数 {meta['all_count']}"
            )
            if has_more:
                rate_sleep(self.config)

    def _sample_root(self, cid: str) -> bool:
        """对有回复的一级评论做可恢复、可复现的线程级抽样。"""
        meta = self.state["meta"]
        sampled = set(map(str, meta.get("sampled_cids", [])))
        skipped = set(map(str, meta.get("skipped_cids", [])))
        if cid in sampled:
            return True
        if cid in skipped:
            return False
        rng = random.Random(int(meta["seed"]) + int(cid))
        if rng.random() < float(meta["sub_rate"]):
            sampled.add(cid)
            meta["sampled_cids"] = sorted(sampled)
            return True
        skipped.add(cid)
        meta["skipped_cids"] = sorted(skipped)
        return False

    def fetch_subs(self) -> None:
        """采集抽样命中的二级回复线程，并在每个 root 完成后保存。"""
        meta = self.state["meta"]
        subs_map: dict[str, list[dict[str, Any]]] = self.state["subs"]
        done = set(map(str, meta.get("sub_done_cids", [])))
        roots: list[tuple[str, int]] = []

        for comment in self.state["comments"]:
            cid = str(comment.get("cid"))
            reply_total = int(comment.get("reply_comment_total", 0) or 0)
            if reply_total <= 0 or cid in done or cid in subs_map:
                continue
            if self._sample_root(cid):
                roots.append((cid, reply_total))

        # 抽样决策先落盘，确保中断续跑不会重新随机。
        save_state(self.output_path, self.state)
        print(
            f"[二级评论] 有回复线程中抽样命中 {len(roots)} 条，"
            f"比例={float(meta['sub_rate']):.0%}"
        )

        for index, (root_cid, reported_total) in enumerate(roots, 1):
            if index > 1:
                rate_sleep(self.config)
            if index % 10 == 0:
                self.bridge.wait_captcha_clear()

            cursor = self._cursor_start(sub=True)
            has_more = 1
            root_items: list[dict[str, Any]] = []
            seen: set[str] = set()
            while has_more:
                data = request_with_retry(
                    lambda: self.bridge.fetch_json(
                        "comment/list/reply", self._sub_params(root_cid, cursor)
                    ),
                    self.config,
                    f"二级评论 root={root_cid}, cursor={cursor}",
                )
                for item in data.get("comments", []):
                    cid = str(item.get("cid"))
                    if cid and cid not in seen:
                        root_items.append(item)
                        seen.add(cid)
                cursor = int(data.get("cursor", cursor))
                has_more = int(bool(data.get("has_more", 0)))
                if has_more:
                    rate_sleep(self.config)

            subs_map[root_cid] = root_items
            done.add(root_cid)
            meta["sub_done_cids"] = sorted(done)
            save_state(self.output_path, self.state)
            print(
                f"  [二级进度] {index}/{len(roots)}，root={root_cid}，"
                f"采集 {len(root_items)}/{reported_total} 条"
            )

    def _cursor_start(self, sub: bool) -> int:
        """读取平台分页起点；旧的离线测试替身没有该方法时沿用抖音起点。"""
        method_name = "sub_cursor_start" if sub else "main_cursor_start"
        method = getattr(self.bridge, method_name, None)
        return int(method()) if callable(method) else 0


def export_csv(path: Path, state: dict[str, Any]) -> None:
    """把双平台一级与二级评论平铺为 UTF-8 BOM CSV，便于 Excel 直接打开。"""
    validate_output_path(path)
    meta = state["meta"]
    fieldnames = [
        "platform",
        "video_id",
        "cid",
        "root_cid",
        "is_sub",
        "text",
        "digg_count",
        "create_time",
        "reply_comment_total",
        "nickname",
        "uid",
        "ip_label",
        "level",
        "is_hot",
        "reply_id",
        "root_comment_id",
        "is_author_digged",
    ]
    rows: list[dict[str, Any]] = []
    for comment in state.get("comments", []):
        rows.append(
            {
                "platform": meta.get("platform", ""),
                "video_id": meta.get("video_id", ""),
                "root_cid": "0",
                "is_sub": 0,
                **comment,
            }
        )
    for root_cid, items in state.get("subs", {}).items():
        for item in items:
            rows.append(
                {
                    "platform": meta.get("platform", ""),
                    "video_id": meta.get("video_id", ""),
                    "root_cid": root_cid,
                    "is_sub": 1,
                    **item,
                }
            )

    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})
    except OSError as exc:
        raise CollectionError(f"写入 CSV 失败: {exc}") from exc
    print(f"[CSV] {path}，共 {len(rows)} 行")


def run_space_batch(args: argparse.Namespace, config: CollectorConfig, bridge: WebBridgeClient) -> int:
    """
    博主主页批量模式：按主页顺序（新→旧）串行采集每个视频的评论。

    队列层断点写在 ``<output-dir>/video_list.json``（每个视频的 done/failed 状态），
    视频层断点沿用单视频 v2 状态文件；两层叠加使“第 N 个视频第 M 页中断”可以
    原样续跑。单个视频失败只记录并跳过；连续失败达到上限时判定为风控或登录
    失效，中止整个任务等待人工检查。
    """
    platform = config.platform
    user_id, space_url = extract_space_target(args.space, platform)
    output_dir = Path(
        args.output_dir or f"space-comments-{user_id}"
    ).expanduser().resolve()
    if output_dir.exists() and not output_dir.is_dir():
        raise ConfigError(f"--output-dir 指向的不是目录: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / SPACE_MANIFEST_NAME

    source_note = f"，配置来源 {args.config_source}" if args.config_source else "，未使用配置文件"
    print(
        f"[运行参数] 基础间隔={config.delay:.1f}s，视频间隔={config.video_delay:.1f}s，"
        f"页面停留={config.dwell:.1f}s，抖动幅度={config.jitter_range:.2f}"
        f"（±{config.jitter_range / 2:.0%}），抽样率={args.sub_rate:.2f}，"
        f"种子={args.seed}{source_note}",
        flush=True,
    )

    print(f"[主页] 打开 {space_url}", flush=True)
    bridge.prepare_space_page(space_url)
    bridge.wait_captcha_clear()

    items = bridge.collect_video_list(args.max_videos)
    if not items:
        raise CollectionError(
            "未在主页发现任何视频；请确认主页可公开访问、登录状态正常，且页面没有未处理的验证"
        )

    manifest = load_space_manifest(manifest_path)
    if manifest is None:
        manifest = new_space_manifest(platform, user_id, space_url)
    elif (
        str(manifest.get("platform")) != platform
        or str(manifest.get("user_id")) != user_id
    ):
        raise ConfigError(
            f"{manifest_path} 属于其他平台或博主，不能混用；请更换 --output-dir"
        )
    merge_space_videos(manifest, items, args.max_videos)
    save_space_manifest(manifest_path, manifest)

    videos = manifest["videos"]
    total = len(videos)
    pending_total = sum(1 for v in videos if v.get("status") != "done")
    print(
        f"[批量] 主页视频 {total} 个（按发布时间新→旧），待采集 {pending_total} 个",
        flush=True,
    )

    consecutive_failures = 0
    for index, entry in enumerate(videos, 1):
        if entry.get("status") == "done":
            print(f"[批量] {index}/{total} {entry['video_id']} 已完成，跳过")
            continue
        video_id = str(entry["video_id"])
        print(f"[批量] {index}/{total} 视频 {video_id}（{entry.get('title') or '无标题'}）", flush=True)
        try:
            bridge.prepare_page(video_id)
            browse = bridge.simulate_browse()
            if config.dwell > 0:
                if browse.get("has_video"):
                    print(f"  [浏览模拟] 已触发静音播放，停留 {config.dwell:.0f}s 模拟观看")
                jittered_sleep(config.dwell, config)
            bridge.wait_captcha_clear()

            output_path = output_dir / f"{video_id}.json"
            state = load_state(output_path)
            if state is None:
                state = new_state(
                    video_id, args.sub_rate, args.seed, args.with_sub, platform=platform
                )
            else:
                validate_resume_state(
                    state, video_id, args.sub_rate, args.seed, platform=platform
                )
                state["meta"]["with_sub"] = bool(
                    state["meta"].get("with_sub") or args.with_sub
                )

            collector = CommentsCollector(bridge, config, output_path, state)
            collector.fetch_main(max_pages=args.max_pages)
            if args.with_sub:
                collector.fetch_subs()
            save_state(output_path, state)

            summary = serialize_state(state)["meta"]
            entry.update(
                {
                    "status": "done",
                    "comments": summary["fetched_count"],
                    "subs": summary["sub_count"],
                    "error": None,
                }
            )
            consecutive_failures = 0
            print(
                f"  [批量进度] {video_id} 完成：一级 {summary['fetched_count']} 条 "
                f"+ 二级 {summary['sub_count']} 条"
            )
        except (CollectionError, PageNotReady) as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            consecutive_failures += 1
            print(f"  [批量警告] {video_id} 采集失败：{exc}；跳过并继续下一个视频")
            save_space_manifest(manifest_path, manifest)
            if consecutive_failures >= MAX_CONSECUTIVE_VIDEO_FAILURES:
                raise CollectionError(
                    f"连续 {MAX_CONSECUTIVE_VIDEO_FAILURES} 个视频采集失败，"
                    "可能触发风控或登录失效；进度已保存，"
                    "请检查浏览器会话后重跑同一命令续采"
                ) from exc
        save_space_manifest(manifest_path, manifest)

        # 视频之间按 video_delay 等待；后面没有待采视频时不再等待。
        remaining = any(v.get("status") != "done" for v in videos[index:])
        if remaining:
            jittered_sleep(config.video_delay, config)

    done = sum(1 for v in videos if v.get("status") == "done")
    failed = sum(1 for v in videos if v.get("status") == "failed")
    print(f"[批量完成] 成功 {done}/{total}，失败 {failed}；清单 {manifest_path}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="通过 Kimi WebBridge 采集抖音或 B 站视频评论（串行、可恢复、人工验证感知）"
    )
    parser.add_argument("video", nargs="?", help="抖音/B 站视频 URL、aweme_id、BV 号或 av 号")
    parser.add_argument(
        "--space",
        help="博主主页批量模式：B 站 space.bilibili.com/<mid> 或抖音 user 主页链接；"
        "按发布时间新→旧串行采集每个视频的评论",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        help="批量模式采集的最近视频个数；--space 必填，需先与用户确认数量",
    )
    parser.add_argument(
        "--output-dir",
        help="批量模式输出目录；默认 space-comments-<博主ID>；逐视频 JSON 与 video_list.json 写入该目录",
    )
    parser.add_argument(
        "--platform",
        choices=[PLATFORM_AUTO, PLATFORM_DOUYIN, PLATFORM_BILIBILI],
        default=PLATFORM_AUTO,
        help="目标平台；默认根据 URL/ID 自动识别",
    )
    parser.add_argument("--output", help="JSON 输出路径；默认按视频 ID 命名")
    parser.add_argument("--csv", help="可选 CSV 输出路径")
    parser.add_argument("--with-sub", action="store_true", help="采集抽样命中的二级回复线程")
    parser.add_argument("--subs-only", action="store_true", help="复用已有一级评论，只补采二级回复")
    parser.add_argument(
        "--sub-rate",
        type=float,
        default=None,
        help="二级线程抽样比例 0–1；缺省读 config.json，内置默认 0.5",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="确定性抽样种子；缺省读 config.json，内置默认 42",
    )
    parser.add_argument("--max-pages", type=int, help="本次最多新增采集的一级评论页数")
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="请求基础间隔秒数，最低 3.0；缺省读 config.json，内置默认 5.0",
    )
    parser.add_argument(
        "--jitter-range",
        type=float,
        default=None,
        help="间隔随机抖动总幅度，0.6 表示 ±30%%；缺省读 config.json，内置默认 0.6",
    )
    parser.add_argument(
        "--video-delay",
        type=float,
        default=None,
        help="批量模式相邻视频的基础间隔秒数，最低 5.0；缺省读 config.json，内置默认 20.0",
    )
    parser.add_argument(
        "--dwell",
        type=float,
        default=None,
        help="批量模式每个视频页面的停留浏览秒数，0 表示不停留；缺省读 config.json，内置默认 15.0",
    )
    parser.add_argument(
        "--config",
        help="自定义 config.json 路径；缺省自动读取技能目录下的 config.json（存在时）",
    )
    parser.add_argument(
        "--daemon-url",
        default=os.getenv("COMMENTS_CATCHER_DAEMON_URL", DEFAULT_DAEMON_URL),
        help="WebBridge daemon URL，也可用 COMMENTS_CATCHER_DAEMON_URL",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="WebBridge 会话名；省略时按平台使用默认会话，也可用 COMMENTS_CATCHER_SESSION",
    )
    parser.add_argument("--health-check", action="store_true", help="仅检查 daemon、页面、SDK 与验证状态")
    parser.add_argument(
        "--prepare-page",
        action="store_true",
        help="打开目标视频并等待页面就绪后退出；普通采集默认也会自动执行此步骤",
    )
    parser.add_argument(
        "--reuse-current-page",
        action="store_true",
        help="复用当前 WebBridge 会话页面，不自动导航；仅用于已确认页面正确的场景",
    )
    parser.add_argument(
        "--include-user-identifiers",
        action="store_true",
        help="显式保存稳定 UID 与 IP 属地；默认留空以减少个人数据",
    )
    parser.add_argument("--version", action="version", version=f"comments-catcher {COLLECTOR_VERSION}")
    return parser


def parse_and_validate_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, CollectorConfig]:
    """解析参数并按“命令行 > config.json > 内置默认”合成配置；全部验证不依赖网络。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_pages is not None and args.max_pages <= 0:
        raise ConfigError("--max-pages 必须大于 0")
    if args.max_videos is not None and args.max_videos <= 0:
        raise ConfigError("--max-videos 必须大于 0")
    if args.subs_only:
        args.with_sub = True
    if args.prepare_page and args.reuse_current_page:
        raise ConfigError("--prepare-page 不能与 --reuse-current-page 同时使用")

    if args.space:
        # 批量模式与单视频模式的参数集合互斥，防止输出路径语义混乱：
        # 批量输出一律落在 --output-dir 下按视频 ID 命名。
        if args.video:
            raise ConfigError("--space 批量模式不能同时提供单个视频参数")
        for used, name in (
            (args.output, "--output"),
            (args.csv, "--csv"),
            (args.prepare_page, "--prepare-page"),
            (args.reuse_current_page, "--reuse-current-page"),
            (args.subs_only, "--subs-only"),
        ):
            if used:
                raise ConfigError(f"{name} 仅用于单视频模式，不能与 --space 同用")
        if args.max_videos is None:
            # 视频数量必须由用户拍板，实现不提供默认值，避免替用户决定抓取规模。
            raise ConfigError(
                "--space 批量模式必须显式指定 --max-videos（请先与用户确认要采集的最近视频个数）"
            )
    else:
        if args.max_videos is not None:
            raise ConfigError("--max-videos 只能与 --space 一起使用")
        if args.output_dir:
            raise ConfigError("--output-dir 只能与 --space 一起使用")
    if not args.health_check and not args.video and not args.space:
        raise ConfigError("采集或准备页面时必须提供视频 URL/ID 或 --space 主页")

    args.platform = detect_platform(args.space or args.video, args.platform)
    session = args.session or os.getenv("COMMENTS_CATCHER_SESSION") or DEFAULT_SESSIONS[args.platform]
    args.session = session

    # 先合成节奏与抽样参数再验证，保证配置文件里的非法值与命令行非法值一样
    # 在发起任何网络请求之前被拒绝。平台已确定，按“平台小节覆盖顶层通用”合并配置。
    config_path = resolve_config_path(args.config)
    file_values = (
        merge_platform_config(load_config_file(config_path), args.platform)
        if config_path
        else {}
    )
    args.config_source = str(config_path) if config_path else None
    args.sub_rate = float(_resolve_option(args.sub_rate, file_values, "sub_rate", DEFAULT_SUB_RATE))
    args.seed = int(_resolve_option(args.seed, file_values, "seed", DEFAULT_SEED))
    args.delay = float(_resolve_option(args.delay, file_values, "delay", DEFAULT_DELAY))
    args.jitter_range = float(
        _resolve_option(args.jitter_range, file_values, "jitter_range", DEFAULT_JITTER_RANGE)
    )
    args.video_delay = float(
        _resolve_option(args.video_delay, file_values, "video_delay", DEFAULT_VIDEO_DELAY)
    )
    args.dwell = float(_resolve_option(args.dwell, file_values, "dwell", DEFAULT_DWELL))
    validate_sub_rate(args.sub_rate)

    config = CollectorConfig(
        platform=args.platform,
        daemon_url=args.daemon_url,
        session=session,
        delay=args.delay,
        jitter_range=args.jitter_range,
        video_delay=args.video_delay,
        dwell=args.dwell,
        include_user_identifiers=args.include_user_identifiers,
    )
    config.validate()
    return args, config


def run(argv: list[str] | None = None) -> int:
    """命令行主流程，返回稳定退出码供 Skill/Agent 判断下一步。"""
    args, config = parse_and_validate_args(argv)
    bridge = WebBridgeClient(config)

    if args.health_check:
        result, code = bridge.health_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code

    if args.space:
        return run_space_batch(args, config, bridge)

    platform = config.platform
    video_id = extract_video_id(args.video, platform)
    if args.reuse_current_page:
        status = bridge.check_page_ready(video_id)
    else:
        # 正常采集由 Agent 自己导航到目标页；用户无需预先打开正确的站点页面。
        status = bridge.prepare_page(video_id)
    if args.prepare_page:
        print(
            json.dumps(
                {
                    "ok": True,
                    "platform": platform,
                    "video_id": video_id,
                    "host": status.get("host"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_OK

    bridge.wait_captcha_clear()

    output_path = Path(args.output or f"{platform}-comments-{video_id}.json").expanduser().resolve()
    validate_output_path(output_path)
    state = load_state(output_path)
    if state is None:
        state = new_state(video_id, args.sub_rate, args.seed, args.with_sub, platform=platform)
    else:
        validate_resume_state(state, video_id, args.sub_rate, args.seed, platform=platform)
        state["meta"]["with_sub"] = bool(state["meta"].get("with_sub") or args.with_sub)
        state["meta"].setdefault("sampled_cids", [])
        state["meta"].setdefault("skipped_cids", [])
        state["meta"].setdefault("sub_done_cids", [])
        state.setdefault("subs", {})

    state["meta"]["platform"] = platform
    state["meta"]["video_id"] = video_id
    if platform == PLATFORM_DOUYIN:
        state["meta"]["aweme_id"] = video_id
    elif bridge.oid is not None:
        state["meta"]["bvid"] = video_id
        state["meta"]["oid"] = bridge.oid

    if args.subs_only and not state.get("comments"):
        raise ConfigError("--subs-only 需要已有输出文件包含一级评论")

    # 采集开始前打印最终生效的节奏与抽样参数，便于确认命令行/配置文件的合成结果。
    source_note = f"，配置来源 {args.config_source}" if args.config_source else "，未使用配置文件"
    print(
        f"[运行参数] 基础间隔={config.delay:.1f}s，抖动幅度={config.jitter_range:.2f}"
        f"（±{config.jitter_range / 2:.0%}），抽样率={args.sub_rate:.2f}，"
        f"种子={args.seed}{source_note}",
        flush=True,
    )

    collector = CommentsCollector(bridge, config, output_path, state)
    if not args.subs_only:
        collector.fetch_main(max_pages=args.max_pages)
    if args.with_sub:
        collector.fetch_subs()
    save_state(output_path, state)

    if args.csv:
        export_csv(Path(args.csv).expanduser().resolve(), state)

    summary = serialize_state(state)["meta"]
    print(
        f"[完成] 一级 {summary['fetched_count']} 条 + 二级 {summary['sub_count']} 条 "
        f"= {summary['total_count']} 条 -> {output_path}"
    )
    return EXIT_OK


def main() -> None:
    """进程入口：统一中文输出编码、异常文案与退出码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    try:
        code = run()
    except ConfigError as exc:
        print(f"[参数错误] {exc}", file=sys.stderr)
        code = EXIT_ARGUMENT
    except DaemonUnavailable as exc:
        print(f"[WebBridge 不可用] {exc}", file=sys.stderr)
        code = EXIT_DAEMON
    except PageNotReady as exc:
        print(f"[页面未就绪] {exc}", file=sys.stderr)
        code = EXIT_PAGE
    except ManualActionRequired as exc:
        print(f"[需要人工处理] {exc}", file=sys.stderr)
        code = EXIT_MANUAL_ACTION
    except CollectionError as exc:
        print(f"[采集失败] {exc}", file=sys.stderr)
        code = EXIT_COLLECTION
    raise SystemExit(code)


if __name__ == "__main__":
    main()
