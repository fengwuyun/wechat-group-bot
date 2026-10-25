# -*- coding: utf-8 -*-
"""通道抽象：所有收发消息的实现都遵循 Channel 接口。

- MockChannel：控制台/脚本模拟，本地自测用
- WxuiChannel：微信 4.x PC 客户端 UIAutomation 真实通道（见 wxui_channel.py）
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass
class Message:
    group: str
    sender: str
    content: str
    is_at_bot: bool = False
    ts: float = field(default_factory=time.time)


class ChannelError(RuntimeError):
    pass


class Channel:
    def start(self) -> None: ...
    def poll(self) -> List[Message]:
        raise NotImplementedError
    def send(self, group: str, text: str) -> None:
        raise NotImplementedError
    def stop(self) -> None: ...


class MockChannel(Channel):
    """交互模式：stdin 每行输入 `群名|发送者|内容` 模拟群消息；
    测试模式：直接传 feed 列表。所有 send 打印到控制台。"""

    def __init__(self, feed: Optional[Iterable[Message]] = None, interactive: bool = False):
        self.interactive = interactive
        self._q: "queue.Queue[Message]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        for m in feed or []:
            self._q.put(m)

    def start(self) -> None:
        if self.interactive:
            self._thread = threading.Thread(target=self._read_stdin, daemon=True)
            self._thread.start()
        print("[mock] 通道已启动。输入 `群名|发送者|内容` 模拟群消息，Ctrl+C 退出。")

    def _read_stdin(self) -> None:
        import sys
        if sys.stdin is None:  # pythonw / 打包 exe 下无标准输入
            return
        for line in sys.stdin:
            line = line.strip()
            if not line or self._stop.is_set():
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                self._q.put(Message(group=parts[0].strip(), sender=parts[1].strip(), content=parts[2].strip()))
            else:
                print("[mock] 格式应为 群名|发送者|内容")

    def poll(self) -> List[Message]:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def send(self, group: str, text: str) -> None:
        print(f"\n<<< 发往 [{group}]:\n{text}\n---")

    def stop(self) -> None:
        self._stop.set()
