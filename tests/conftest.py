# -*- coding: utf-8 -*-
"""
pytest 公共配置（tests/conftest.py）
====================================
职责：把生产代码目录加入 sys.path，让测试文件能直接
      `import hfut_scanner` / `import hfut_cli` / `import daily_intel_template`
      / `import send_daily_intel`，无需改动任何生产代码。

历史背景（2026-09-23）
---------------------
本文件最初写了一段 workaround：在 import 生产模块前先把 sys.stdout/stderr
换成 `sys.__stdout__`，以绕开这几个模块在 import 期执行
      `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`
导致的 pytest 崩溃（`ValueError: I/O operation on closed file` + `lost sys.stderr`，
整个测试进程在收集阶段挂掉、一个用例都跑不了）。

**workaround 已删除** —— 因为根因已在源码里修好：那 4 个模块
（hfut_scanner / hfut_cli / hfut_report_gen / hfut_email）现在改用
`stream.reconfigure(encoding=..., errors=...)` 原地改编码，**不再替换宿主对象**。
回归用例见 `test_hfut_site.py::TestNoGlobalStreamHijack`，
它会在子进程里 import 全部四个模块并断言宿主的 StringIO 没被换掉。

保留这段说明是为了：以后谁再写回 `sys.stdout = ...`，能立刻知道为什么不能这么写。
"""

import os
import sys

# 项目根目录 = tests/ 的上一级
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HFUT_SITE = os.path.join(_ROOT, "hfut_site")
_SCRIPTS = os.path.join(_ROOT, "scripts")

for _p in (_HFUT_SITE, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 正常导入即可 —— 生产模块已不再劫持 sys.stdout / sys.stderr
import hfut_scanner           # noqa: E402,F401
import hfut_cli               # noqa: E402,F401
import daily_intel_template   # noqa: E402,F401
import send_daily_intel       # noqa: E402,F401
