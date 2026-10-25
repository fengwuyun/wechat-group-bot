# -*- coding: utf-8 -*-
"""离线自测：不连微信、不连外网，验证路由/限流/免打扰/记忆逻辑。

运行：python selftest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

from bot.channel import Message  # noqa: E402
from bot.config import BotConfig, ChannelConfig, LLMConfig, RateLimitConfig, TriggerConfig  # noqa: E402
from bot.llm import LLMClient  # noqa: E402
from bot.router import Router  # noqa: E402


def make_cfg(**kw) -> BotConfig:
    cfg = BotConfig()
    cfg.channel = ChannelConfig(bot_nickname="小助手", groups=["测试群"], type="mock")
    cfg.trigger = TriggerConfig(at_bot=True, ai_prefixes=["/问"], command_prefix="/")
    cfg.rate_limit = RateLimitConfig(per_group_per_minute=2, max_reply_chars=100, quiet_hours=None)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_ai_at_trigger():
    cfg = make_cfg()
    r = Router(cfg, LLMClient(LLMConfig(mock=True)))
    reply = r.handle(Message(group="测试群", sender="阿明", content="@小助手 今天有什么新闻"))
    assert reply and "阿明" in reply and "模拟回复" in reply, reply
    print("PASS  @昵称 触发 AI，回复带 @发送者")


def test_ai_prefix_trigger():
    cfg = make_cfg()
    r = Router(cfg, LLMClient(LLMConfig(mock=True)))
    reply = r.handle(Message(group="测试群", sender="阿明", content="/问 帮我写句诗"))
    assert reply and "模拟回复" in reply, reply
    print("PASS  /问 前缀触发 AI")


def test_ordinary_message_ignored():
    cfg = make_cfg()
    r = Router(cfg, LLMClient(LLMConfig(mock=True)))
    assert r.handle(Message(group="测试群", sender="阿明", content="今天天气不错")) is None
    assert r.handle(Message(group="别的群", sender="阿明", content="@小助手 在吗")) is None  # 非白名单
    print("PASS  普通消息/非白名单群不响应")


def test_commands():
    cfg = make_cfg()
    cfg.rate_limit.per_group_per_minute = 10
    r = Router(cfg, LLMClient(LLMConfig(mock=True)))
    assert "pong" in r.handle(Message(group="测试群", sender="x", content="/ping"))
    assert "帮助" in r.handle(Message(group="测试群", sender="x", content="/帮助"))
    assert "用法" in r.handle(Message(group="测试群", sender="x", content="/天气"))
    assert "没有指令" in r.handle(Message(group="测试群", sender="x", content="/不存在的指令"))
    print("PASS  指令分发（ping/帮助/天气缺参/未知指令）")


def test_rate_limit():
    cfg = make_cfg()
    r = Router(cfg, LLMClient(LLMConfig(mock=True)))
    for i in range(2):  # 限额 2/分钟
        assert r.handle(Message(group="测试群", sender="x", content="/ping"))
    assert r.handle(Message(group="测试群", sender="x", content="/ping")) is None
    print("PASS  每群限流生效（第 3 条被吞）")


def test_quiet_hours():
    import time as _t
    cfg = make_cfg()
    cfg.rate_limit.quiet_hours = [_t.localtime().tm_hour, (_t.localtime().tm_hour + 1) % 24]  # 覆盖当前小时
    r = Router(cfg, LLMClient(LLMConfig(mock=True)))
    assert r.handle(Message(group="测试群", sender="路人", content="/ping")) is None
    # 管理员豁免
    cfg.admin.senders = ["老板"]
    assert r.handle(Message(group="测试群", sender="老板", content="/ping"))
    print("PASS  免打扰时段静默，管理员豁免")


def test_memory_trim():
    cfg = make_cfg()
    cfg.memory.history_per_group = 2
    r = Router(cfg, LLMClient(LLMConfig(mock=True)))
    for i in range(5):
        r.handle(Message(group="测试群", sender="x", content=f"@小助手 问题{i}"))
    assert len(r.history["测试群"]) == 4, r.history
    print("PASS  每群上下文按上限裁剪")


def test_config_load():
    from bot.config import load_config
    cfg = load_config(Path(__file__).resolve().parent / "config.example.yaml")
    assert cfg.channel.groups == ["测试群"]
    assert cfg.llm.mock is True  # 示例配置默认 mock
    print("PASS  config.example.yaml 解析正常")


def test_validate_flexibility():
    from bot.manage import validate
    # 昵称可选
    d = {"channel": {"groups": ["群"], "bot_nickname": ""}, "llm": {"mock": True}}
    assert validate(d)["channel"]["bot_nickname"] == ""
    # 本地 Ollama 免 Key
    d2 = {"channel": {"groups": ["群"]}, "llm": {"mock": False, "base_url": "http://127.0.0.1:11434/v1", "api_key": ""}}
    validate(d2)
    # 在线缺 Key 必须报错
    d3 = {"channel": {"groups": ["群"]}, "llm": {"mock": False, "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": ""}}
    try:
        validate(d3)
        raise AssertionError("在线缺 Key 应该报错")
    except ValueError:
        pass
    print("PASS  校验放宽：昵称可选 / Ollama 免 Key / 在线缺 Key 报错")


if __name__ == "__main__":
    test_config_load()
    test_validate_flexibility()
    test_ai_at_trigger()
    test_ai_prefix_trigger()
    test_ordinary_message_ignored()
    test_commands()
    test_rate_limit()
    test_quiet_hours()
    test_memory_trim()
    print("\n全部自测通过 ✅")
