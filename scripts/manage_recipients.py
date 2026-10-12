#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《每日情报》收件人管理脚本（2026-09-23 固化）。

为什么需要这个脚本：
    直接打开 `hfut_site/hfut_email_config.json` 读/写会让 **SMTP 授权码进入对话上下文**。
    本脚本只在进程内读写该文件，输出里 **永不打印 password**，只回显邮箱列表。

用法（必须用项目 venv）：
    python scripts/manage_recipients.py list                     # 列出收件人（含序号）
    python scripts/manage_recipients.py add a@x.com b@y.com      # 新增（自动去重、自动备份）
    python scripts/manage_recipients.py remove a@x.com           # 删除（自动备份）
    python scripts/manage_recipients.py check a@x.com            # 判断是否已在名单里（退出码 0=在 / 1=不在）

约定：
    - 增删收件人**只动配置文件**，不写死人数、不改代码、不改文档。
    - 每次写操作前自动备份到 `temp_files/_backup_email_config_<时间戳>.json`。
    - 「补发」不在这里做，用 `scripts/send_daily_intel.py --to <邮箱>`。
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_FILE = os.path.join(ROOT, "hfut_site", "hfut_email_config.json")
BACKUP_DIR = os.path.join(ROOT, "temp_files")

# 只用于回显，绝不读取/打印 password 的值
SAFE_KEYS = ("smtp_host", "smtp_port", "sender", "recipients")


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        sys.exit(f"[manage_recipients] 配置不存在: {CONFIG_FILE}")
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"_backup_email_config_{stamp}.json")
    shutil.copy2(CONFIG_FILE, backup)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return backup


def normalize(addr: str) -> str:
    return addr.strip().lower()


def bad_format(addr: str) -> bool:
    return "@" not in addr or "." not in addr.split("@")[-1]


def show(recipients: list) -> None:
    print(f"[manage_recipients] 当前收件人 {len(recipients)} 个：")
    for i, r in enumerate(recipients, 1):
        print(f"  {i:>2}. {r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="《每日情报》收件人管理")
    ap.add_argument("action", choices=["list", "add", "remove", "check"])
    ap.add_argument("emails", nargs="*", help="邮箱地址，可多个（add/remove）或一个（check）")
    ap.add_argument("--dry-run", action="store_true", help="只演示，不写文件")
    args = ap.parse_args()

    cfg = load_config()
    recipients = list(cfg.get("recipients", []))

    if args.action == "list":
        show(recipients)
        return 0

    if not args.emails:
        sys.exit("[manage_recipients] 该动作需要至少一个邮箱地址")

    bad = [e for e in args.emails if bad_format(e)]
    if bad:
        sys.exit(f"[manage_recipients] 邮箱格式可疑: {bad}")

    if args.action == "check":
        want = normalize(args.emails[0])
        exists = any(normalize(r) == want for r in recipients)
        print(f"[manage_recipients] {args.emails[0]} -> {'已在名单' if exists else '不在名单'}")
        return 0 if exists else 1

    changed = []
    if args.action == "add":
        existing = {normalize(r) for r in recipients}
        for e in args.emails:
            if normalize(e) in existing:
                print(f"[manage_recipients] 跳过（已存在）: {e}")
                continue
            recipients.append(e.strip())
            existing.add(normalize(e))
            changed.append(f"+ {e.strip()}")
    else:  # remove
        drop = {normalize(e) for e in args.emails}
        kept = [r for r in recipients if normalize(r) not in drop]
        removed = [r for r in recipients if normalize(r) not in kept]
        recipients = kept
        changed = [f"- {r}" for r in removed]
        for e in args.emails:
            if normalize(e) not in {normalize(r) for r in removed}:
                print(f"[manage_recipients] 跳过（本就不在名单）: {e}")

    if not changed:
        print("[manage_recipients] 名单无变化")
        show(recipients)
        return 0

    print("[manage_recipients] 变更：")
    for c in changed:
        print(f"  {c}")

    if args.dry_run:
        print("[manage_recipients] DRY-RUN，未写入")
        show(recipients)
        return 0

    cfg["recipients"] = recipients
    backup = save_config(cfg)
    print(f"[manage_recipients] 已写入配置；备份 -> {backup}")
    show(recipients)
    return 0


if __name__ == "__main__":
    sys.exit(main())
