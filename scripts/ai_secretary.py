"""
AI私人秘书模块
功能：智能分析信息，生成摘要，制定推送策略
"""

import json
import requests
from datetime import datetime
import time
import sys
import os

# 添加scripts目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入配置加载模块
from config_loader import load_config, get_json_path, get_db_path

# 加载配置
CONFIG = load_config()

AI_STRATEGY = CONFIG['ai_secretary']['push_strategy']
IMPORTANT_KEYWORDS = CONFIG['ai_secretary']['filter_rules']['important_keywords']

# 导入自定义模块
from filter import filter_and_extract, is_important
from push import push_immediate, push_daily_digest

def analyze_with_rules(text):
    """
    使用规则分析信息（轻量级，适合MateBook E）
    :param text: 待分析文本
    :return: 分析结果字典
    """
    result = {
        'is_important': False,
        'category': '普通信息',
        'summary': text[:100],  # 简单截取前100字符
        'keywords': []
    }
    
    # 规则1：关键词判断重要性
    for keyword in IMPORTANT_KEYWORDS:
        if keyword in text:
            result['is_important'] = True
            result['keywords'].append(keyword)
    
    # 规则2：简单分类
    if '通知' in text or '公告' in text:
        result['category'] = '通知公告'
    elif '活动' in text or '比赛' in text:
        result['category'] = '活动比赛'
    elif '保研' in text or '考研' in text or '成绩' in text:
        result['category'] = '学术信息'
    elif '招聘' in text or '就业' in text:
        result['category'] = '招聘就业'
    
    # 规则3：生成简单摘要（取前200字符）
    if len(text) > 200:
        result['summary'] = text[:200] + '...'
    
    return result

def analyze_with_llm(text, use_local=True):
    """
    使用LLM分析信息（需要算力，适合Y7000P）
    :param text: 待分析文本
    :param use_local: 是否使用本地LLM（Ollama）
    :return: 分析结果字典
    """
    if use_local:
        # 使用本地Ollama
        return analyze_with_ollama(text)
    else:
        # 使用云端API（GPT/Claude）
        return analyze_with_cloud_api(text)

def analyze_with_ollama(text):
    """
    使用本地Ollama分析
    :param text: 待分析文本
    :return: 分析结果字典
    """
    try:
        # 调用Ollama API
        response = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model': 'qwen2:7b',
                'prompt': f"""分析以下信息，输出JSON格式结果：
                
信息内容：
{text[:500]}

请输出以下JSON（不要有任何其他内容）：
{{
  "is_important": true/false,
  "category": "通知公告/活动比赛/学术信息/招聘就业/普通信息",
  "summary": "100字摘要",
  "keywords": ["关键词1", "关键词2"]
}}""",
                'stream': False,
                'format': 'json'
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()['response']
            return json.loads(result)
        else:
            print(f"❌ Ollama分析失败：{response.status_code}")
            return analyze_with_rules(text)  # 降级到规则分析
    
    except Exception as e:
        print(f"❌ Ollama调用失败：{e}")
        print("⚠️  降级到规则分析")
        return analyze_with_rules(text)

def analyze_with_cloud_api(text):
    """
    使用云端API分析（需要配置API Key）
    :param text: 待分析文本
    :return: 分析结果字典
    """
    # TODO：实现云端API调用
    # 这里需要提供API Key
    print("⚠️  云端API分析尚未实现，使用规则分析")
    return analyze_with_rules(text)

