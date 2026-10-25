# -*- coding: utf-8 -*-
"""机器人管理逻辑：配置读写校验、进程内启停控制器。GUI 与命令行共用。"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

log = logging.getLogger("bot.manage")

LLM_PRESETS = {
    "zhipu": {"label": "智谱 GLM（有免费额度）", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "kimi": {"label": "Kimi (Moonshot)", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "custom": {"label": "自定义（OpenAI 兼容）", "base_url": "", "model": ""},
}

SKILL_OPTIONS = ["daily_news", "weather", "ping", "help"]


def app_root() -> Path:
    """项目根目录；打包成 exe 后= exe 所在目录（配置/日志跟着 exe 走）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = app_root() / "config.yaml"
EXAMPLE_PATH = app_root() / "config.example.yaml"
LOG_PATH = app_root() / "bot.log"


def read_cfg() -> Tuple[dict, bool]:
    """返回 (配置dict, 是否还是未保存的示例配置)。"""
    is_example = not CONFIG_PATH.exists()
    src = EXAMPLE_PATH if is_example else CONFIG_PATH
    data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    data.setdefault("push", {"jobs": []})
    return data, is_example


def _as_int(v, lo, hi, name) -> int:
    try:
        iv = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是整数")
    if not (lo <= iv <= hi):
        raise ValueError(f"{name} 必须在 {lo}~{hi} 之间")
    return iv


def validate(d: dict) -> dict:
    ch = d.get("channel") or {}
    groups = [g.strip() for g in (ch.get("groups") or []) if str(g).strip()]
    if not groups:
        raise ValueError("至少要填一个监听群名")
    if not str(ch.get("bot_nickname") or "").strip():
        raise ValueError("bot 昵称不能为空（用于识别 @机器人）")
    try:
        ch["poll_interval"] = max(0.3, float(ch.get("poll_interval") or 0.8))
    except (TypeError, ValueError):
        raise ValueError("轮询间隔必须是数字")
    ch["groups"] = groups
    ch["type"] = ch.get("type") if ch.get("type") in ("wxui", "mock") else "wxui"

    llm = d.get("llm") or {}
    llm["model"] = str(llm.get("model") or "").strip() or "glm-4-flash"
    llm["mock"] = bool(llm.get("mock"))
    if not llm["mock"] and not (str(llm.get("base_url") or "").strip() and str(llm.get("api_key") or "").strip()):
        raise ValueError("已关闭 mock 模式，必须填写 base_url 和 api_key")

    rl = d.get("rate_limit") or {}
    rl["per_group_per_minute"] = max(1, _as_int(rl.get("per_group_per_minute") or 8, 1, 120, "每分钟回复上限"))
    rl["max_reply_chars"] = max(50, _as_int(rl.get("max_reply_chars") or 800, 50, 2000, "单条回复字数上限"))
    qh = rl.get("quiet_hours")
    if isinstance(qh, list) and len(qh) == 2 and qh[0] not in ("", None) and qh[1] not in ("", None):
        rl["quiet_hours"] = [_as_int(qh[0], 0, 23, "免打扰开始小时"), _as_int(qh[1], 0, 23, "免打扰结束小时")]
    else:
        rl["quiet_hours"] = None

    mem = d.get("memory") or {}
    mem["history_per_group"] = max(0, _as_int(mem.get("history_per_group") or 12, 0, 100, "上下文条数"))

    jobs = (d.get("push") or {}).get("jobs") or []
    cleaned_jobs = []
    for j in jobs:
        name = str(j.get("name") or "").strip()
        group = str(j.get("group") or "").strip()
        skill = str(j.get("skill") or "").strip()
        if not (name and group and skill):
            continue
        cron = j.get("cron") or {}
        cleaned_jobs.append({
            "name": name,
            "group": group,
            "skill": skill,
            "cron": {"hour": _as_int(cron.get("hour", 8), 0, 23, f"任务[{name}]小时"),
                     "minute": _as_int(cron.get("minute", 0), 0, 59, f"任务[{name}]分钟")},
            "args": [a.strip() for a in (j.get("args") or []) if str(a).strip()],
        })
    d["push"] = {"jobs": cleaned_jobs}

    adm = d.get("admin") or {}
    adm["senders"] = [s.strip() for s in (adm.get("senders") or []) if str(s).strip()]
    return d


def save_cfg(d: dict) -> None:
    d = validate(d)
    if CONFIG_PATH.exists():
        (app_root() / "config.yaml.bak").write_text(
            CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    CONFIG_PATH.write_text(
        yaml.safe_dump(d, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8")


def log_tail(lines: int = 200, max_bytes: int = 16384) -> List[str]:
    """只读文件尾部，避免日志变大后每次刷新整读。"""
    if not LOG_PATH.exists():
        return []
    try:
        with LOG_PATH.open("rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
        ls = data.splitlines()
        if size > max_bytes and ls:
            ls = ls[1:]  # 丢弃截断的首行
        return ls[-lines:]
    except OSError:
        return []


def test_llm(llm: dict) -> str:
    from .config import LLMConfig
    from .llm import LLMClient
    cfg = LLMConfig(
        base_url=str(llm.get("base_url") or ""),
        api_key=str(llm.get("api_key") or ""),
        model=str(llm.get("model") or "glm-4-flash"),
        mock=bool(llm.get("mock")),
        timeout=30,
    )
    return LLMClient(cfg).chat("请用一句话回复：连接成功", [])


class BotController:
    """进程内启停：机器人作为 GUI 进程里的后台线程运行，关闭软件可选择一并停止。"""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()      # 防止连点 启动/停止 开出多个机器人线程
        self._stopping = False

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, config_path: Path | str) -> Tuple[bool, str]:
        with self._lock:
            if self._stopping:
                return False, "机器人正在停止，请等它退完再启动"
            if self.is_running():
                return False, "机器人已在运行"
            from . import main as bm
            self._thread = threading.Thread(
                target=self._run, args=(config_path,), name="bot-main", daemon=True)
            self._thread.start()
        time.sleep(1.0)
        if not self._thread.is_alive():
            return False, "启动失败（配置错误或通道连不上），请看「运行日志」页"
        return True, "机器人已启动，收发情况见「运行日志」页"

    def _run(self, config_path: Path | str) -> None:
        from . import main as bm
        try:
            bm.run_main(["--config", str(config_path)])
        except Exception:  # noqa: BLE001
            log.exception("机器人线程异常退出")

    def stop(self, timeout: float = 20.0) -> Tuple[bool, str]:
        with self._lock:
            if not self.is_running():
                return False, "机器人未在运行"
            self._stopping = True
            try:
                from . import main as bm
                bm.request_stop()
                self._thread.join(timeout=timeout)
                if self._thread.is_alive():
                    return False, "停止超时（通道收尾较慢），稍候会自动退出"
                return True, "机器人已停止"
            finally:
                self._stopping = False
