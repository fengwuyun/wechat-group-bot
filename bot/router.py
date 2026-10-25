# -*- coding: utf-8 -*-
"""Bot 路由核心：白名单过滤 -> 触发判定 -> 限流/免打扰 -> 分发（指令/AI）-> 记忆。"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from .channel import Message
from .config import BotConfig
from .llm import LLMClient
from .skills import run_skill

log = logging.getLogger("bot.router")


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: Dict[str, Deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


class Router:
    def __init__(self, cfg: BotConfig, llm: LLMClient):
        self.cfg = cfg
        self.llm = llm
        self.history: Dict[str, List[Dict[str, str]]] = {}
        self.limiter = RateLimiter(cfg.rate_limit.per_group_per_minute)

    # ---------------------------------------------------------- 主入口
    def handle(self, msg: Message) -> Optional[str]:
        if msg.group not in self.cfg.channel.groups:
            return None
        text = (msg.content or "").strip()
        if not text:
            return None
        # AI 前缀（可能同样以 / 开头，如 /问）优先于指令前缀判定
        if any(text.startswith(p) for p in self.cfg.trigger.ai_prefixes):
            return self._dispatch_ai(msg, text)
        if text.startswith(self.cfg.trigger.command_prefix):
            return self._dispatch_command(msg, text)
        return self._dispatch_ai(msg, text)

    # ---------------------------------------------------------- 指令
    def _dispatch_command(self, msg: Message, text: str) -> Optional[str]:
        if self._quiet() and not self._is_admin(msg):
            return None
        if not self.limiter.allow(msg.group):
            return None
        body = text[len(self.cfg.trigger.command_prefix):].strip()
        parts = body.split(None, 1)
        name = parts[0] if parts else "帮助"
        args = parts[1].split() if len(parts) > 1 else []
        known = name in {"天气", "日报", "帮助", "ping", "help"}
        if not known:
            return f"没有指令「{self.cfg.trigger.command_prefix}{name}」。\n试试 /帮助"
        log.info("[%s] %s 执行指令: %s %s", msg.group, msg.sender, name, args)
        return self._clip(run_skill(name, args))

    # ---------------------------------------------------------- AI
    def _dispatch_ai(self, msg: Message, text: str) -> Optional[str]:
        nick = self.cfg.channel.bot_nickname
        at_tag = f"@{nick}"
        at = self.cfg.trigger.at_bot and nick and at_tag in text
        prefix = next((p for p in self.cfg.trigger.ai_prefixes if text.startswith(p)), None)
        if not (at or prefix):
            return None
        if self._quiet() and not self._is_admin(msg):
            return None
        if not self.limiter.allow(msg.group):
            return None
        question = text
        if prefix:
            question = text[len(prefix):].strip()
        elif at:
            question = re.sub(re.escape(at_tag), "", text).strip()
        if not question:
            question = "在吗？"
        hist = self.history.setdefault(msg.group, [])
        reply = self.llm.chat(question, hist)
        hist.append({"role": "user", "content": question})
        hist.append({"role": "assistant", "content": reply})
        keep = self.cfg.memory.history_per_group * 2
        if len(hist) > keep:
            del hist[:-keep]
        reply = self._clip(reply)
        if msg.sender:
            reply = f"@{msg.sender} {reply}"
        return reply

    # ---------------------------------------------------------- 工具
    def _clip(self, text: str) -> str:
        limit = self.cfg.rate_limit.max_reply_chars
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _is_admin(self, msg: Message) -> bool:
        return msg.sender in self.cfg.admin.senders

    def _quiet(self) -> bool:
        q = self.cfg.rate_limit.quiet_hours
        if not q or len(q) != 2:
            return False
        h = time.localtime().tm_hour
        start, end = q
        if start <= end:
            return start <= h < end
        return h >= start or h < end
