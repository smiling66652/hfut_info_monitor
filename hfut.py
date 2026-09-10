#!/usr/bin/env python3
"""
hfut.py — 合工大信息巡检系统 统一入口 v5.0
==========================================
一套系统，一个入口。官网 18 栏目扫描 + QQ / 公众号采集 + AI 三层分析 + 邮件推送。

用法：
  python hfut.py scan              增量扫描官网 18 栏目 + 发邮件（日常）
  python hfut.py scan --full       全量扫描
  python hfut.py scan --no-email   只扫描不发邮件
  python hfut.py report            读现有数据出摘要（0 网络请求）
  python hfut.py status            系统状态
  python hfut.py kb 保研           按关键词查历史索引
  python hfut.py all               全源巡检（官网+QQ群+QQ空间+公众号+AI分析+邮件）
  python hfut.py bootstrap         环境自检（Ollama / NapCat / 依赖）

环境：必须用项目 venv → .venv/Scripts/python.exe
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "hfut_site")
PY = sys.executable

# 官网 18 栏目扫描（v3.2 核心，10 秒级）
SITE_CMDS = {"scan", "report", "status", "kb"}


def _run(args, cwd, timeout=600):
    try:
        r = subprocess.run([PY] + args, cwd=cwd, timeout=timeout)
        return r.returncode
    except subprocess.TimeoutExpired:
        print(f"[hfut] ❌ 超时（>{timeout}s）：{' '.join(args)}")
        return 1
    except FileNotFoundError:
        print(f"[hfut] ❌ 找不到解释器：{PY}")
        return 1


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0

    cmd = args[0].lower()

    # 官网扫描子系统：转发到 hfut_site/hfut_cli.py
    if cmd in SITE_CMDS:
        return _run([os.path.join(SITE, "hfut_cli.py")] + args, cwd=SITE, timeout=360)

    # 全源巡检：转发到主控制器
    if cmd == "all":
        return _run(["main_controller.py", "--mode", "all"] + args[1:], cwd=ROOT, timeout=1800)

    # 环境自检
    if cmd == "bootstrap":
        return _run([os.path.join("scripts", "environment_bootstrap.py"), "--check-only"], cwd=ROOT, timeout=180)

    print(f"[hfut] 未知命令: {cmd}")
    print("可用: scan [--full|--no-email] | report | status | kb <关键词> | all | bootstrap")
    return 1


if __name__ == "__main__":
    sys.exit(main())
