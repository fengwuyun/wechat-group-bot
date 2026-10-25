# -*- coding: utf-8 -*-
"""微信 4.x PC 客户端 UIAutomation 通道（自研，方法参照 pyweixin/pymwx 的公开实现）。

原理：
- 用 pywinauto(uia backend) 读取微信主窗口/独立聊天窗口的控件树
- 为每个监听的群打开一个「独立聊天窗口」，轮询其消息列表最后一条的 runtime_id
  检测新消息；群聊中他人消息带昵称 Text，自己气泡没有 -> 以此过滤回声
- 发送：聚焦窗口 -> 剪贴板粘贴 -> Alt+S（微信自带发送快捷键）

对 UI 树的假设来自微信 4.1.x 简体中文界面：
- 主窗口: class mmui::MainWindow, title 微信/Weixin
- 独立窗口: class mmui::ChatSingleWindow, title=聊天名称
- 消息列表: List title=消息；文本消息 item class mmui::ChatTextItemView
- 输入框: Edit auto_id=chat_input_field / class mmui::ChatInputField
若微信版本升级导致失效，运行 `python -m bot.probe` 重新采集控件树校准。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import pyautogui
import pyperclip
from pywinauto import Desktop

from .channel import Channel, ChannelError, Message
from .config import BotConfig

log = logging.getLogger("bot.wxui")

TS_RE = re.compile(r"^(下午|上午|昨天|前天)?\s*\d{1,2}:\d{2}$|^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
SYSTEM_HINTS = ("撤回了一条消息", "加入了群聊", "移出了群聊", "以上是新消息", "邀请你加入了群聊", "开启了朋友验证")


class WxuiChannel(Channel):
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.desktop: Optional[Desktop] = None
        self.main = None
        self.windows: Dict[str, object] = {}
        self._last_rid: Dict[str, tuple] = {}
        self._msg_list: Dict[str, object] = {}     # 消息列表控件缓存（失效才重扫）
        self._err: Dict[str, int] = {}             # 每窗口连续出错次数（自愈用）
        self._next_reconnect: float = 0.0
        self._own: List[str] = []                  # 自己最近发出的文本（规范化后），防回声
        pyautogui.FAILSAFE = False

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        self.desktop = Desktop(backend="uia")
        self.main = self.desktop.window(class_name_re="mmui::MainWindow", title_re="微信|Weixin")
        try:
            self.main.wait("visible ready", timeout=15)
        except Exception as e:  # noqa: BLE001
            raise ChannelError(
                "找不到微信主窗口：请确认 PC 微信 4.x 已登录、语言为简体中文。"
                f"（{e}）"
            ) from e
        log.info("已连接微信主窗口")
        for g in self.cfg.channel.groups:
            try:
                self.ensure_window(g)
            except Exception as e:  # noqa: BLE001
                log.error("打开群「%s」的独立聊天窗口失败: %s", g, e)
        if not self.windows:
            raise ChannelError("没有任何一个群成功打开聊天窗口，请检查群名是否与微信一致（probe 工具可辅助排查）")
        log.info("监听中：%s", list(self.windows))

    # ------------------------------------------------------------ 窗口定位
    def ensure_window(self, group: str):
        win = self.windows.get(group)
        if win is not None:
            try:
                if win.exists():
                    return win
            except Exception:  # noqa: BLE001
                pass
        self.main.set_focus()
        time.sleep(0.4)
        # 已在会话列表 -> 直接双击打开独立窗口
        item = self._session_item(group)
        if item is None:
            self._search(group)
            item = self._session_item(group, wait=4.0)
        if item is None:
            raise ChannelError(f"会话列表/搜索里找不到群「{group}」")
        item.double_click_input()
        time.sleep(1.2)
        win = self.desktop.window(class_name="mmui::ChatSingleWindow", title=group)
        win.wait("visible", timeout=8)
        self.windows[group] = win
        self._last_rid[group] = None
        self._msg_list[group] = None
        log.info("已打开独立聊天窗口：%s", group)
        return win

    def _session_item(self, group: str, wait: float = 1.0):
        """会话列表项的 automation_id 形如 session_item_<名称>。"""
        end = time.time() + wait
        while time.time() < end:
            try:
                it = self.main.child_window(auto_id=f"session_item_{group}", control_type="ListItem")
                if it.exists():
                    return it
            except Exception:  # noqa: BLE001
                pass
            # 退化：按标题匹配 ListItem
            try:
                for it in self.main.descendants(control_type="ListItem"):
                    if it.element_info.name == group:
                        return it
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        return None

    def _search(self, text: str) -> None:
        try:
            edits = self.main.descendants(control_type="Edit", class_name="mmui::XValidatorTextEdit")
        except Exception as e:  # noqa: BLE001
            raise ChannelError(f"定位搜索框失败: {e}") from e
        if not edits:
            raise ChannelError("找不到微信搜索框")
        box = edits[1] if len(edits) == 2 else edits[0]
        box.click_input()
        box.set_text("")
        box.set_text(text)
        time.sleep(1.0)
        # 点击第一条联系人/群聊搜索结果
        try:
            results = self.main.descendants(control_type="ListItem", class_name="mmui::SearchContentCellView")
            for r in results:
                name = (r.element_info.name or "").strip()
                if name == text or text in name:
                    r.click_input()
                    time.sleep(0.8)
                    return
            if results:
                results[0].click_input()
                time.sleep(0.8)
                return
        except Exception as e:  # noqa: BLE001
            log.warning("点击搜索结果失败: %s", e)
        pyautogui.hotkey("esc")

    # ------------------------------------------------------------ 收消息
    def poll(self) -> List[Message]:
        out: List[Message] = []
        # 主窗口失联（微信重启/退出）-> 指数退避重连
        if not self._main_alive():
            if time.time() >= self._next_reconnect:
                log.warning("与微信主窗口失去连接，尝试重连…")
                try:
                    self.start()
                    self._next_reconnect = 0.0
                    log.info("重连成功")
                except Exception as e:  # noqa: BLE001
                    log.warning("重连失败：%s（30 秒后重试）", e)
                    self._next_reconnect = time.time() + 30
            return out
        for group, win in list(self.windows.items()):
            try:
                if not win.exists():
                    raise ChannelError("聊天窗口已被关闭")
                out.extend(self._poll_window(group, win))
                self._err[group] = 0
            except Exception as e:  # noqa: BLE001
                n = self._err.get(group, 0) + 1
                self._err[group] = n
                if n == 1 or n % 60 == 0:  # 首次与每分钟提醒一次，不刷屏
                    log.warning("监听窗口「%s」异常：%s", group, e)
                if n in (10, 600):  # ~8 秒后重开一次，之后每 8 分钟
                    log.info("尝试重新打开群「%s」的聊天窗口", group)
                    try:
                        self.ensure_window(group)
                        self._err[group] = 0
                    except Exception as e2:  # noqa: BLE001
                        log.warning("重开窗口「%s」失败：%s", group, e2)
        return out

    def _main_alive(self) -> bool:
        try:
            return bool(self.main.exists(timeout=0.2))
        except Exception:  # noqa: BLE001
            return False

    def _get_msg_list(self, group: str, win):
        """消息列表控件缓存：有效直接复用，失效才重新扫描（省 UIA 开销）。"""
        lst = self._msg_list.get(group)
        try:
            if lst is not None and (lst.element_info.name or "") == "消息":
                return lst
        except Exception:  # noqa: BLE001
            pass  # 缓存失效
        try:
            lists = [l for l in win.descendants(control_type="List") if (l.element_info.name or "") == "消息"]
            lst = lists[0] if lists else None
        except Exception:  # noqa: BLE001
            lst = None
        self._msg_list[group] = lst
        return lst

    def _poll_window(self, group: str, win) -> List[Message]:
        msgs: List[Message] = []
        lst = self._get_msg_list(group, win)
        if lst is None:
            raise ChannelError("找不到消息列表")
        items = lst.children(control_type="ListItem")
        if not items:
            return msgs
        tail_rid = tuple(items[-1].element_info.runtime_id or [])
        last_rid = self._last_rid.get(group)
        if last_rid is None:
            # 首次见到该窗口：只记录指针，不把历史消息当新消息回复
            self._last_rid[group] = tail_rid
            return msgs
        if tail_rid == last_rid:
            return msgs
        # 从尾部往前收集所有新消息（两条间隔小于轮询间隔也不丢）
        new_items = []
        for it in reversed(items):
            rid = tuple(it.element_info.runtime_id or [])
            if rid == last_rid:
                break
            new_items.append(it)
            if len(new_items) >= 20:  # 突发消息防洪：一次最多追 20 条
                break
        self._last_rid[group] = tail_rid
        nick = self.cfg.channel.bot_nickname
        for it in reversed(new_items):  # 恢复时间顺序
            parsed = self._parse_item(it)
            if not parsed:
                continue
            sender, content = parsed
            if not content or self._is_echo(content):
                continue
            log.info("[%s] %s: %s", group, sender or "自己", content[:60])
            msgs.append(Message(
                group=group,
                sender=sender or "",
                content=content,
                is_at_bot=bool(nick and f"@{nick}" in content),
            ))
        return msgs

    def _parse_item(self, item) -> Optional[Tuple[Optional[str], Optional[str]]]:
        """从消息 ListItem 提取 (发送者, 文本内容)。群聊他人消息有昵称，自己气泡没有。"""
        cls = item.element_info.class_name or ""
        if "ChatSystemInfoItemView" in cls or cls.endswith("::ChatItemView"):
            return None
        try:
            texts = [
                t.element_info.name.strip()
                for t in item.descendants(control_type="Text")
                if t.element_info.name and t.element_info.name.strip()
            ]
        except Exception:  # noqa: BLE001
            return None
        texts = [t for t in texts if not TS_RE.match(t) and not any(h in t for h in SYSTEM_HINTS)]
        if not texts:
            return None
        if len(texts) >= 2:
            sender, content = texts[0], texts[-1]
        else:
            return None  # 无昵称 -> 自己的气泡或非文本，忽略
        content = content.strip()
        if not content:
            return None
        return sender, content

    def _is_echo(self, content: str) -> bool:
        norm = re.sub(r"\s+", "", content)
        return any(norm == re.sub(r"\s+", "", own) for own in self._own[-30:])

    # ------------------------------------------------------------ 发消息
    def send(self, group: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        win = self.ensure_window(group)
        win.set_focus()
        time.sleep(0.3)
        inp = self._input_box(win)
        if inp is None:
            raise ChannelError(f"窗口「{group}」找不到输入框")
        inp.click_input()
        old = ""
        try:
            old = pyperclip.paste()
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.35)
            pyautogui.hotkey("alt", "s")
            time.sleep(0.5)
        finally:
            try:
                if old:
                    pyperclip.copy(old)
            except Exception:  # noqa: BLE001
                pass
        self._own.append(re.sub(r"\s+", "", text))
        log.info("已发送到 [%s]：%s", group, text[:60])

    def _input_box(self, win):
        try:
            it = win.child_window(control_type="Edit", auto_id="chat_input_field")
            if it.exists():
                return it
        except Exception:  # noqa: BLE001
            pass
        edits = [e for e in win.descendants(control_type="Edit") if "ChatInputField" in (e.element_info.class_name or "")]
        return edits[-1] if edits else None

    def stop(self) -> None:
        log.info("通道停止")


SYSTEM_SESSIONS = {"文件传输助手", "微信支付", "公众号", "订阅号消息", "服务通知", "折叠的聊天",
                   "腾讯新闻", "QQ邮箱提醒", "腾讯文档", "视频号私信"}


def list_wechat_sessions() -> List[str]:
    """读取微信会话列表里最近出现的聊天名（好友+群聊，供 GUI 勾选）。只读，不动 UI。"""
    desktop = Desktop(backend="uia")
    main = desktop.window(class_name_re="mmui::MainWindow", title_re="微信|Weixin")
    main.wait("visible ready", timeout=8)
    names = set()
    for it in main.descendants(control_type="ListItem"):
        aid = it.element_info.automation_id or ""
        if aid.startswith("session_item_"):
            n = aid[len("session_item_"):]
            if n and n not in SYSTEM_SESSIONS:
                names.add(n)
    return sorted(names)
