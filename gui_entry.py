# -*- coding: utf-8 -*-
"""PyInstaller 打包入口（生成单文件 exe）。"""
import multiprocessing

multiprocessing.freeze_support()

from bot.gui import main

raise SystemExit(main())
