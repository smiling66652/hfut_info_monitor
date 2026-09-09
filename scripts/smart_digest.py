"""
智能邮件汇总系统 - 超级秘书v2.0
功能：
1. 一天只发一封汇总邮件
2. 多层重要性判定：关键词 + 本地AI + 云端AI
3. 遵循"本地算力优先"原则
"""

import json
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import os
import sys

# 添加scripts目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config_loader import load_config
from archive import get_archive, save_to_archive
from push import push_daily_digest, push_immediate

logger = logging.getLogger('SmartDigest')

class SmartDigestSystem:
    """智能邮件汇总系统"""
    
    def __init__(self):
        self.config = load_config()
        self.ai_config = self.config.get('ai_secretary', {})
        
        # 重要性判定规则
        self.keywords = self.ai_config.get('filter_rules', {}).get('important_keywords', [])
        self.push_strategy = self.ai_config.get('push_strategy', {})
        
        # Ollama配置
        self.ollama_url = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
        self.ollama_model = self.config.get('ollama', {}).get('model', 'qwen2:7b')
        
        # 邮件发送记录（防止一天多次发送）
        self.last_digest_date = None
        self.digest_sent_today = False
        
    def should_send_digest(self) -> bool:
        """
        判断是否应该发送今日汇总邮件
        规则：一天只发一次
        """
        today = datetime.now().date()
        
        # 如果今天已经发送过，不再发送
        if self.digest_sent_today and self.last_digest_date == today:
            logger.info(f"📭 今日汇总邮件已发送（{today}），跳过")
            return False
        
        # 检查是否到了发送时间（默认晚上8点）
        send_time = self.push_strategy.get('digest_send_time', '20:00')
        current_time = datetime.now().strftime('%H:%M')
        
        if current_time >= send_time:
            return True
        else:
            logger.info(f"⏰ 未到发送时间（当前:{current_time}，设定:{send_time}），等待...")
            return False
    
    def mark_digest_sent(self):
        """标记今日汇总已发送"""
        self.last_digest_date = datetime.now().date()
        self.digest_sent_today = True
        logger.info(f"✅ 已标记今日汇总邮件已发送：{self.last_digest_date}")
    
    def analyze_with_keywords(self, text: str) -> dict:
        """
        第一层：关键词匹配（最快，本地执行）
        返回：{'score': 0-10, 'matched_keywords': [...]}
        """
        score = 0
        matched_keywords = []
        
        # 高优先级关键词（+5分）
        high_priority = ['保研', '夏令营', '预推免', '复试', '面试', '录取', '报名截止']
        # 中优先级关键词（+3分）
        medium_priority = ['通知', '公告', '重要', '紧急', '成绩', '排名']
        # 低优先级关键词（+1分）
        low_priority = ['活动', '比赛', '讲座', '招聘', '就业']
        
        for kw in high_priority:
            if kw in text:
                score += 5
                matched_keywords.append(kw)
        
        for kw in medium_priority:
            if kw in text:
                score += 3
                matched_keywords.append(kw)
        
        for kw in low_priority:
            if kw in text:
                score += 1
                matched_keywords.append(kw)
        
        return {
            'score': min(score, 10),  # 最高10分
            'matched_keywords': matched_keywords,
            'layer': 'keyword'
        }
    
    def analyze_with_local_ai(self, text: str) -> dict:
        """
        第二层：本地Ollama分析（中等算力，Y7000P执行）
        返回：{'score': 0-10, 'category': '', 'reason': ''}
        """
        try:
            response = requests.post(
                f'{self.ollama_url}/api/generate',
                json={
                    'model': self.ollama_model,
                    'prompt': f"""你是合工大机械工程学生，正在准备保研（目标：电气/计算机方向）。
                    
请分析以下信息对你的保研/学习是否重要，给出0-10的评分，并简要说明理由。

信息内容：
{text[:500]}

评分标准：
- 10分：保研直接相关（夏令营、预推免、复试、录取等）
- 7-9分：学术相关（竞赛、科研项目、学术论文等）
- 4-6分：课程/考试相关
- 1-3分：校园活动/其他
- 0分：无关信息

请严格输出以下JSON格式（不要有任何其他内容）：
{{
  "score": 8,
  "category": "保研信息",
  "reason": "包含夏令营报名信息，与保研直接相关"
}}""",
                    'stream': False,
                    'format': 'json',
                    'options': {
                        'temperature': 0.3,  # 降低随机性
                        'num_predict': 200    # 限制输出长度
                    }
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = json.loads(response.json().get('response', '{}'))
                result['layer'] = 'local_ai'
                return result
            else:
                logger.warning(f"⚠️ Ollama分析失败：{response.status_code}")
                return {'score': 0, 'category': '未知', 'reason': 'Ollama调用失败', 'layer': 'local_ai'}
        
        except requests.exceptions.ConnectionError:
            logger.warning("⚠️ Ollama未启动，跳过本地AI分析")
            return {'score': 0, 'category': '未知', 'reason': 'Ollama未连接', 'layer': 'local_ai'}
        except Exception as e:
            logger.warning(f"⚠️ Ollama分析异常：{e}")
            return {'score': 0, 'category': '未知', 'reason': str(e), 'layer': 'local_ai'}
    
    def analyze_with_cloud_ai(self, text: str, user_profile: dict) -> dict:
        """
        第三层：云端WorkBuddy AI分析（高算力，深度分析）
        只有在前两层得分较高时才调用（节省成本）
        """
        # TODO: 调用WorkBuddy内置AI进行分析
        # 这里需要先检查是否有WorkBuddy的API接口
        logger.info("☁️ 调用云端AI进行深度分析...")
        
        # 模拟返回（实际应该调用WorkBuddy API）
        return {
            'score': 0,
            'category': '未知',
            'reason': '云端AI尚未实现',
            'layer': 'cloud_ai',
            'detail_analysis': ''
        }
    
    def comprehensive_analysis(self, text: str, source: str = '未知') -> dict:
        """
        综合分析：三层评估
        1. 关键词匹配（快速筛选）
        2. 本地AI分析（中等算力）
        3. 云端AI分析（仅高分内容）
        """
        logger.info(f"🔍 开始综合分析：{text[:30]}... (来源:{source})")
        
        # 第一层：关键词匹配
        keyword_result = self.analyze_with_keywords(text)
        logger.info(f"  [第一层] 关键词匹配得分：{keyword_result['score']}/10，匹配关键词：{keyword_result['matched_keywords']}")
        
        # 如果关键词得分>=4，进行第二层分析
        if keyword_result['score'] >= 4:
            local_ai_result = self.analyze_with_local_ai(text)
            logger.info(f"  [第二层] 本地AI分析得分：{local_ai_result.get('score', 0)}/10，分类：{local_ai_result.get('category', '未知')}")
            
            # 综合得分（关键词权重40% + 本地AI权重60%）
            final_score = keyword_result['score'] * 0.4 + local_ai_result.get('score', 0) * 0.6
            
            # 如果综合得分>=7，进行第三层分析（云端AI）
            if final_score >= 7:
                # TODO: 获取用户画像
                user_profile = {
                    'name': '同学',
                    'major': '机械工程',
                    'target': '保研电气/计算机'
                }
                cloud_ai_result = self.analyze_with_cloud_ai(text, user_profile)
                logger.info(f"  [第三层] 云端AI分析完成")
                
                return {
                    'final_score': final_score,
                    'keyword_score': keyword_result['score'],
                    'local_ai_score': local_ai_result.get('score', 0),
                    'cloud_ai_score': cloud_ai_result.get('score', 0),
                    'category': local_ai_result.get('category', '未知'),
                    'reason': local_ai_result.get('reason', ''),
                    'matched_keywords': keyword_result['matched_keywords'],
                    'need_cloud_analysis': True
                }
            else:
                return {
                    'final_score': final_score,
                    'keyword_score': keyword_result['score'],
                    'local_ai_score': local_ai_result.get('score', 0),
                    'cloud_ai_score': 0,
                    'category': local_ai_result.get('category', '未知'),
                    'reason': local_ai_result.get('reason', ''),
                    'matched_keywords': keyword_result['matched_keywords'],
                    'need_cloud_analysis': False
                }
        else:
            # 关键词得分<4，直接判定为不重要
            logger.info(f"  [跳过] 关键词得分过低（{keyword_result['score']}/10），跳过后续分析")
            return {
                'final_score': keyword_result['score'],
                'keyword_score': keyword_result['score'],
                'local_ai_score': 0,
                'cloud_ai_score': 0,
                'category': '普通信息',
                'reason': '关键词匹配度低',
                'matched_keywords': keyword_result['matched_keywords'],
                'need_cloud_analysis': False
            }
    
    def generate_daily_digest(self, date: str = None) -> Optional[dict]:
        """
        生成每日汇总邮件内容
        :param date: 日期字符串（格式：YYYY-MM-DD），默认为今天
        :return: 汇总邮件内容字典
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        logger.info(f"📊 生成每日汇总：{date}")
        
        # 读取当天所有信息
        archive = get_archive()
        all_infos = archive.read_by_date(date)
        
        if not all_infos:
            logger.info(f"📭 {date} 没有收集到信息")
            return None
        
        logger.info(f"📊 共收集到 {len(all_infos)} 条信息，开始综合分析...")
        
        # 对所有信息进行综合分析
        analyzed_infos = []
        important_infos = []
        
        for info in all_infos:
            text = info.get('content', info.get('text', ''))
            source = info.get('source', '未知')
            
            # 综合分析
            analysis = self.comprehensive_analysis(text, source)
            
            # 合并信息和分析结果
            merged_info = {**info, **analysis}
            analyzed_infos.append(merged_info)
            
            # 筛选重要内容（final_score >= 6）
            if analysis['final_score'] >= 6:
                important_infos.append(merged_info)
        
        logger.info(f"✅ 综合分析完成：重要信息 {len(important_infos)}/{len(all_infos)} 条")
        
        # 按重要性排序
        important_infos.sort(key=lambda x: x['final_score'], reverse=True)
        
        # 生成邮件内容
        digest = {
            'date': date,
            'total_count': len(all_infos),
            'important_count': len(important_infos),
            'important_infos': important_infos[:10],  # 最多显示10条
            'all_analyzed': analyzed_infos
        }
        
        return digest
    
    def send_daily_digest_if_needed(self) -> bool:
        """
        判断是否发送每日汇总，并发送
        :return: 是否成功发送
        """
        if not self.should_send_digest():
            return False
        
        # 生成汇总内容
        digest = self.generate_daily_digest()
        
        if digest is None:
            logger.info("📭 今日无信息，不发送汇总邮件")
            return False
        
        if digest['important_count'] == 0:
            logger.info("📭 今日无重要信息，不发送汇总邮件")
            return False
        
        # 发送邮件
        logger.info(f"📧 准备发送每日汇总邮件（重要信息 {digest['important_count']} 条）...")
        success = push_daily_digest(digest)
        
        if success:
            self.mark_digest_sent()
            logger.info("✅ 每日汇总邮件发送成功")
            return True
        else:
            logger.error("❌ 每日汇总邮件发送失败")
            return False


def run_smart_digest():
    """运行智能邮件汇总系统（主函数）"""
    logger.info("=" * 60)
    logger.info("智能邮件汇总系统 - 超级秘书v2.0")
    logger.info("=" * 60)
    
    system = SmartDigestSystem()
    
    # 检查是否应该发送汇总
    if system.should_send_digest():
        system.send_daily_digest_if_needed()
    else:
        logger.info("⏰ 未到发送时间或今日已发送，退出")
    
    logger.info("=" * 60)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    run_smart_digest()
