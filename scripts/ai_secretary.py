"""
AI私人秘书模块
功能：智能分析信息，生成摘要，制定推送策略

修复：
- 使用统一的 archive 模块（消除重复的 save_to_archive）
- generate_daily_digest 兼容多种时间格式
- 统一使用 logging
- run_secretary 不再硬编码测试数据（改为从监控模块获取）
"""

import json
import requests
from datetime import datetime
import time
import sys
import os
import logging
from typing import Optional

# 添加 scripts 目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入配置加载模块
from config_loader import load_config, get_json_path

# 加载配置
CONFIG = load_config()

AI_STRATEGY = CONFIG.get('ai_secretary', {}).get('push_strategy', {})
IMPORTANT_KEYWORDS = CONFIG.get('ai_secretary', {}).get('filter_rules', {}).get('important_keywords', [])

# 导入自定义模块
from filter import filter_and_extract, is_important
from push import push_immediate, push_daily_digest
from archive import save_to_archive, get_archive

logger = logging.getLogger('AISecretary')


def analyze_with_rules(text: str) -> dict:
    """
    使用规则分析信息（轻量级，适合MateBook E）
    :param text: 待分析文本
    :return: 分析结果字典
    """
    result = {
        'is_important': False,
        'category': '普通信息',
        'summary': text[:100],
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
    
    # 规则3：生成简单摘要
    if len(text) > 200:
        result['summary'] = text[:200] + '...'
    
    return result


def analyze_with_llm(text: str, use_local=True) -> dict:
    """
    使用LLM分析信息（需要算力，适合Y7000P）
    :param text: 待分析文本
    :param use_local: 是否使用本地LLM（Ollama）
    :return: 分析结果字典
    """
    if use_local:
        return analyze_with_ollama(text)
    else:
        return analyze_with_cloud_api(text)


def analyze_with_ollama(text: str) -> dict:
    """
    使用本地Ollama分析
    :param text: 待分析文本
    :return: 分析结果字典
    """
    try:
        ollama_url = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
        response = requests.post(
            f'{ollama_url}/api/generate',
            json={
                'model': CONFIG.get('ollama', {}).get('model', 'qwen2:7b'),
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
            result = response.json().get('response', '{}')
            return json.loads(result)
        else:
            logger.warning(f"❌ Ollama分析失败：{response.status_code}")
            return analyze_with_rules(text)
    
    except requests.exceptions.ConnectionError:
        logger.warning("⚠️ Ollama 未启动，降级到规则分析")
        return analyze_with_rules(text)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"⚠️ Ollama 返回格式错误：{e}，降级到规则分析")
        return analyze_with_rules(text)
    except Exception as e:
        logger.warning(f"❌ Ollama调用失败：{e}，降级到规则分析")
        return analyze_with_rules(text)


def analyze_with_cloud_api(text: str) -> dict:
    """
    使用云端API分析（需要配置API Key）
    :param text: 待分析文本
    :return: 分析结果字典
    """
    logger.info("⚠️ 云端API分析尚未实现，使用规则分析")
    return analyze_with_rules(text)


def process_info(text: str, source='未知', time='未知') -> Optional[dict]:
    """
    处理单条信息
    :param text: 信息文本
    :param source: 信息来源
    :param time: 发布时间
    :return: 处理结果字典
    """
    logger.info(f"📊 分析信息：{text[:30]}...")
    
    # Step 1：过滤垃圾信息
    is_pass, info = filter_and_extract(text)
    if not is_pass:
        logger.debug("🗑️ 过滤垃圾信息")
        return None
    
    # Step 2：使用规则分析（MateBook E可用）
    analysis = analyze_with_rules(text)
    
    # Step 3：合并信息
    result = {
        'title': info.get('title', ''),
        'content': text,
        'source': source,
        'time': time,
        'is_important': analysis.get('is_important', False),
        'category': analysis.get('category', '普通信息'),
        'summary': analysis.get('summary', ''),
        'keywords': analysis.get('keywords', [])
    }
    
    # Step 4：推送策略
    immediate_push = AI_STRATEGY.get('immediate_push', [])
    should_immediate = (
        result['is_important'] and 
        AI_STRATEGY.get('immediate_push_enabled', True) and
        any(kw in text for kw in immediate_push)
    )
    
    if should_immediate:
        logger.info("🚨 即时推送重要信息")
        push_immediate(result)
    else:
        logger.info("📝 归档普通信息")
        save_to_archive(result)
    
    return result


def generate_daily_digest() -> Optional[str]:
    """
    生成每日摘要（修复：兼容多种时间格式）
    
    🔴 关键修复：
    - 旧版只匹配精确的 '%Y-%m-%d' 格式
    - 新版使用 startswith 匹配，兼容 '%Y-%m-%d %H:%M:%S' 等格式
    - 使用 archive 模块的 read_today() 方法
    """
    logger.info(f"📊 生成每日摘要：{datetime.now().strftime('%Y-%m-%d')}")
    
    try:
        # 使用统一归档模块读取当天数据（已内置时间格式兼容）
        infos = get_archive().read_today()
        
        if not infos:
            logger.info("📭 今天没有新信息")
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
分类：{info.get('category', 'N/A')}
标题：{info.get('title', 'N/A')}
来源：{info.get('source', '未知')}
时间：{info.get('time', '未知')}
关键词：{', '.join(info.get('keywords', []))}

摘要：
{info.get('summary', 'N/A')}

------------------------------------------------------------
"""
        
        digest += """
============================================================

此邮件由AI私人秘书自动生成
"""
        
        # 推送每日摘要
        if AI_STRATEGY.get('daily_digest', False):
            push_daily_digest(infos)
        
        return digest
    
    except FileNotFoundError:
        logger.info("📭 归档文件不存在")
        return None
    except Exception as e:
        logger.error(f"❌ 生成每日摘要失败：{e}")
        return None


def run_secretary(infos=None):
    """
    AI私人秘书主函数
    
    Args:
        infos: 信息列表（可选）。如果为 None，尝试从各监控模块获取。
               不再硬编码测试数据！
    """
    logger.info("=" * 60)
    logger.info("合工大信息监控系统 - AI私人秘书")
    logger.info("=" * 60)
    logger.info(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    processed = []
    
    if infos:
        # 外部传入的信息列表（推荐方式）
        for info in infos:
            text = info.get('text', info.get('content', ''))
            source = info.get('source', '未知')
            time_str = info.get('time', datetime.now().strftime('%Y-%m-%d'))
            
            result = process_info(text, source, time_str)
            if result:
                processed.append(result)
    else:
        # 尝试自动从监控模块获取信息
        logger.info("ℹ️ 未传入信息列表，尝试从监控模块采集...")
        
        try:
            from wechat_monitor import monitor_wechat as collect_wechat
            from qzone_monitor import monitor_qzone as collect_qzone
            
            # 注意：monitor 函数内部会自行处理和归档
            # 这里只是触发采集
            collect_wechat()
            collect_qzone()
            
        except ImportError as e:
            logger.warning(f"监控模块不可用：{e}")
            logger.info("💡 提示：可以通过命令行参数 `run` 或 scheduler 自动触发完整流程")
        except Exception as e:
            logger.error(f"自动采集中出错：{e}")
    
    # 生成每日摘要
    if processed and AI_STRATEGY.get('daily_digest'):
        logger.info("")
        generate_daily_digest()
    
    logger.info(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    return processed


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger.info("🧪 测试AI私人秘书模块...")
    
    # 测试数据（仅用于 __main__ 独立测试）
    test_infos = [
        {'text': '【通知】明天上午9点开会', 'source': '微信公众号', 'time': datetime.now().strftime('%Y-%m-%d')},
        {'text': '关于2026年保研工作的通知', 'source': 'QQ空间', 'time': datetime.now().strftime('%Y-%m-%d')},
    ]
    
    processed = run_secretary(test_infos)
    
    logger.info(f"\n✅ 测试完成，处理了 {len(processed) if processed else 0} 条信息")
