#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comments Catcher 离线安装自检；默认不访问 daemon、浏览器或平台页面。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

SKILL_DIR = Path(__file__).resolve().parents[1]
COLLECTOR = SKILL_DIR / "scripts" / "comments_catcher.py"
REQUIRED_SKILL_FILES = [
    SKILL_DIR / "SKILL.md",
    SKILL_DIR / "config.json",
    SKILL_DIR / "agents" / "openai.yaml",
    COLLECTOR,
    SKILL_DIR / "references" / "setup.md",
    SKILL_DIR / "references" / "cli-reference.md",
    SKILL_DIR / "references" / "architecture.md",
    SKILL_DIR / "references" / "safety-privacy.md",
    SKILL_DIR / "references" / "troubleshooting.md",
    SKILL_DIR / "references" / "output-schema-v1.json",
    SKILL_DIR / "references" / "output-schema-v2.json",
]


def load_collector():
    """从 Skill 内绝对路径加载采集器，验证直接复制安装不依赖当前工作目录。"""
    spec = importlib.util.spec_from_file_location("comments_catcher_smoke", COLLECTOR)
    if not spec or not spec.loader:
        raise RuntimeError("无法创建采集器模块加载器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_frontmatter(skill_md: Path) -> None:
    """执行无第三方依赖的最小 Skill frontmatter 校验。"""
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        raise RuntimeError("SKILL.md 缺少有效 YAML frontmatter")
    header = match.group(1)
    if not re.search(r"^name:\s*comments-catcher\s*$", header, re.MULTILINE):
        raise RuntimeError("SKILL.md name 必须为 comments-catcher")
    if not re.search(r"^description:\s*", header, re.MULTILINE):
        raise RuntimeError("SKILL.md 缺少 description")
    keys = re.findall(r"^([A-Za-z][A-Za-z0-9_-]*):", header, re.MULTILINE)
    if set(keys) != {"name", "description"}:
        raise RuntimeError("SKILL.md frontmatter 只能包含 name 和 description")


def validate_openai_yaml(path: Path) -> None:
    """检查 Codex UI 元数据的关键字段，不依赖第三方 YAML 解析器。"""
    text = path.read_text(encoding="utf-8")
    required = ("display_name:", "short_description:", "default_prompt:", "$comments-catcher")
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(f"agents/openai.yaml 缺少：{', '.join(missing)}")


def find_repo_root() -> Path | None:
    """在完整仓库模式下定位根目录；直接复制 Skill 时允许不存在。"""
    candidate = SKILL_DIR.parents[1]
    if (
        (candidate / ".claude-plugin" / "plugin.json").exists()
        and (candidate / ".codex-plugin" / "plugin.json").exists()
    ):
        return candidate
    return None


def validate_json_file(path: Path) -> dict:
    """读取 JSON 文件并要求根节点为对象。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} 的 JSON 根节点必须为对象")
    return value


def run_command(args: list[str]) -> str:
    """运行确定性本地命令并在失败时附带输出。"""
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stdout + "\n" + completed.stderr).strip()
        raise RuntimeError(f"命令失败（exit={completed.returncode}）：{' '.join(args)}\n{detail}")
    return completed.stdout.strip()


def offline_check() -> dict:
    """执行文件、导入、版本、manifest 与输出 Schema 的离线检查。"""
    missing = [str(path) for path in REQUIRED_SKILL_FILES if not path.is_file()]
    if missing:
        raise RuntimeError("缺少 Skill 文件：\n" + "\n".join(missing))

    validate_frontmatter(SKILL_DIR / "SKILL.md")
    validate_openai_yaml(SKILL_DIR / "agents" / "openai.yaml")
    cache_dirs = list(SKILL_DIR.rglob("__pycache__"))
    if cache_dirs:
        raise RuntimeError("skill 包含 Python 缓存目录：" + ", ".join(map(str, cache_dirs)))
    schema = validate_json_file(SKILL_DIR / "references" / "output-schema-v2.json")
    module = load_collector()
    # config.json 是发布包的一部分，其顶层键与平台小节都必须通过采集器校验规则。
    config_values = module.load_config_file(SKILL_DIR / "config.json")
    for platform in (module.PLATFORM_DOUYIN, module.PLATFORM_BILIBILI):
        merged = module.merge_platform_config(config_values, platform)
        probe = module.CollectorConfig(
            platform=platform,
            delay=float(merged.get("delay", module.DEFAULT_DELAY)),
            jitter_range=float(merged.get("jitter_range", module.DEFAULT_JITTER_RANGE)),
            video_delay=float(merged.get("video_delay", module.DEFAULT_VIDEO_DELAY)),
            dwell=float(merged.get("dwell", module.DEFAULT_DWELL)),
        )
        probe.validate()
        module.validate_sub_rate(float(merged.get("sub_rate", module.DEFAULT_SUB_RATE)))
        if not isinstance(merged.get("seed", module.DEFAULT_SEED), int):
            raise RuntimeError(f"config.json 的 {platform} 平台 seed 必须是整数")
    version_output = run_command([sys.executable, str(COLLECTOR), "--version"])
    run_command([sys.executable, str(COLLECTOR), "--help"])

    expected_version = str(module.COLLECTOR_VERSION)
    if expected_version not in version_output:
        raise RuntimeError("--version 输出与模块版本不一致")
    if schema.get("$id") != "urn:comments-catcher:output-schema:v2" or schema.get("type") != "object":
        raise RuntimeError("v2 输出 Schema 缺少正确的 $id 或对象根类型")
    if module.detect_platform("https://www.bilibili.com/video/BV1qnuq6dEga") != module.PLATFORM_BILIBILI:
        raise RuntimeError("B 站平台自动识别失败")
    if module.extract_video_id("av123456", module.PLATFORM_BILIBILI) != "av123456":
        raise RuntimeError("B 站 av 号解析失败")

    repo = find_repo_root()
    manifests_checked = False
    if repo:
        plugin = validate_json_file(repo / ".claude-plugin" / "plugin.json")
        codex_plugin = validate_json_file(repo / ".codex-plugin" / "plugin.json")
        marketplace = validate_json_file(repo / ".claude-plugin" / "marketplace.json")
        if plugin.get("name") != "comments-catcher":
            raise RuntimeError("plugin.json name 不正确")
        if plugin.get("version") != expected_version:
            raise RuntimeError("plugin.json version 与采集器版本不一致")
        if codex_plugin.get("name") != "comments-catcher":
            raise RuntimeError(".codex-plugin/plugin.json name 不正确")
        if codex_plugin.get("version") != expected_version:
            raise RuntimeError(".codex-plugin/plugin.json version 与采集器版本不一致")
        plugins = marketplace.get("plugins", [])
        if not any(item.get("name") == "comments-catcher" for item in plugins):
            raise RuntimeError("marketplace.json 未登记 comments-catcher")
        manifests_checked = True

    return {
        "ok": True,
        "mode": "full-repository" if repo else "standalone-skill",
        "collector_version": expected_version,
        "schema_version": str(module.SCHEMA_VERSION),
        "manifests_checked": manifests_checked,
    }


def main() -> None:
    """命令行入口；仅在显式 ``--live`` 时连接本机 WebBridge。"""
    parser = argparse.ArgumentParser(description="Comments Catcher 安装与文件完整性自检")
    parser.add_argument(
        "--live",
        action="store_true",
        help="离线检查后额外执行 WebBridge --health-check",
    )
    args = parser.parse_args()

    try:
        result = offline_check()
        if args.live:
            completed = subprocess.run(
                [sys.executable, str(COLLECTOR), "--health-check"],
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            result["live_exit_code"] = completed.returncode
            if completed.returncode != 0:
                raise RuntimeError(f"WebBridge 实时健康检查未通过（exit={completed.returncode}）")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[自检失败] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
