# -*- coding: utf-8 -*-
"""入口：组装通道/LLM/路由/定时推送，主循环轮询。

用法：
    python -m bot.main --config config.yaml           # 真实微信通道
    python -m bot.main --config config.yaml --mock    # 控制台模拟（不连微信）
    python -m bot.main --config config.yaml --probe   # 打印收到的消息但不回复
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .channel import Channel, MockChannel
from .config import BotConfig, load_config
from .llm import LLMClient
from .router import Router
from .skills import run_skill

log = logging.getLogger("bot.main")
_running = True
PID_PATH = Path(__file__).resolve().parent.parent / "bot.pid"


def request_stop() -> None:
    """请求主循环优雅退出（信号处理器 / GUI 控制器都调这里）。"""
    global _running
    _running = False


def _setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if sys.stdout:  # pythonw/打包 exe 下无控制台，只写文件
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)
    log_path = Path(__file__).resolve().parent.parent / "bot.log"
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


def build_channel(cfg: BotConfig, force_mock: bool) -> Channel:
    kind = "mock" if force_mock else cfg.channel.type
    if kind == "mock":
        return MockChannel(interactive=True)
    from .wxui_channel import WxuiChannel  # 延迟导入：mock 模式无需 pywinauto
    return WxuiChannel(cfg)


def setup_pushes(cfg: BotConfig, channel: Channel) -> object | None:
    if not cfg.push:
        return None
    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler()

    def run_push(job) -> None:
        try:
            text = run_skill(job.skill, job.args or [])
            channel.send(job.group, text)
            log.info("定时推送完成：%s -> %s", job.name, job.group)
        except Exception:  # noqa: BLE001
            log.exception("定时推送失败：%s", job.name)

    for job in cfg.push:
        try:
            sched.add_job(run_push, "cron", args=[job], id=job.name, **(job.cron or {}))
            log.info("已注册定时推送：%s（%s -> %s）", job.name, job.cron, job.group)
        except Exception:  # noqa: BLE001
            log.exception("定时任务 %s 注册失败，cron=%s", job.name, job.cron)
    sched.start()
    return sched


def run_main(argv: list[str] | None = None) -> int:
    """可嵌入入口：命令行或 GUI 线程都从这里启动机器人。"""
    ap = argparse.ArgumentParser(description="微信群机器人")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mock", action="store_true", help="强制使用控制台模拟通道")
    ap.add_argument("--probe", action="store_true", help="只打印识别到的消息，不回复")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    _setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    cfg = load_config(args.config)
    try:
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass  # 只读环境（如打包 exe）下跳过
    log.info(
        "配置加载完成：通道=%s 群=%s bot昵称=%s LLM=%s",
        "mock" if args.mock else cfg.channel.type,
        cfg.channel.groups,
        cfg.channel.bot_nickname,
        "mock" if cfg.llm.mock or not cfg.llm_ready() else cfg.llm.model,
    )

    channel = build_channel(cfg, args.mock)
    llm = LLMClient(cfg.llm)
    router = Router(cfg, llm)

    if threading.current_thread() is threading.main_thread():
        def _sigint(_sig, _frm):
            request_stop()
            print("\n收到退出信号，正在停止…")
        signal.signal(signal.SIGINT, _sigint)
        signal.signal(signal.SIGTERM, _sigint)

    try:
        channel.start()
    except Exception as e:  # noqa: BLE001
        log.error("通道启动失败：%s", e)
        return 1

    sched = setup_pushes(cfg, channel)

    log.info("机器人开始运行（Ctrl+C 退出）…")
    interval = max(0.3, float(cfg.channel.poll_interval))
    try:
        while _running:
            try:
                for msg in channel.poll():
                    if args.probe:
                        log.info("[probe] 收到 %s/%s: %s", msg.group, msg.sender, msg.content)
                        continue
                    try:
                        reply = router.handle(msg)
                    except Exception:  # noqa: BLE001
                        log.exception("处理消息异常")
                        continue
                    if reply:
                        channel.send(msg.group, reply)
                        time.sleep(0.8)  # 发送节流，行为更像真人
            except Exception:  # noqa: BLE001
                log.exception("轮询异常，5 秒后重试")
                time.sleep(5)
            time.sleep(interval)
    finally:
        if sched:
            sched.shutdown(wait=False)
        channel.stop()
        try:
            PID_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        log.info("已退出")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8") if sys.stdout else None
    raise SystemExit(run_main())
