# -*- coding: utf-8 -*-
"""配置加载：config.yaml -> 强类型配置对象。"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class ChannelConfig:
    type: str = "wxui"              # wxui / mock
    poll_interval: float = 0.8
    bot_nickname: str = ""
    groups: List[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = "glm-4-flash"
    mock: bool = True
    system_prompt: str = "你是一个微信群助手，回答简洁有用。"
    timeout: float = 60.0


@dataclass
class TriggerConfig:
    at_bot: bool = True
    ai_prefixes: List[str] = field(default_factory=lambda: ["/问"])
    command_prefix: str = "/"


@dataclass
class RateLimitConfig:
    per_group_per_minute: int = 8
    max_reply_chars: int = 800
    quiet_hours: Optional[List[int]] = None   # [23, 7] = 23:00~次日7:00


@dataclass
class MemoryConfig:
    history_per_group: int = 12


@dataclass
class PushJob:
    name: str
    group: str
    skill: str
    cron: Dict[str, Any] = field(default_factory=dict)   # APScheduler cron 字段
    args: List[str] = field(default_factory=list)


@dataclass
class AdminConfig:
    senders: List[str] = field(default_factory=list)


@dataclass
class BotConfig:
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    push: List[PushJob] = field(default_factory=list)
    admin: AdminConfig = field(default_factory=AdminConfig)

    def llm_ready(self) -> bool:
        return bool(self.llm.base_url and self.llm.api_key) and not self.llm.mock


def _build(dc, data: Dict[str, Any]):
    """把 dict 安全地装进 dataclass（忽略多余键、补默认值）。"""
    if data is None:
        return dc()
    fields = {f.name: f for f in dataclasses.fields(dc)}
    kwargs = {}
    for k, v in data.items():
        if k in fields and v is not None:
            kwargs[k] = v
    return dc(**kwargs)


def load_config(path: str | Path) -> BotConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = BotConfig(
        channel=_build(ChannelConfig, raw.get("channel")),
        llm=_build(LLMConfig, raw.get("llm")),
        trigger=_build(TriggerConfig, raw.get("trigger")),
        rate_limit=_build(RateLimitConfig, raw.get("rate_limit")),
        memory=_build(MemoryConfig, raw.get("memory")),
        admin=_build(AdminConfig, raw.get("admin")),
    )
    jobs = []
    for j in raw.get("push", {}).get("jobs", []) or []:
        if j.get("name") and j.get("group") and j.get("skill"):
            jobs.append(PushJob(**{k: v for k, v in j.items() if k in {"name", "group", "skill", "cron", "args"}}))
    cfg.push = jobs
    if not cfg.channel.groups:
        raise ValueError("config.channel.groups 不能为空：至少填一个要监听的群名")
    return cfg
