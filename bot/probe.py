# -*- coding: utf-8 -*-
"""UI 探针：把微信主窗口/独立聊天窗口的控件树 dump 到文件，用于版本适配排查。

用法：
    python -m bot.probe                 # 只 dump 主窗口
    python -m bot.probe --window 群名   # 附加 dump 指定独立聊天窗口
输出：probe_output.txt（发回给开发者即可定位控件规格变化）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "probe_output.txt"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", help="附加 dump 一个独立聊天窗口（按群名/聊天名）")
    args = ap.parse_args()

    try:
        from pywinauto import Desktop
    except ImportError:
        print("请先安装依赖：pip install -r requirements.txt")
        return 1

    desktop = Desktop(backend="uia")
    lines: list[str] = []

    def dump(title: str, win) -> None:
        lines.append(f"\n{'=' * 70}\n### {title}\n{'=' * 70}\n")
        try:
            win.print_control_identifiers(depth=6)
        except Exception as e:  # noqa: BLE001
            lines.append(f"<dump 失败: {e}>\n")

    # print_control_identifiers 直接打 stdout，这里重定向到文件
    import io
    from contextlib import redirect_stdout

    main_win = desktop.window(class_name_re="mmui::MainWindow", title_re="微信|Weixin")
    try:
        main_win.wait("visible", timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"找不到微信主窗口：{e}\n请确认微信已登录且语言为简体中文。")
        return 1
    buf = io.StringIO()
    with redirect_stdout(buf):
        dump("微信主窗口", main_win)
        if args.window:
            w = desktop.window(class_name="mmui::ChatSingleWindow", title=args.window)
            try:
                w.wait("visible", timeout=5)
                dump(f"独立聊天窗口：{args.window}", w)
            except Exception as e:  # noqa: BLE001
                lines.append(f"\n### 独立窗口「{args.window}」不存在或不可见: {e}\n")
    lines.append(buf.getvalue())

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {OUT}")
    print("提示：在微信里右键任意群 -> 「在独立聊天窗口中打开」，再运行 --window <群名> 可采集更多控件树。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
