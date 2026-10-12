#!/usr/bin/env python3
"""
hfut_notify.py - 多渠道推送模块（基于 apprise）
=================================================
与既有 hfut_email.py 并存，不替换、不修改其任何逻辑。

目的：把推送渠道从"仅 QQ 邮箱 SMTP"扩展到 200+ 渠道，且不需要自己写适配。

支持的国内渠道（apprise scheme）：
  dingtalk://  钉钉        wecombot://  企业微信机器人
  wechat://    微信        wxpusher://  WxPusher
  lark://      飞书        feishu://    飞书
  pushdeer://  PushDeer    pushplus://  PushPlus
  bark://      Bark(iOS)   chanify://   Chanify
  spugpush://  Spug        ntfy://      ntfy
  mailto://    邮件        （以及 200+ 其它）

配置文件 hfut_notify_config.json（含凭据，须加入 .gitignore）：
{
  "urls": [
    "wecombot://<bot_key>",
    "pushdeer://<pushkey>",
    "mailto://<邮箱>:<授权码>@smtp.qq.com"
  ],
  "enabled": true
}

用法：
  from hfut_notify import push
  push("标题", "**正文**支持 markdown")

  # 命令行自测
  python hfut_notify.py --test
  python hfut_notify.py --list-schemes
"""

import json
import os
import sys

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "hfut_notify_config.json")

# 项目中常用的国内渠道，用于配置向导展示
COMMON_SCHEMES = [
    ("wecombot", "企业微信机器人", "wecombot://<bot_key>"),
    ("dingtalk", "钉钉", "dingtalk://<APIKEY>/<手机号>"),
    ("pushdeer", "PushDeer", "pushdeer://<pushkey>"),
    ("pushplus", "PushPlus", "pushplus://<token>"),
    ("wxpusher", "WxPusher", "wxpusher://<appToken>/<uid>"),
    ("lark", "飞书", "lark://<webhook_key>"),
    ("bark", "Bark (iOS)", "bark://<device_key>"),
    ("mailto", "邮件", "mailto://<邮箱>:<授权码>@smtp.qq.com"),
]


def _load_apprise():
    """延迟导入 apprise，缺失时给出明确指引而不是崩栈"""
    try:
        import apprise
        return apprise
    except ImportError:
        print("[notify] ❌ 未安装 apprise，请执行：pip install apprise")
        return None


def load_config(path=CONFIG_PATH):
    """读取配置，返回 dict。文件不存在时返回空配置。"""
    if not os.path.isfile(path):
        return {"urls": [], "enabled": True}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[notify] ⚠ 配置文件解析失败：{e}")
        return {"urls": [], "enabled": True}

    if not isinstance(cfg.get("urls"), list):
        cfg["urls"] = []
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    """写回配置（用于向导）"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[notify] ✅ 配置已保存：{path}")


def push(title, body, urls=None, body_format="markdown", config_path=CONFIG_PATH):
    """推送到所有已配置渠道。

    Args:
        title: 消息标题
        body: 消息正文（默认按 markdown 解析）
        urls: 覆盖配置文件中的渠道列表；None 表示读配置
        body_format: text / markdown / html
        config_path: 配置文件路径

    Returns:
        bool: 是否至少一个渠道发送成功
    """
    apprise = _load_apprise()
    if apprise is None:
        return False

    cfg = load_config(config_path)
    if not cfg.get("enabled", True):
        print("[notify] 推送已在配置中关闭（enabled=false），跳过")
        return False

    target_urls = urls if urls is not None else cfg.get("urls", [])
    if not target_urls:
        print("[notify] 未配置任何推送渠道，跳过（参考 hfut_notify_config.example.json）")
        return False

    a = apprise.Apprise()
    added, skipped = 0, []
    for u in target_urls:
        if a.add(u):
            added += 1
        else:
            skipped.append(u)

    if skipped:
        # 只打印 scheme 部分，避免把密钥写进日志
        safe = [u.split("://")[0] + "://***" for u in skipped]
        print(f"[notify] ⚠ 无效渠道已跳过：{', '.join(safe)}")

    if not added:
        print("[notify] ❌ 没有可用渠道")
        return False

    ok = a.notify(title=title, body=body, body_format=body_format)
    print(f"[notify] {'✅ 推送成功' if ok else '❌ 推送失败'}（{added} 个渠道）")
    return bool(ok)


def list_schemes(filter_keyword=""):
    """列出 apprise 支持的全部 URL scheme，可按关键词过滤。"""
    apprise = _load_apprise()
    if apprise is None:
        return []
    from apprise.plugins import N_MGR
    N_MGR.load_modules()
    schemas = sorted(N_MGR._schema_map.keys())
    if filter_keyword:
        schemas = [s for s in schemas if filter_keyword.lower() in s.lower()]
    return schemas


def setup_wizard():
    """交互式配置向导：逐条录入推送渠道 URL"""
    print("=" * 56)
    print("  hfut_notify 推送渠道配置向导")
    print("=" * 56)
    print("\n常用渠道写法：")
    for _, name, example in COMMON_SCHEMES:
        print(f"  {name:<14} {example}")

    cfg = load_config()
    urls = cfg.get("urls", [])

    print(f"\n当前已配置 {len(urls)} 个渠道。")
    print("逐条粘贴渠道 URL（直接回车结束）：\n")

    while True:
        try:
            u = input("  渠道 URL > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not u:
            break
        urls.append(u)
        print(f"    已添加（共 {len(urls)} 个）")

    if not urls:
        print("\n未添加任何渠道，退出。")
        return

    cfg["urls"] = urls
    cfg["enabled"] = True
    save_config(cfg)

    print("\n发送测试消息…")
    push("hfut 巡检 · 配置测试",
         "**推送渠道配置成功**\n\n- 如果你看到这条消息，说明配置生效\n- 后续巡检结果将通过这些渠道送达")


def main():
    args = sys.argv[1:]

    if "--list-schemes" in args:
        kw = ""
        if "--filter" in args:
            i = args.index("--filter")
            if i + 1 < len(args):
                kw = args[i + 1]
        schemas = list_schemes(kw)
        print(f"apprise 支持的 scheme 共 {len(schemas)} 个")
        if kw:
            print(f"（过滤词：{kw}）")
        for s in schemas:
            print("  ", s)
        return 0

    if "--test" in args:
        return 0 if push("hfut 巡检 · 测试",
                         "这是一条测试消息。\n\n- 渠道连通性正常\n- Markdown 渲染正常") else 1

    if "--wizard" in args or "--setup" in args:
        setup_wizard()
        return 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
