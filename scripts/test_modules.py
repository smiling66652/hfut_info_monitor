#!/usr/bin/env python3
"""
测试脚本 - 验证所有模块功能
"""
import sys
import os

# 添加scripts目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("合工大官网巡检系统 v4.0 - 模块测试")
print("=" * 60)

# 测试1: wechat_account_monitor
print("\n[测试1/5] wechat_account_monitor 模块...")
try:
    from wechat_account_monitor import _load_config, WECHAT_ACCOUNTS
    accounts = _load_config()
    print(f"✅ 模块加载成功")
    print(f"   配置了 {len(accounts)} 个公众号:")
    for i, acc in enumerate(accounts, 1):
        if isinstance(acc, dict):
            print(f"   {i}. {acc.get('name', '未知')} (优先级:{acc.get('priority', '未知')})")
        else:
            print(f"   {i}. {acc} (类型错误: {type(acc)})")
except Exception as e:
    print(f"❌ 模块加载失败: {e}")
    import traceback
    traceback.print_exc()

# 测试2: smart_digest
print("\n[测试2/5] smart_digest 模块...")
try:
    from smart_digest import SmartDigestSystem
    print("✅ 模块加载成功")
except Exception as e:
    print(f"❌ 模块加载失败: {e}")

# 测试3: task_orchestrator
print("\n[测试3/5] task_orchestrator 模块...")
try:
    from task_orchestrator import TaskOrchestrator
    print("✅ 模块加载成功")
except Exception as e:
    print(f"❌ 模块加载失败: {e}")

# 测试4: data_sync
print("\n[测试4/5] data_sync 模块...")
try:
    from data_sync import DataSyncManager
    print("✅ 模块加载成功")
except Exception as e:
    print(f"❌ 模块加载失败: {e}")

# 测试5: config_loader
print("\n[测试5/5] config_loader 模块...")
try:
    from config_loader import load_config
    config = load_config()
    print("✅ 模块加载成功")
    print(f"   配置加载成功，包含 {len(config)} 个顶级配置项")
except Exception as e:
    print(f"❌ 模块加载失败: {e}")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