def process_info(text, source='未知', time='未知'):
    """
    处理单条信息
    :param text: 信息文本
    :param source: 信息来源
    :param time: 发布时间
    :return: 处理结果字典
    """
    print(f"📊 分析信息：{text[:30]}...")
    
    # Step 1：过滤垃圾信息
    is_pass, info = filter_and_extract(text)
    if not is_pass:
        print("🗑️  过滤垃圾信息")
        return None
    
    # Step 2：使用规则分析（MateBook E可用）
    analysis = analyze_with_rules(text)
    
    # Step 3：合并信息
    result = {
        'title': info['title'],
        'content': text,
        'source': source,
        'time': time,
        'is_important': analysis['is_important'],
        'category': analysis['category'],
        'summary': analysis['summary'],
        'keywords': analysis['keywords']
    }
    
    # Step 4：推送策略
    if analysis['is_important'] and AI_STRATEGY['immediate_push']:
        print("🚨 即时推送重要信息")
        push_immediate(result)
    else:
        print("📝 归档普通信息")
        save_to_archive(result)
    
    return result

def save_to_archive(info):
    """
    保存信息到归档
    :param info: 信息字典
    """
    from config_loader import get_json_path, get_db_path
    
    # 保存到JSON文件
    with open(get_json_path(), 'a', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False)
        f.write('\n')
    
    # 同时保存到数据库（可选）
    # import sqlite3
    # conn = sqlite3.connect(get_db_path())
    # ...

def generate_daily_digest():
    """
    生成每日摘要
    :return: 摘要内容
    """
    from config_loader import get_json_path
    
    print(f"📊 生成每日摘要：{datetime.now().strftime('%Y-%m-%d')}")
    
    # 读取当天归档的信息
    try:
        with open(get_json_path(), 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        infos = []
        for line in lines:
            try:
                info = json.loads(line.strip())
                # 只取当天的信息
                if info['time'] == datetime.now().strftime('%Y-%m-%d'):
                    infos.append(info)
            except:
                continue
        
        if not infos:
            print("📭 今天没有新信息")
            return None
        
        # 生成摘要
        digest = f"""
合工大信息监控系统 - 每日摘要

日期：{datetime.now().strftime('%Y年%m月%d日')}
共收集到 {len(infos)} 条有用信息

============================================================
"""
        
        for i, info in enumerate(infos, 1):
            digest += f"""
【信息 {i}】
分类：{info['category']}
标题：{info['title']}
来源：{info['source']}
时间：{info['time']}
关键词：{', '.join(info['keywords'])}

摘要：
{info['summary']}

------------------------------------------------------------
"""
        
        digest += """
============================================================

此邮件由AI私人秘书自动生成
"""
        
        # 推送每日摘要
        if AI_STRATEGY['daily_digest']:
            push_daily_digest(infos)
        
        return digest
    
    except FileNotFoundError:
        print("📭 归档文件不存在")
        return None

def run_secretary():
    """
    AI私人秘书主函数
    """
    print("=" * 60)
    print("合工大信息监控系统 - AI私人秘书")
    print("=" * 60)
    print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 这里应该调用监控模块获取新信息
    # 示例：
    # from wechat_monitor import monitor_wechat
    # new_infos = monitor_wechat()
    
    # 模拟处理信息
    test_infos = [
        {
            'text': '【通知】明天上午9点开会',
            'source': '微信公众号',
            'time': datetime.now().strftime('%Y-%m-%d')
        },
        {
            'text': '关于2026年保研工作的通知',
            'source': 'QQ空间',
            'time': datetime.now().strftime('%Y-%m-%d')
        }
    ]
    
    processed = []
    for info in test_infos:
        result = process_info(info['text'], info['source'], info['time'])
        if result:
            processed.append(result)
    
    # 生成每日摘要
    if processed and AI_STRATEGY['daily_digest']:
        print()
        generate_daily_digest()
    
    print()
    print(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

if __name__ == '__main__':
    # 测试
    print("🧪 测试AI私人秘书模块...")
    print()
    
    # 运行私人秘书
    run_secretary()
    
    print()
    print("✅ 测试完成")
    print()
    print("⚠️  注意：")
    print("  1. 需要配置监控模块才能获取实际信息")
    print("  2. 需要配置QQ邮箱才能推送")
    print("  3. 在Y7000P上可启用本地LLM分析（Ollama）")
