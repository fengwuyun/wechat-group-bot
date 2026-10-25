# -*- coding: utf-8 -*-
"""技能层：群内指令（/天气 /日报 /帮助 ...）与定时推送的数据源。

每个技能是一个函数 -> 返回纯文本。新增技能：写函数并注册到 SKILLS。
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Dict, List, Optional

log = logging.getLogger("bot.skills")

HELP_TEXT = """/帮助  —— 显示本菜单
/问 <问题>  —— AI 问答（也可以直接 @机器人）
/天气 <城市>  —— 查天气，例：/天气 北京
/日报  —— 今日新闻日报（60秒读懂世界）
/ping  —— 存活检测
更多技能持续添加中，欢迎提需求～"""


def _clip(text: str, limit: int = 800) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- 天气
def weather(args: List[str]) -> str:
    city = args[0].strip() if args else ""
    if not city:
        return "用法：/天气 <城市>，例如 /天气 上海"
    import httpx  # 懒加载，未用到技能时不占内存
    # 首选 vvhan 免费接口
    try:
        r = httpx.get(
            "https://api.vvhan.com/api/weather",
            params={"city": city},
            timeout=15,
            headers={"User-Agent": "wx-group-bot/1.0"},
        )
        d = r.json()
        info = (d.get("info") or {}) if d.get("success") else {}
        if info.get("type"):
            air = d.get("air") or {}
            aqi = air.get("aqi")
            air_txt = f" 空气AQI {aqi}({air.get('level', '')})" if aqi else ""
            return _clip(
                f"🌤 {d.get('city', city)}今日：{info.get('type')}，"
                f"{info.get('low', '')}~{info.get('high', '')}，"
                f"{info.get('fengxiang', '')}{info.get('fengli', '')}。{air_txt}\n"
                f"温馨提示：{info.get('tips', '注意增减衣物。')}"
            )
    except Exception as e:  # noqa: BLE001
        log.warning("vvhan 天气失败: %s", e)
    # 备选 wttr.in
    try:
        r = httpx.get(f"https://wttr.in/{city}", params={"format": "j1"}, timeout=15)
        d = r.json()
        cur = d["current_condition"][0]
        today = d["weather"][0]
        return _clip(
            f"🌤 {city}：{cur.get('lang_zh', [{}])[0].get('value', cur.get('weatherDesc', [{}])[0].get('value', ''))}，"
            f"{today['mintempC']}~{today['maxtempC']}℃，湿度{cur.get('humidity', '?')}%，风{cur.get('windspeedKmph', '?')}km/h"
        )
    except Exception as e:  # noqa: BLE001
        log.warning("wttr 天气失败: %s", e)
    return "天气查询暂时不可用，稍后再试～"


# ---------------------------------------------------------------- 日报
def daily_news(args: List[str]) -> str:
    import httpx  # 懒加载
    # 首选 topurl（实测稳定：newsList + 农历 + 每日一句）
    try:
        r = httpx.get("https://news.topurl.cn/api", timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        d = r.json()
        if d.get("code") == 200:
            data = d["data"]
            lines = []
            for i, item in enumerate(data.get("newsList", [])[:20], 1):
                title = re.sub(r"\s+", " ", item.get("title", "")).strip()
                if title:
                    lines.append(f"{i}. {title}")
            cal = data.get("calendar") or {}
            head = f"📰 今日日报（{cal.get('yearCn', '')}{cal.get('monthCn', '')}{cal.get('dayCn', '')}）\n"
            sent = data.get("sentence") or {}
            tail = ""
            if sent.get("sentence"):
                tail = f"\n\n💬 「{sent['sentence']}」—— {sent.get('author', '')}"
            if lines:
                return _clip(head + "\n".join(lines) + tail, 900)
    except Exception as e:  # noqa: BLE001
        log.warning("topurl 日报失败: %s", e)
    # 备选源
    for url in ["https://api.vvhan.com/api/60s", "https://api.aa1.cn/api/60s"]:
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            d = r.json()
            data = d.get("data") or d.get("info") or {}
            news = data.get("news") or d.get("news")
            if isinstance(news, list) and news:
                lines = []
                for i, item in enumerate(news[:20], 1):
                    txt = item if isinstance(item, str) else str(item.get("title", "") or item)
                    txt = re.sub(r"\s+", " ", txt).strip()
                    if txt:
                        lines.append(f"{i}. {txt}")
                head = f"📰 {data.get('date', '今日')}日报（60秒读懂世界）\n"
                tail = f"\n🥠 {data.get('weiyu', '')}" if data.get("weiyu") else ""
                return _clip(head + "\n".join(lines) + tail, 900)
        except Exception as e:  # noqa: BLE001
            log.warning("日报源 %s 失败: %s", url, e)
    return "日报接口暂时都不可用，稍后再试～"


# ---------------------------------------------------------------- 杂项
def ping(args: List[str]) -> str:
    return "pong 🏓 我在线。"


def help_menu(args: List[str]) -> str:
    return HELP_TEXT


SKILLS: Dict[str, Callable[[List[str]], str]] = {
    "weather": weather,
    "天气": weather,
    "daily_news": daily_news,
    "日报": daily_news,
    "ping": ping,
    "help": help_menu,
    "帮助": help_menu,
}


def run_skill(name: str, args: List[str]) -> str:
    fn = SKILLS.get(name)
    if not fn:
        return f"未知技能：{name}。可用：{', '.join(SKILLS)}"
    try:
        return fn(args)
    except Exception as e:  # noqa: BLE001
        log.exception("技能 %s 执行异常", name)
        return f"技能 {name} 出错了：{e}"
