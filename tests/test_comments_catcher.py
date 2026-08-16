# -*- coding: utf-8 -*-
"""Comments Catcher 离线单元测试：只使用合成数据，不访问浏览器或平台页面。"""

from __future__ import annotations

import codecs
import csv
import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "skills" / "comments-catcher" / "scripts" / "comments_catcher.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("comments_catcher", SCRIPT_PATH)
assert spec and spec.loader
cc = importlib.util.module_from_spec(spec)
# dataclass 在装饰时会读取 sys.modules，因此执行前必须先注册动态模块。
import sys
sys.modules[spec.name] = cc
spec.loader.exec_module(cc)


def fixture(name: str) -> dict:
    """读取一份明确标记为 synthetic 的测试响应。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeBridge:
    """采集器测试替身：按 endpoint/cursor 返回合成页面，不产生网络请求。"""

    def __init__(self):
        self.main_pages = {
            0: fixture("main-page-1.synthetic.json"),
            20: fixture("main-page-2.synthetic.json"),
        }
        self.reply_pages = {
            ("7000000000000000001", 0): fixture("reply-page.synthetic.json")
        }
        self.calls: list[tuple[str, dict]] = []
        self.captcha_checks = 0

    def fetch_json(self, endpoint: str, params: dict) -> dict:
        """模拟 WebBridge 返回；缺失游标会立即让测试失败。"""
        self.calls.append((endpoint, dict(params)))
        if endpoint == "comment/list":
            return deepcopy(self.main_pages[int(params["cursor"])])
        key = (str(params["comment_id"]), int(params["cursor"]))
        return deepcopy(self.reply_pages[key])

    def wait_captcha_clear(self) -> None:
        """记录检测次数；离线测试默认页面清晰可用。"""
        self.captcha_checks += 1


class FakeBilibiliBridge:
    """B 站分页替身，覆盖 next/pn 从 1 开始且响应结构已被适配的场景。"""

    def __init__(self):
        self.main_pages = {
            1: {
                "code": 0,
                "comments": [
                    {
                        "cid": "8000000000000000001",
                        "text": "B 站一级评论",
                        "reply_comment_total": 1,
                    },
                    {
                        "cid": "8000000000000000002",
                        "text": "第二条",
                        "reply_comment_total": 0,
                    },
                ],
                "cursor": 2,
                "has_more": 1,
                "total": 3,
            },
            2: {
                "code": 0,
                "comments": [
                    {
                        "cid": "8000000000000000002",
                        "text": "重复评论",
                        "reply_comment_total": 0,
                    },
                    {
                        "cid": "8000000000000000003",
                        "text": "最后一条",
                        "reply_comment_total": 0,
                    },
                ],
                "cursor": 2,
                "has_more": 0,
                "total": 3,
            },
        }
        self.reply_pages = {
            ("8000000000000000001", 1): {
                "code": 0,
                "comments": [
                    {
                        "cid": "8000000000000000011",
                        "text": "B 站楼中楼",
                        "reply_comment_total": 0,
                    }
                ],
                "cursor": 2,
                "has_more": 0,
                "total": 1,
            }
        }
        self.calls: list[tuple[str, dict]] = []

    def main_cursor_start(self) -> int:
        return 1

    def sub_cursor_start(self) -> int:
        return 1

    def fetch_json(self, endpoint: str, params: dict) -> dict:
        self.calls.append((endpoint, dict(params)))
        if endpoint == "comment/list":
            return deepcopy(self.main_pages[int(params["next"])])
        return deepcopy(self.reply_pages[(str(params["root"]), int(params["pn"]))])

    def wait_captcha_clear(self) -> None:
        pass


class IdentifierAndConfigTests(unittest.TestCase):
    """输入解析与本地安全边界测试。"""

    def test_modal_id_has_priority_over_vid(self):
        url = (
            "https://www.douyin.com/user/example?"
            "modal_id=7674193846108943571&vid=7674214863132396011"
        )
        self.assertEqual(cc.extract_aweme_id(url), "7674193846108943571")

    def test_video_path_and_numeric_id(self):
        self.assertEqual(
            cc.extract_aweme_id("https://www.douyin.com/video/7674214863132396011"),
            "7674214863132396011",
        )
        self.assertEqual(cc.extract_aweme_id("7674214863132396011"), "7674214863132396011")

    def test_bilibili_ids_and_platform_detection(self):
        bvid_url = "https://www.bilibili.com/video/BV1qnuq6dEga/?spm_id_from=333"
        self.assertEqual(cc.detect_platform(bvid_url), cc.PLATFORM_BILIBILI)
        self.assertEqual(cc.extract_video_id(bvid_url, cc.PLATFORM_BILIBILI), "BV1qnuq6dEga")
        self.assertEqual(cc.extract_video_id("av123456", cc.PLATFORM_BILIBILI), "av123456")
        self.assertEqual(cc.extract_video_id("123456", cc.PLATFORM_BILIBILI), "av123456")
        self.assertEqual(cc.detect_platform("7674214863132396011"), cc.PLATFORM_DOUYIN)

    def test_bilibili_url_builder(self):
        url = cc.build_bilibili_url(
            "x/v2/reply/main", {"type": 1, "oid": 123, "next": 1, "mode": 3}
        )
        self.assertTrue(url.startswith("https://api.bilibili.com/x/v2/reply/main?"))
        self.assertIn("oid=123", url)
        self.assertIn("next=1", url)

    def test_invalid_id_raises(self):
        with self.assertRaises(cc.ConfigError):
            cc.extract_aweme_id("https://www.douyin.com/")

    def test_remote_daemon_is_rejected(self):
        config = cc.CollectorConfig(daemon_url="http://192.0.2.10:10086/command")
        with self.assertRaises(cc.ConfigError):
            config.validate()

    def test_delay_and_sub_rate_validation(self):
        with self.assertRaises(cc.ConfigError):
            cc.CollectorConfig(delay=2.99).validate()
        with self.assertRaises(cc.ConfigError):
            cc.validate_sub_rate(float("nan"))
        with self.assertRaises(cc.ConfigError):
            cc.validate_sub_rate(1.01)
        self.assertEqual(cc.validate_sub_rate(0.5), 0.5)

    def test_build_url_encodes_values(self):
        url = cc.build_url("comment/list", {"aweme_id": "123", "insert_ids": "a b"})
        self.assertIn("aweme_id=123", url)
        self.assertIn("insert_ids=a+b", url)


class ConfigFileTests(unittest.TestCase):
    """config.json 本地配置加载、校验与参数优先级测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "config.json"

    def _write(self, payload: dict) -> Path:
        self.config_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return self.config_path

    def test_load_config_file_ignores_comment_keys(self):
        path = self._write(
            {"_说明": "注释", "delay": 6, "jitter_range": 0.4, "sub_rate": 0.3, "seed": 7}
        )
        values = cc.load_config_file(path)
        self.assertEqual(
            values, {"delay": 6, "jitter_range": 0.4, "sub_rate": 0.3, "seed": 7}
        )

    def test_unknown_key_and_wrong_types_rejected(self):
        with self.assertRaises(cc.ConfigError):
            cc.load_config_file(self._write({"dealy": 5}))
        with self.assertRaises(cc.ConfigError):
            cc.load_config_file(self._write({"delay": "fast"}))
        with self.assertRaises(cc.ConfigError):
            cc.load_config_file(self._write({"seed": True}))
        with self.assertRaises(cc.ConfigError):
            cc.load_config_file(self._write({"seed": 4.5}))

    def test_resolve_config_path_rules(self):
        # 仓库自带的技能 config.json 存在时应作为默认配置被识别。
        if cc.DEFAULT_CONFIG_PATH.is_file():
            self.assertEqual(cc.resolve_config_path(None), cc.DEFAULT_CONFIG_PATH)
        explicit = self._write({"delay": 8.0})
        self.assertEqual(cc.resolve_config_path(str(explicit)), explicit.resolve())
        with self.assertRaises(cc.ConfigError):
            cc.resolve_config_path(str(Path(self.temp.name) / "missing.json"))

    def test_config_file_values_applied_and_cli_overrides(self):
        self._write({"delay": 9.0, "jitter_range": 0.2, "sub_rate": 0.25, "seed": 99})
        args, config = cc.parse_and_validate_args(
            ["--health-check", "--config", str(self.config_path)]
        )
        self.assertEqual(config.delay, 9.0)
        self.assertEqual(config.jitter_range, 0.2)
        self.assertEqual(args.sub_rate, 0.25)
        self.assertEqual(args.seed, 99)
        self.assertEqual(args.config_source, str(self.config_path.resolve()))

        args, config = cc.parse_and_validate_args(
            [
                "--health-check",
                "--config",
                str(self.config_path),
                "--delay",
                "4.5",
                "--sub-rate",
                "0.8",
            ]
        )
        self.assertEqual(config.delay, 4.5)
        # 未被命令行覆盖的键仍然读取配置文件。
        self.assertEqual(config.jitter_range, 0.2)
        self.assertEqual(args.sub_rate, 0.8)
        self.assertEqual(args.seed, 99)

    def test_invalid_config_values_rejected_before_network(self):
        for bad in ({"delay": 1.0}, {"jitter_range": 5.0}, {"sub_rate": 1.5}):
            self._write(bad)
            with self.assertRaises(cc.ConfigError):
                cc.parse_and_validate_args(
                    ["--health-check", "--config", str(self.config_path)]
                )

    def test_platform_sections_override_top_level(self):
        self._write(
            {
                "delay": 5.0,
                "sub_rate": 0.5,
                "douyin": {"delay": 3.0},
                "bilibili": {"delay": 8.0, "seed": 7},
            }
        )
        # 抖音：delay 读平台小节，sub_rate 回落顶层通用值。
        args, config = cc.parse_and_validate_args(
            ["--health-check", "--platform", "douyin", "--config", str(self.config_path)]
        )
        self.assertEqual(config.delay, 3.0)
        self.assertEqual(args.sub_rate, 0.5)

        # B 站：delay 与 seed 都读平台小节。
        args, config = cc.parse_and_validate_args(
            ["--health-check", "--platform", "bilibili", "--config", str(self.config_path)]
        )
        self.assertEqual(config.delay, 8.0)
        self.assertEqual(args.seed, 7)

        # 命令行参数仍然优先于平台小节。
        args, config = cc.parse_and_validate_args(
            [
                "--health-check",
                "--platform",
                "bilibili",
                "--delay",
                "4.0",
                "--config",
                str(self.config_path),
            ]
        )
        self.assertEqual(config.delay, 4.0)

    def test_platform_section_invalid_content_rejected(self):
        with self.assertRaises(cc.ConfigError):
            cc.load_config_file(self._write({"douyin": {"dealy": 3}}))
        with self.assertRaises(cc.ConfigError):
            cc.load_config_file(self._write({"douyin": 5}))
        with self.assertRaises(cc.ConfigError):
            cc.load_config_file(self._write({"bilibili": {"delay": "slow"}}))
        self._write({"douyin": {"delay": 1.0}})
        with self.assertRaises(cc.ConfigError):
            cc.parse_and_validate_args(
                ["--health-check", "--platform", "douyin", "--config", str(self.config_path)]
            )

    def test_merge_platform_config_ignores_other_platform(self):
        values = {
            "delay": 5.0,
            "douyin": {"delay": 3.0},
            "bilibili": {"delay": 8.0},
        }
        douyin = cc.merge_platform_config(values, cc.PLATFORM_DOUYIN)
        bilibili = cc.merge_platform_config(values, cc.PLATFORM_BILIBILI)
        self.assertEqual(douyin, {"delay": 3.0})
        self.assertEqual(bilibili, {"delay": 8.0})

    def test_jitter_range_bounds(self):
        with self.assertRaises(cc.ConfigError):
            cc.CollectorConfig(jitter_range=-0.1).validate()
        with self.assertRaises(cc.ConfigError):
            cc.CollectorConfig(jitter_range=2.01).validate()
        cc.CollectorConfig(jitter_range=0.0).validate()
        cc.CollectorConfig(jitter_range=2.0).validate()

    def test_rate_sleep_uses_jitter_range(self):
        sleeps: list[float] = []
        config = cc.CollectorConfig(delay=4.0, jitter_range=0.4)
        cc.rate_sleep(config, sleeper=sleeps.append, random_fn=lambda: 1.0)
        cc.rate_sleep(config, sleeper=sleeps.append, random_fn=lambda: 0.0)
        self.assertAlmostEqual(sleeps[0], 4.0 * 1.2)
        self.assertAlmostEqual(sleeps[1], 4.0 * 0.8)


