#!/usr/bin/env python3
"""
测试脚本 - 验证邮件发送功能
"""
import sys
import os
from datetime import datetime

# 添加scripts目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("测试邮件发送功能")
print("=" * 60)

# 测试1: 测试 push_daily_digest 函数
print("\n[测试1/2] 测试 push_daily_digest 函数...")
try:
    from push import push_daily_digest
    
    # 创建测试数据（正确格式：信息列表）
    test_infos = [
        {
            'title': '测试邮件功能',
            'content': '这是一封测试邮件，验证邮件发送功能是否正常。',
            'source': 'test',
            'url': 'https://example.com',
            'time': datetime.now().strftime('%Y-%m-%d'),
            'keywords': ['测试', '邮件']
        },
        {
            'title': '合工大保研通知',
            'content': '关于2026年保研工作的通知...',
            'source': 'hfut_web',
            'url': 'https://hfut.edu.cn/notice/123',
            'time': datetime.now().strftime('%Y-%m-%d'),
            'keywords': ['保研', '通知']
        }
    ]
    
    print(f"✅ 模块加载成功")
    print(f"   测试数据准备完成，共 {len(test_infos)} 条信息")
    print(f"\n开始发送测试邮件...")
    
    # 调用函数（传入信息列表）
    result = push_daily_digest(test_infos)
    
    if result:
        print("\n✅ 测试邮件发送成功！")
        print(f"   收件人: <测试QQ号>@qq.com")
    else:
        print("\n❌ 测试邮件发送失败")
        
except Exception as e:
    print(f"❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试2: 测试 smart_digest 模块
print("\n[测试2/2] 测试 smart_digest 模块...")
try:
    from smart_digest import SmartDigestSystem
    print("✅ 模块加载成功")
    print("   智能邮件汇总系统已就绪")
except Exception as e:
    print(f"❌ 模块加载失败: {e}")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
