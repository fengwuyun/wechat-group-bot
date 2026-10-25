# -*- coding: utf-8 -*-
"""LLM 客户端：OpenAI 兼容接口（智谱/DeepSeek/Kimi 等通用），支持 mock 模式。"""
from __future__ import annotations

import logging
import time
from typing import Dict, List

from .config import LLMConfig

log = logging.getLogger("bot.llm")


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    @property
    def mock(self) -> bool:
        return self.cfg.mock or not (self.cfg.base_url and self.cfg.api_key)

    def chat(self, question: str, history: List[Dict[str, str]]) -> str:
        """history: 每群独立的多轮上下文 [{role, content}, ...]"""
        if self.mock:
            return f"〔模拟回复〕已收到「{question}」。填好 llm.base_url/api_key 后我将真正调用大模型作答。"
        import httpx  # 懒加载：mock 模式/纯 GUI 状态下不占内存
        messages = [{"role": "system", "content": self.cfg.system_prompt}]
        messages += history[-24:]
        messages.append({"role": "user", "content": question})
        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 900,
        }
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        last_err = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self.cfg.timeout) as cli:
                    resp = cli.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    if text and text.strip():
                        return text.strip()
                    last_err = RuntimeError("空回复")
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("LLM 第 %s 次调用失败: %s", attempt + 1, e)
                time.sleep(1.5)
        log.error("LLM 调用最终失败: %s", last_err)
        return "（大脑暂时开小差了，稍后再试一次～）"
