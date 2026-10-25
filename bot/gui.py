# -*- coding: utf-8 -*-
"""桌面版控制台（tkinter）：双击即用的原生窗口，无服务、无浏览器。

python -m bot.gui        或双击 配置面板.bat
打包 exe：build_exe.bat（详见 README）

使用习惯自查已落实到代码：
- 首次打开有"配置就绪检查"，缺什么点什么直接跳页签
- 启动成功自动切到日志页；机器人异常退出会红色提醒
- 配置改动实时提示"未保存"，运行中保存会提示"重启后生效"
- 窗口大小/位置/页签记忆（ui.json）；Ctrl+S 保存
- 日志等宽字体、内容变化才刷新、支持一键打开日志/配置目录
- 启停按钮加锁，连点不会开出多个机器人
"""
from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from . import manage
from .manage import LLM_PRESETS, SKILL_OPTIONS

UI_PATH = manage.app_root() / "ui.json"

TYPE_LABELS = [("wxui", "真实微信 wxui（推荐）"), ("mock", "本地模拟 mock（测试用）")]
LOG_TAB = 4


def _post(widget, fn):
    """工作线程 -> UI 线程的安全调度。"""
    try:
        widget.after(0, fn)
    except tk.TclError:
        pass


class JobDialog(tk.Toplevel):
    """单条推送任务的编辑对话框。"""

    def __init__(self, parent, job: Optional[Dict[str, Any]]):
        super().__init__(parent)
        self.title("推送任务")
        self.resizable(False, False)
        self.result: Optional[Dict[str, Any]] = None
        self.transient(parent)
        self.grab_set()

        job = job or {}
        cron = job.get("cron") or {}
        frm = ttk.Frame(self, padding=16)
        frm.grid(sticky="nsew")
        labels = ["任务名", "目标群", "技能", "每天 时", "分", "参数(逗号分隔,如城市)"]
        defaults = [
            job.get("name", ""), job.get("group", ""), job.get("skill") or SKILL_OPTIONS[0],
            cron.get("hour", 8), cron.get("minute", 0), ", ".join(job.get("args") or []),
        ]
        widgets = []
        for i, (lab, dft) in enumerate(zip(labels, defaults)):
            ttk.Label(frm, text=lab).grid(row=i, column=0, sticky="e", padx=(0, 10), pady=5)
            if lab == "技能":
                w: Any = ttk.Combobox(frm, values=SKILL_OPTIONS, state="readonly", width=22)
                w.set(dft)
            else:
                w = ttk.Entry(frm, width=24)
                w.insert(0, str(dft))
            w.grid(row=i, column=1, sticky="w", pady=5)
            widgets.append(w)
        self._w = widgets

        btns = ttk.Frame(frm)
        btns.grid(row=len(labels), column=0, columnspan=2, pady=(14, 0))
        ttk.Button(btns, text="确定", command=self._ok).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=6)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.wait_window(self)

    def _ok(self) -> None:
        name, group, skill, hour, minute, args = [w.get() for w in self._w]
        self.result = {
            "name": name, "group": group, "skill": skill,
            "cron": {"hour": hour, "minute": minute},
            "args": [a.strip() for a in args.split(",") if a.strip()],
        }
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("微信群机器人控制台")
        self.minsize(720, 600)
        self._state = self._load_ui_state()
        self.geometry(self._state.get("geometry", "780x660"))
        self.controller = manage.BotController()
        self.jobs: List[Dict[str, Any]] = []
        self._dirty = False
        self._suppress_exit_flash = False
        self._last_log_text = ""
        self._build()
        self.load()
        try:
            self.nb.select(self._state.get("tab", 0))
        except tk.TclError:
            pass
        self._tick()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ UI 结构
    def _build(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        self.option_add("*Font", ("Microsoft YaHei UI", 10))

        top = ttk.Frame(self, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        ttk.Label(top, text="微信群机器人控制台", font=("Microsoft YaHei UI", 13, "bold"))\
            .pack(side="left")
        self.status_lbl = ttk.Label(top, text="○ 未运行", font=("Microsoft YaHei UI", 11, "bold"),
                                    foreground="#64748b")
        self.status_lbl.pack(side="left", padx=(16, 0))
        self.btn_stop = ttk.Button(top, text="■ 停止", command=self.stop_bot, state="disabled",
                                   width=8)
        self.btn_stop.pack(side="right", padx=(4, 0))
        self.btn_start = ttk.Button(top, text="▶ 启动机器人", command=self.start_bot, width=14)
        self.btn_start.pack(side="right")

        self.check_lbl = ttk.Label(top, text="", foreground="#d97706", cursor="hand2")
        self.check_lbl.pack(side="right", padx=(0, 18))
        self.check_lbl.bind("<Button-1>", self._jump_missing)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=14, pady=4)
        self._tab_basic(self.nb)
        self._tab_llm(self.nb)
        self._tab_trigger(self.nb)
        self._tab_jobs(self.nb)
        self._tab_log(self.nb)

        foot = ttk.Frame(self, padding=(14, 8))
        foot.pack(fill="x")
        ttk.Button(foot, text="💾 保存配置", command=self.save).pack(side="left")
        self.msg_lbl = ttk.Label(foot, text="", foreground="#16a34a")
        self.msg_lbl.pack(side="left", padx=12)
        self.dirty_lbl = ttk.Label(foot, text="", foreground="#d97706")
        self.dirty_lbl.pack(side="left")
        ttk.Label(foot, text="Ctrl+S 快速保存 · 保存后启动/重启生效", foreground="#94a3b8")\
            .pack(side="right")

        self.bind_all("<Control-s>", lambda e: self.save())

    def _tab(self, nb, title):
        f = ttk.Frame(nb, padding=16)
        nb.add(f, text=title)
        f.columnconfigure(1, weight=1)
        return f

    @staticmethod
    def _row(f, r, text):
        ttk.Label(f, text=text).grid(row=r, column=0, sticky="e", padx=(0, 12), pady=6)

    def _watch(self, *variables):
        for v in variables:
            v.trace_add("write", lambda *_: self._mark_dirty())

    def _watch_text(self, *texts):
        for t in texts:
            t.bind("<KeyRelease>", lambda e: self._mark_dirty())

    def _mark_dirty(self):
        if not self._dirty:
            self._dirty = True
            self.dirty_lbl.config(text="● 有未保存的修改")

    def _tab_basic(self, nb):
        f = self._tab(nb, "① 基本设置")
        self.v_type = tk.StringVar(value=TYPE_LABELS[0][1])
        self._row(f, 0, "运行通道")
        ttk.Combobox(f, textvariable=self.v_type, state="readonly", width=32,
                     values=[lab for _, lab in TYPE_LABELS]).grid(row=0, column=1, sticky="w", pady=5)
        self._row(f, 1, "轮询间隔(秒)")
        self.v_poll = tk.StringVar(value="0.8")
        self._watch(self.v_poll)
        ttk.Entry(f, textvariable=self.v_poll, width=10).grid(row=1, column=1, sticky="w", pady=5)
        self._row(f, 2, "机器人昵称")
        self.v_nick = tk.StringVar()
        self._watch(self.v_nick)
        ttk.Entry(f, textvariable=self.v_nick, width=32).grid(row=2, column=1, sticky="we", pady=5)
        ttk.Label(f, text="= 小号的微信昵称；群友 @它 触发 AI", foreground="#94a3b8")\
            .grid(row=2, column=2, sticky="w")
        self._row(f, 3, "监听的群")
        self.t_groups = tk.Text(f, height=5, width=34)
        self._watch_text(self.t_groups)
        self.t_groups.grid(row=3, column=1, rowspan=2, sticky="we", pady=5)
        ttk.Label(f, text="每行一个，必须与微信里\n显示的群名完全一致", foreground="#94a3b8")\
            .grid(row=3, column=2, sticky="nw")

    def _tab_llm(self, nb):
        f = self._tab(nb, "② AI 大模型")
        self._row(f, 0, "快速选择")
        self.v_preset = tk.StringVar()
        box = ttk.Combobox(f, textvariable=self.v_preset, state="readonly", width=30,
                           values=[f"{k} · {p['label']}" for k, p in LLM_PRESETS.items()])
        box.current(0)
        box.grid(row=0, column=1, sticky="w", pady=5)

        def apply_preset(_e=None):
            key = list(LLM_PRESETS)[box.current()]
            p = LLM_PRESETS[key]
            if key != "custom":
                self.v_url.set(p["base_url"])
                self.v_model.set(p["model"])
        box.bind("<<ComboboxSelected>>", apply_preset)
        self._row(f, 1, "API 地址")
        self.v_url = tk.StringVar()
        self._watch(self.v_url)
        ttk.Entry(f, textvariable=self.v_url, width=44).grid(row=1, column=1, sticky="we", pady=5)
        self._row(f, 2, "API Key")
        self.v_key = tk.StringVar()
        self._watch(self.v_key)
        ttk.Entry(f, textvariable=self.v_key, width=44, show="*").grid(row=2, column=1, sticky="we", pady=5)
        self._row(f, 3, "模型名")
        self.v_model = tk.StringVar()
        self._watch(self.v_model)
        ttk.Entry(f, textvariable=self.v_model, width=24).grid(row=3, column=1, sticky="w", pady=5)
        self._row(f, 4, "mock 模式")
        self.v_mock = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="不调真实接口（没有 Key 时勾上，流程照常跑通）",
                        variable=self.v_mock, command=self._mark_dirty)\
            .grid(row=4, column=1, sticky="w", pady=5)
        self._row(f, 5, "人设提示词")
        self.t_prompt = tk.Text(f, height=6, width=52)
        self._watch_text(self.t_prompt)
        self.t_prompt.grid(row=5, column=1, columnspan=2, sticky="we", pady=5)
        bar = ttk.Frame(f)
        bar.grid(row=6, column=1, sticky="w", pady=6)
        ttk.Button(bar, text="🔌 测试连接", command=self.test_llm).pack(side="left")
        self.llm_result = ttk.Label(f, text="", foreground="#475569", wraplength=430)
        self.llm_result.grid(row=7, column=1, columnspan=2, sticky="w")

    def _tab_trigger(self, nb):
        f = self._tab(nb, "③ 触发与安全")
        self.v_at = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="群里 @机器人昵称 触发 AI", variable=self.v_at,
                        command=self._mark_dirty).grid(row=0, column=0, columnspan=2, sticky="w", pady=5)
        self._row(f, 1, "AI 前缀")
        self.v_prefix = tk.StringVar(value="/问, /ai")
        self._watch(self.v_prefix)
        ttk.Entry(f, textvariable=self.v_prefix, width=24).grid(row=1, column=1, sticky="w", pady=5)
        self._row(f, 2, "指令前缀")
        self.v_cmd = tk.StringVar(value="/")
        self._watch(self.v_cmd)
        ttk.Entry(f, textvariable=self.v_cmd, width=8).grid(row=2, column=1, sticky="w", pady=5)
        self._row(f, 3, "每分钟回复上限")
        self.v_rate = tk.StringVar(value="8")
        self._watch(self.v_rate)
        ttk.Entry(f, textvariable=self.v_rate, width=8).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Label(f, text="条/群（防刷屏防风控）", foreground="#94a3b8").grid(row=3, column=2, sticky="w")
        self._row(f, 4, "回复字数上限")
        self.v_chars = tk.StringVar(value="800")
        self._watch(self.v_chars)
        ttk.Entry(f, textvariable=self.v_chars, width=8).grid(row=4, column=1, sticky="w", pady=5)
        self._row(f, 5, "夜间免打扰")
        qf = ttk.Frame(f)
        qf.grid(row=5, column=1, sticky="w", pady=5)
        self.v_qh1 = tk.StringVar()
        self.v_qh2 = tk.StringVar()
        self._watch(self.v_qh1, self.v_qh2)
        ttk.Entry(qf, textvariable=self.v_qh1, width=5).pack(side="left")
        ttk.Label(qf, text=" 点 至 ").pack(side="left")
        ttk.Entry(qf, textvariable=self.v_qh2, width=5).pack(side="left")
        ttk.Label(qf, text="（留空关闭；该时段 AI/指令静默，管理员与推送不受限）", foreground="#94a3b8")\
            .pack(side="left", padx=6)
        self._row(f, 6, "上下文条数")
        self.v_hist = tk.StringVar(value="12")
        self._watch(self.v_hist)
        ttk.Entry(f, textvariable=self.v_hist, width=8).grid(row=6, column=1, sticky="w", pady=5)
        self._row(f, 7, "管理员昵称")
        self.t_admin = tk.Text(f, height=3, width=30)
        self._watch_text(self.t_admin)
        self.t_admin.grid(row=7, column=1, sticky="w", pady=5)
        ttk.Label(f, text="每行一个；免打扰时段\n仍可使用指令", foreground="#94a3b8")\
            .grid(row=7, column=2, sticky="nw")

    def _tab_jobs(self, nb):
        f = self._tab(nb, "④ 定时推送")
        cols = ("name", "group", "skill", "time", "args")
        self.tv = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c, t, w in zip(cols, ("任务名", "目标群", "技能", "时间", "参数"), (110, 150, 110, 70, 180)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w")
        self.tv.pack(fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=10)
        ttk.Button(bar, text="＋ 添加任务", command=self._job_add).pack(side="left", padx=4)
        ttk.Button(bar, text="✎ 编辑", command=self._job_edit).pack(side="left", padx=4)
        ttk.Button(bar, text="✕ 删除", command=self._job_del).pack(side="left", padx=4)
        ttk.Label(f, text="到点向目标群发送技能产出；天气任务请在参数里填城市。改动随「保存配置」一起生效。",
                  foreground="#94a3b8").pack(anchor="w")

    def _tab_log(self, nb):
        f = self._tab(nb, "⑤ 运行日志")
        f.rowconfigure(1, weight=1)
        bar = ttk.Frame(f)
        bar.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 8))
        ttk.Button(bar, text="打开日志文件", command=self._open_log).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="打开配置文件夹", command=self._open_dir).pack(side="left")
        self.t_log = tk.Text(f, height=20, wrap="none", background="#0f172a", foreground="#cbd5e1",
                             insertbackground="#cbd5e1", font=("Consolas", 9))
        self.t_log.grid(row=1, column=0, columnspan=2, sticky="nsew")

    def _open_log(self):
        try:
            if not manage.LOG_PATH.exists():
                manage.LOG_PATH.touch()
            os.startfile(str(manage.LOG_PATH))  # noqa: S606
        except OSError as e:
            messagebox.showerror("打开失败", str(e), parent=self)

    def _open_dir(self):
        os.startfile(str(manage.app_root()))  # noqa: S606

    # ------------------------------------------------------------ 数据进出
    def load(self):
        data, is_example = manage.read_cfg()
        ch, llm, tr = data.get("channel") or {}, data.get("llm") or {}, data.get("trigger") or {}
        rl, mem, adm = data.get("rate_limit") or {}, data.get("memory") or {}, data.get("admin") or {}
        self.v_type.set(dict(TYPE_LABELS).get(ch.get("type") or "wxui", TYPE_LABELS[0][1]))
        self.v_poll.set(ch.get("poll_interval") or 0.8)
        self.v_nick.set(ch.get("bot_nickname") or "")
        self.t_groups.delete("1.0", "end")
        self.t_groups.insert("1.0", "\n".join(ch.get("groups") or []))
        self.v_url.set(llm.get("base_url") or "")
        self.v_key.set(llm.get("api_key") or "")
        self.v_model.set(llm.get("model") or "glm-4-flash")
        self.v_mock.set(bool(llm.get("mock")))
        self.t_prompt.delete("1.0", "end")
        self.t_prompt.insert("1.0", llm.get("system_prompt") or "")
        self.v_at.set(tr.get("at_bot", True))
        self.v_prefix.set(", ".join(tr.get("ai_prefixes") or []))
        self.v_cmd.set(tr.get("command_prefix") or "/")
        self.v_rate.set(rl.get("per_group_per_minute") or 8)
        self.v_chars.set(rl.get("max_reply_chars") or 800)
        qh = rl.get("quiet_hours") or ["", ""]
        self.v_qh1.set(qh[0] if qh[0] != "" else "")
        self.v_qh2.set(qh[1] if qh[1] != "" else "")
        self.v_hist.set(mem.get("history_per_group") or 12)
        self.t_admin.delete("1.0", "end")
        self.t_admin.insert("1.0", "\n".join(adm.get("senders") or []))
        self.jobs = ((data.get("push") or {}).get("jobs")) or []
        self._refresh_jobs()
        self._dirty = False
        self.dirty_lbl.config(text="")
        self._flash("⚠ 当前是示例配置，请修改后保存" if is_example else "已加载 config.yaml")

    def collect(self) -> Dict[str, Any]:
        label = self.v_type.get()
        ch_type = next((v for v, lab in TYPE_LABELS if lab == label), "wxui")
        qh1, qh2 = self.v_qh1.get().strip(), self.v_qh2.get().strip()
        return {
            "channel": {"type": ch_type, "poll_interval": self.v_poll.get(),
                        "bot_nickname": self.v_nick.get().strip(),
                        "groups": self.t_groups.get("1.0", "end").splitlines()},
            "llm": {"base_url": self.v_url.get().strip(), "api_key": self.v_key.get().strip(),
                    "model": self.v_model.get().strip(), "mock": self.v_mock.get(),
                    "system_prompt": self.t_prompt.get("1.0", "end").strip()},
            "trigger": {"at_bot": self.v_at.get(),
                        "ai_prefixes": [s.strip() for s in self.v_prefix.get().split(",") if s.strip()],
                        "command_prefix": self.v_cmd.get().strip() or "/"},
            "rate_limit": {"per_group_per_minute": self.v_rate.get(),
                           "max_reply_chars": self.v_chars.get(),
                           "quiet_hours": [qh1, qh2] if (qh1 and qh2) else None},
            "memory": {"history_per_group": self.v_hist.get()},
            "push": {"jobs": self.jobs},
            "admin": {"senders": self.t_admin.get("1.0", "end").splitlines()},
        }

    def save(self) -> bool:
        try:
            manage.save_cfg(self.collect())
        except ValueError as e:
            messagebox.showerror("配置有误", str(e), parent=self)
            return False
        self._dirty = False
        self.dirty_lbl.config(text="")
        running = self.controller.is_running()
        self._flash("✅ 已保存（机器人运行中：重启后生效）" if running else "✅ 已保存")
        return True

    def _flash(self, text: str, color: str = "#16a34a"):
        self.msg_lbl.config(text=text, foreground=color)
        self.after(8000, lambda: self.msg_lbl.config(text=""))

    # ------------------------------------------------------------ 就绪检查
    def _missing_items(self) -> List[Tuple[str, int]]:
        items: List[Tuple[str, int]] = []
        if not self.v_nick.get().strip():
            items.append(("机器人昵称", 0))
        if not self.t_groups.get("1.0", "end").strip():
            items.append(("监听群名", 0))
        if not self.v_mock.get() and not (self.v_url.get().strip() and self.v_key.get().strip()):
            items.append(("大模型 API 地址/Key", 1))
        return items

    def _refresh_check(self):
        items = self._missing_items()
        if items:
            self.check_lbl.config(text="待完善：" + "、".join(t for t, _ in items), foreground="#d97706")
        else:
            self.check_lbl.config(text="✅ 配置就绪", foreground="#16a34a")

    def _jump_missing(self, _e=None):
        items = self._missing_items()
        if items:
            self.nb.select(items[0][1])

    # ------------------------------------------------------------ 推送任务
    def _refresh_jobs(self):
        self.tv.delete(*self.tv.get_children())
        for i, j in enumerate(self.jobs):
            cron = j.get("cron") or {}
            self.tv.insert("", "end", iid=str(i), values=(
                j.get("name", ""), j.get("group", ""), j.get("skill", ""),
                f"{int(cron.get('hour', 0)):02d}:{int(cron.get('minute', 0)):02d}",
                ", ".join(j.get("args") or [])))
        self._mark_dirty()

    def _job_add(self):
        dlg = JobDialog(self, None)
        if dlg.result and dlg.result["name"]:
            self.jobs.append(dlg.result)
            self._refresh_jobs()

    def _job_edit(self):
        sel = self.tv.selection()
        if not sel:
            return
        i = int(sel[0])
        dlg = JobDialog(self, self.jobs[i])
        if dlg.result and dlg.result["name"]:
            self.jobs[i] = dlg.result
            self._refresh_jobs()

    def _job_del(self):
        sel = self.tv.selection()
        if sel:
            del self.jobs[int(sel[0])]
            self._refresh_jobs()

    # ------------------------------------------------------------ 启停/测试
    def start_bot(self):
        if not self.save():
            return
        self._set_buttons("busy")
        def work():
            ok, msg = self.controller.start(manage.CONFIG_PATH)
            def done():
                self._flash(("✅ " if ok else "❌ ") + msg, "#16a34a" if ok else "#dc2626")
                if ok:
                    self.nb.select(LOG_TAB)
                self._set_buttons()
            _post(self, done)
        threading.Thread(target=work, daemon=True).start()

    def stop_bot(self):
        self._suppress_exit_flash = True
        self._set_buttons("busy")
        def work():
            ok, msg = self.controller.stop()
            def done():
                self._flash(("✅ " if ok else "⚠ ") + msg, "#16a34a" if ok else "#d97706")
                self._set_buttons()
            _post(self, done)
        threading.Thread(target=work, daemon=True).start()

    def _set_buttons(self, state: str = ""):
        running = self.controller.is_running()
        busy = state == "busy"
        self.btn_start.config(state="disabled" if (running or busy) else "normal")
        self.btn_stop.config(state="normal" if running else "disabled")

    def test_llm(self):
        llm = self.collect()["llm"]
        self.llm_result.config(text="测试中…")
        def work():
            try:
                reply = manage.test_llm(llm)
                _post(self, lambda: self.llm_result.config(text="✅ " + reply, foreground="#16a34a"))
            except Exception as e:  # noqa: BLE001
                _post(self, lambda: self.llm_result.config(text=f"❌ {e}", foreground="#dc2626"))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ 周期刷新/退出
    def _tick(self):
        running = self.controller.is_running()
        if getattr(self, "_was_running", False) and not running and not self._suppress_exit_flash:
            self._flash("⚠ 机器人已退出，请查看运行日志", "#dc2626")
        self._suppress_exit_flash = False
        self._was_running = running
        self.status_lbl.config(
            text="● 运行中" if running else "○ 未运行",
            foreground="#16a34a" if running else "#64748b")
        if not self._dirty:
            self._refresh_check()
        text = "\n".join(manage.log_tail(150))
        if text != self._last_log_text:  # 内容变化才重绘，省 CPU/内存
            self._last_log_text = text
            self.t_log.config(state="normal")
            self.t_log.delete("1.0", "end")
            self.t_log.insert("1.0", text)
            self.t_log.see("end")
            self.t_log.config(state="disabled")
        self.after(3000, self._tick)

    # ------------------------------------------------------------ 窗口状态记忆
    @staticmethod
    def _load_ui_state() -> Dict[str, Any]:
        try:
            return json.loads(UI_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _save_ui_state(self):
        try:
            UI_PATH.write_text(json.dumps({
                "geometry": self.geometry(),
                "tab": self.nb.index(self.nb.select()),
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _on_close(self):
        if self.controller.is_running():
            if not messagebox.askyesno("退出", "机器人正在运行。\n停止机器人并退出软件吗？", parent=self):
                return
            self.controller.stop()
        self._save_ui_state()
        self.destroy()


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