class RetryTests(unittest.TestCase):
    """有限重试与退避行为测试。"""

    def test_no_sleep_after_last_failure(self):
        sleeps: list[float] = []
        config = cc.CollectorConfig(max_retry=3, delay=3.0)

        def always_fail():
            return {"code": 1, "error": "synthetic failure"}

        with self.assertRaises(cc.CollectionError):
            cc.request_with_retry(always_fail, config, "合成请求", sleeper=sleeps.append)
        self.assertEqual(sleeps, [3.0, 6.0])


class StateAndCollectorTests(unittest.TestCase):
    """分页、去重、断点恢复、抽样和持久化测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "state.json"
        self.config = cc.CollectorConfig(delay=3.0)

    def test_main_pagination_resume_and_dedup(self):
        bridge = FakeBridge()
        state = cc.new_state("7674214863132396011", 0.5, 42, False)
        collector = cc.CommentsCollector(bridge, self.config, self.output, state)

        with mock.patch.object(cc, "rate_sleep", return_value=None):
            collector.fetch_main(max_pages=1)
        self.assertEqual(len(state["comments"]), 2)
        self.assertEqual(state["meta"]["last_cursor"], 20)
        self.assertFalse(state["meta"]["main_complete"])

        resumed = cc.load_state(self.output)
        assert resumed is not None
        resumed_collector = cc.CommentsCollector(bridge, self.config, self.output, resumed)
        with mock.patch.object(cc, "rate_sleep", return_value=None):
            resumed_collector.fetch_main()

        # 第二页含一条重复 cid，最终应只保留 3 条唯一一级评论。
        self.assertEqual(len(resumed["comments"]), 3)
        self.assertEqual(len({item["cid"] for item in resumed["comments"]}), 3)
        self.assertTrue(resumed["meta"]["main_complete"])
        self.assertEqual(resumed["meta"]["last_cursor"], 40)
        self.assertEqual(resumed["meta"]["main_page_count"], 2)

    def test_final_save_preserves_checkpoint_and_existing_subs(self):
        state = cc.new_state("7674214863132396011", 1.0, 42, True)
        state["meta"].update(
            {"last_cursor": 40, "last_has_more": 0, "main_complete": True}
        )
        state["comments"] = fixture("main-page-1.synthetic.json")["comments"]
        state["subs"] = {
            "7000000000000000001": fixture("reply-page.synthetic.json")["comments"]
        }
        cc.save_state(self.output, state)
        loaded = cc.load_state(self.output)
        assert loaded is not None
        self.assertEqual(loaded["meta"]["last_cursor"], 40)
        self.assertEqual(loaded["meta"]["last_has_more"], 0)
        self.assertTrue(loaded["meta"]["main_complete"])
        self.assertEqual(len(loaded["subs"]["7000000000000000001"]), 2)

    def test_resume_rejects_different_video_or_sampling_settings(self):
        state = cc.new_state("7674214863132396011", 0.5, 42, True)
        with self.assertRaises(cc.ConfigError):
            cc.validate_resume_state(state, "7674193846108943571", 0.5, 42)
        state["meta"]["sampled_cids"] = ["7000000000000000001"]
        with self.assertRaises(cc.ConfigError):
            cc.validate_resume_state(state, "7674214863132396011", 0.6, 42)
        with self.assertRaises(cc.ConfigError):
            cc.validate_resume_state(state, "7674214863132396011", 0.5, 7)

    def test_sampling_is_deterministic(self):
        first = cc.new_state("7674214863132396011", 0.5, 42, True)
        second = cc.new_state("7674214863132396011", 0.5, 42, True)
        c1 = cc.CommentsCollector(FakeBridge(), self.config, self.output, first)
        c2 = cc.CommentsCollector(FakeBridge(), self.config, self.output, second)
        result1 = c1._sample_root("7000000000000000001")
        result2 = c2._sample_root("7000000000000000001")
        self.assertEqual(result1, result2)
        self.assertEqual(first["meta"]["sampled_cids"], second["meta"]["sampled_cids"])
        self.assertEqual(first["meta"]["skipped_cids"], second["meta"]["skipped_cids"])

    def test_sub_collection_and_done_checkpoint(self):
        bridge = FakeBridge()
        state = cc.new_state("7674214863132396011", 1.0, 42, True)
        state["comments"] = fixture("main-page-1.synthetic.json")["comments"]
        collector = cc.CommentsCollector(bridge, self.config, self.output, state)
        with mock.patch.object(cc, "rate_sleep", return_value=None):
            collector.fetch_subs()
        items = state["subs"]["7000000000000000001"]
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item["cid"] for item in items}), 2)
        self.assertIn("7000000000000000001", state["meta"]["sub_done_cids"])

    def test_csv_has_bom_and_expected_hierarchy(self):
        state = cc.new_state("7674214863132396011", 1.0, 42, True)
        state["comments"] = fixture("main-page-1.synthetic.json")["comments"]
        state["subs"] = {
            "7000000000000000001": fixture("reply-page.synthetic.json")["comments"]
        }
        csv_path = Path(self.temp.name) / "comments.csv"
        cc.export_csv(csv_path, state)
        payload = csv_path.read_bytes()
        self.assertTrue(payload.startswith(codecs.BOM_UTF8))
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(row["is_sub"] == "0" for row in rows), 2)
        self.assertEqual(sum(row["is_sub"] == "1" for row in rows), 2)

    def test_bilibili_pagination_resume_and_sub_cursor(self):
        bridge = FakeBilibiliBridge()
        state = cc.new_state(
            "BV1qnuq6dEga", 1.0, 42, True, platform=cc.PLATFORM_BILIBILI
        )
        collector = cc.CommentsCollector(bridge, self.config, self.output, state)

        with mock.patch.object(cc, "rate_sleep", return_value=None):
            collector.fetch_main(max_pages=1)
        self.assertEqual(state["meta"]["last_cursor"], 2)
        self.assertFalse(state["meta"]["main_complete"])
        self.assertEqual(bridge.calls[0][1]["next"], 1)

        resumed = cc.load_state(self.output)
        assert resumed is not None
        resumed_collector = cc.CommentsCollector(
            bridge, self.config, self.output, resumed
        )
        with mock.patch.object(cc, "rate_sleep", return_value=None):
            resumed_collector.fetch_main()
            resumed_collector.fetch_subs()

        self.assertEqual(len(resumed["comments"]), 3)
        self.assertTrue(resumed["meta"]["main_complete"])
        self.assertEqual(resumed["meta"]["last_cursor"], 2)
        self.assertEqual(len(resumed["subs"]["8000000000000000001"]), 1)
        self.assertEqual(
            next(params for endpoint, params in bridge.calls if endpoint == "comment/list/reply")["pn"],
            1,
        )


class CaptchaAndHealthTests(unittest.TestCase):
    """验证状态三态与健康检查退出码测试。"""

    def _client_with_result(self, result):
        client = cc.WebBridgeClient(cc.CollectorConfig(delay=3.0))
        client.eval_js = mock.Mock(return_value=result)
        return client

    def test_captcha_three_states(self):
        self.assertEqual(self._client_with_result({"state": "clear"}).captcha_state(), "clear")
        self.assertEqual(self._client_with_result({"state": "visible"}).captcha_state(), "visible")
        client = cc.WebBridgeClient(cc.CollectorConfig(delay=3.0))
        client.eval_js = mock.Mock(side_effect=cc.PageNotReady("synthetic"))
        self.assertEqual(client.captcha_state(), "check_failed")

    def test_health_check_reports_visible_captcha(self):
        client = cc.WebBridgeClient(cc.CollectorConfig(delay=3.0))
        client.page_status = mock.Mock(
            return_value={"host": "www.douyin.com", "signer_ready": True}
        )
        client.captcha_state = mock.Mock(return_value="visible")
        result, code = client.health_check()
        self.assertFalse(result["ok"])
        self.assertEqual(code, cc.EXIT_MANUAL_ACTION)

    def test_health_check_distinguishes_page_error_from_daemon_failure(self):
        client = cc.WebBridgeClient(cc.CollectorConfig(delay=3.0))
        client.page_status = mock.Mock(side_effect=cc.PageNotReady("session has no tab"))
        result, code = client.health_check()
        self.assertFalse(result["ok"])
        self.assertTrue(result["daemon_reachable"])
        self.assertEqual(code, cc.EXIT_PAGE)

    def test_bilibili_page_check_reads_aid_without_auth_material(self):
        client = cc.WebBridgeClient(
            cc.CollectorConfig(platform=cc.PLATFORM_BILIBILI, delay=3.0)
        )
        client.eval_js = mock.Mock(
            return_value={
                "host": "www.bilibili.com",
                "page_ready": True,
                "oid": 123456,
                "bvid": "BV1qnuq6dEga",
            }
        )
        status = client.check_page_ready("BV1qnuq6dEga")
        self.assertTrue(status["page_ready"])
        self.assertEqual(client.oid, 123456)

    def test_prepare_page_navigates_without_user_preopening_page(self):
        client = cc.WebBridgeClient(
            cc.CollectorConfig(platform=cc.PLATFORM_BILIBILI, delay=3.0)
        )
        client.send = mock.Mock(return_value={"ok": True})
        client.check_page_ready = mock.Mock(
            return_value={"host": "www.bilibili.com", "page_ready": True, "oid": 123}
        )
        client.captcha_state = mock.Mock(return_value="clear")

        with mock.patch.object(cc.time, "sleep", return_value=None):
            status = client.prepare_page("BV1qnuq6dEga", wait_seconds=1)

        client.send.assert_called_once()
        action, args = client.send.call_args.args
        self.assertEqual(action, "navigate")
        self.assertEqual(args["url"], "https://www.bilibili.com/video/BV1qnuq6dEga")
        self.assertEqual(status["host"], "www.bilibili.com")

    def test_connection_refusal_starts_daemon_once_then_retries(self):
        client = cc.WebBridgeClient(cc.CollectorConfig(delay=3.0))
        client._send_once = mock.Mock(
            side_effect=[cc.urllib.error.URLError("refused"), {"ok": True}]
        )
        with mock.patch.object(cc, "start_webbridge_daemon", return_value=True) as start:
            with mock.patch.object(cc.time, "sleep", return_value=None):
                result = client.send("list_tabs", {})
        self.assertEqual(result, {"ok": True})
        start.assert_called_once()

    def test_run_navigates_by_default_before_collecting(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bilibili.json"
            with mock.patch.object(cc, "WebBridgeClient") as bridge_type:
                bridge = bridge_type.return_value
                bridge.oid = 123456
                bridge.prepare_page.return_value = {
                    "host": "www.bilibili.com",
                    "page_ready": True,
                    "oid": 123456,
                }
                bridge.fetch_json.return_value = {
                    "code": 0,
                    "comments": [],
                    "cursor": 1,
                    "has_more": 0,
                    "total": 0,
                }
                result = cc.run(
                    [
                        "https://www.bilibili.com/video/BV1qnuq6dEga",
                        "--platform",
                        "bilibili",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(result, cc.EXIT_OK)
            bridge.prepare_page.assert_called_once_with("BV1qnuq6dEga")
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(document["meta"]["platform"], "bilibili")
            self.assertEqual(document["meta"]["video_id"], "BV1qnuq6dEga")


if __name__ == "__main__":
    unittest.main()
