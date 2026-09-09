"""
统一数据同步模块 - 将采集的信息同步到Notion和IMA知识库
功能：
1. 将采集的信息同步到Notion数据库
2. 将采集的信息同步到IMA知识库
3. 支持批量同步和增量同步
4. 错误重试和本地缓存
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger('DataSync')


# 添加scripts目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config_loader import load_config
from archive import get_archive


class DataSyncManager:
    """统一数据同步管理器"""
    
    def __init__(self):
        self.config = load_config()
        self.notion_config = self.config.get('notion', {})
        self.ima_config = self.config.get('ima', {})
        
        # 初始化Notion管理器
        self.notion_manager = None
        self._init_notion()
        
        # 初始化IMA管理器
        self.ima_manager = None
        self._init_ima()
        
        # 同步状态跟踪
        self.sync_state_file = Path(__file__).parent.parent / "data" / "sync_state.json"
        self.sync_state = self._load_sync_state()
        
    def _init_notion(self):
        """初始化Notion管理器"""
        try:
            from notion_manager import NotionManager
            api_key = self.notion_config.get('api_key', '')
            database_id = self.notion_config.get('database_id', '')
            
            if api_key and database_id:
                self.notion_manager = NotionManager(api_key, database_id)
                logger.info("✅ Notion管理器初始化成功")
            else:
                logger.warning("⚠️ Notion配置不完整，跳过初始化")
        except ImportError as e:
            logger.warning(f"⚠️ Notion模块导入失败: {e}")
        except Exception as e:
            logger.error(f"❌ Notion管理器初始化失败: {e}")
    
    def _init_ima(self):
        """初始化IMA管理器"""
        try:
            from knowledge_base import KnowledgeBaseManager
            kb_id = self.ima_config.get('kb_id', '')
            
            if kb_id:
                self.ima_manager = KnowledgeBaseManager(kb_id)
                logger.info("✅ IMA管理器初始化成功")
            else:
                logger.warning("⚠️ IMA配置不完整，跳过初始化")
        except ImportError as e:
            logger.warning(f"⚠️ IMA模块导入失败: {e}")
        except Exception as e:
            logger.error(f"❌ IMA管理器初始化失败: {e}")
    
    def _load_sync_state(self) -> dict:
        """加载同步状态"""
        if self.sync_state_file.exists():
            try:
                with open(self.sync_state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ 加载同步状态失败: {e}")
        
        return {
            'last_notion_sync': None,
            'last_ima_sync': None,
            'synced_items': []  # 已同步的项目ID列表
        }
    
    def _save_sync_state(self):
        """保存同步状态"""
        try:
            self.sync_state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.sync_state_file, 'w', encoding='utf-8') as f:
                json.dump(self.sync_state, f, ensure_ascii=False, indent=2)
            logger.debug("✅ 同步状态已保存")
        except Exception as e:
            logger.error(f"❌ 保存同步状态失败: {e}")
    
    def sync_to_notion(self, items: List[Dict], force: bool = False) -> int:
        """
        将信息同步到Notion
        
        Args:
            items: 信息列表
            force: 是否强制同步（忽略已同步记录）
            
        Returns:
            成功同步的数量
        """
        if not self.notion_manager:
            logger.error("❌ Notion管理器未初始化，跳过同步")
            return 0
        
        logger.info(f"📤 开始同步到Notion：{len(items)} 条信息")
        
        # 检查Notion连接
        is_ok, msg = self.notion_manager.health_check()
        if not is_ok:
            logger.error(f"❌ Notion连接失败: {msg}")
            return 0
        
        success_count = 0
        synced_ids = self.sync_state.get('synced_items', [])
        
        for i, item in enumerate(items, 1):
            try:
                # 生成唯一ID（用于去重）
                item_id = self._generate_item_id(item)
                
                # 检查是否已同步
                if not force and item_id in synced_ids:
                    logger.debug(f"  [{i}/{len(items)}] 已同步，跳过: {item.get('title', '')[:30]}")
                    continue
                
                # 构建Notion页面属性
                properties = self._build_notion_properties(item)
                
                # 创建Notion页面
                page_id = self.notion_manager.add_page(properties)
                
                if page_id:
                    logger.info(f"  [{i}/{len(items)}] ✅ 已同步到Notion: {item.get('title', '')[:40]}")
                    success_count += 1
                    
                    # 记录已同步
                    synced_ids.append(item_id)
                    
                    # 定期保存同步状态（每10条保存一次）
                    if success_count % 10 == 0:
                        self.sync_state['synced_items'] = synced_ids
                        self.sync_state['last_notion_sync'] = datetime.now().isoformat()
                        self._save_sync_state()
                else:
                    logger.warning(f"  [{i}/{len(items)}] ⚠️ 同步到Notion失败: {item.get('title', '')[:30]}")
                
            except Exception as e:
                logger.error(f"  [{i}/{len(items)}] ❌ 同步到Notion异常: {e}")
                continue
            
            # 友好API调用（避免触发限流）
            time.sleep(0.5)
        
        # 最终保存同步状态
        self.sync_state['synced_items'] = synced_ids
        self.sync_state['last_notion_sync'] = datetime.now().isoformat()
        self._save_sync_state()
        
        logger.info(f"✅ Notion同步完成：成功 {success_count}/{len(items)} 条")
        return success_count
    
    def sync_to_ima(self, items: List[Dict], force: bool = False) -> int:
        """
        将信息同步到IMA知识库
        
        Args:
            items: 信息列表
            force: 是否强制同步（忽略已同步记录）
            
        Returns:
            成功同步的数量
        """
        if not self.ima_manager:
            logger.error("❌ IMA管理器未初始化，跳过同步")
            return 0
        
        logger.info(f"📤 开始同步到IMA知识库：{len(items)} 条信息")
        
        # 检查IMA连接
        if not self.ima_manager.health_check():
            logger.error("❌ IMA连接失败")
            return 0
        
        success_count = 0
        synced_ids = self.sync_state.get('synced_items', [])
        
        for i, item in enumerate(items, 1):
            try:
                # 生成唯一ID（用于去重）
                item_id = self._generate_item_id(item)
                
                # 检查是否已同步
                if not force and item_id in synced_ids:
                    logger.debug(f"  [{i}/{len(items)}] 已同步，跳过: {item.get('title', '')[:30]}")
                    continue
                
                # 优先使用URL导入（如果有的话）
                url = item.get('url', '')
                title = item.get('title', '未命名')
                content = item.get('content', '')
                
                if url and url.startswith('http'):
                    # 方法1：通过URL导入
                    if self.ima_manager.add_from_url(url, title):
                        logger.info(f"  [{i}/{len(items)}] ✅ 已同步到IMA（URL）: {title[:40]}")
                        success_count += 1
                        synced_ids.append(item_id)
                    else:
                        logger.warning(f"  [{i}/{len(items)}] ⚠️ URL导入失败，尝试文本内容: {title[:30]}")
                        # 降级：尝试添加文本内容
                        if content and self.ima_manager.add_text_note(title, content):
                            logger.info(f"  [{i}/{len(items)}] ✅ 已同步到IMA（文本）: {title[:40]}")
                            success_count += 1
                            synced_ids.append(item_id)
                elif content:
                    # 方法2：添加文本内容
                    if self.ima_manager.add_text_note(title, content):
                        logger.info(f"  [{i}/{len(items)}] ✅ 已同步到IMA（文本）: {title[:40]}")
                        success_count += 1
                        synced_ids.append(item_id)
                    else:
                        logger.warning(f"  [{i}/{len(items)}] ⚠️ 文本添加失败: {title[:30]}")
                else:
                    logger.warning(f"  [{i}/{len(items)}] ⚠️ 无URL和文本内容，跳过: {title[:30]}")
                
            except Exception as e:
                logger.error(f"  [{i}/{len(items)}] ❌ 同步到IMA异常: {e}")
                continue
            
            # 友好API调用
            time.sleep(1)
        
        # 保存同步状态
        self.sync_state['synced_items'] = synced_ids
        self.sync_state['last_ima_sync'] = datetime.now().isoformat()
        self._save_sync_state()
        
        logger.info(f"✅ IMA同步完成：成功 {success_count}/{len(items)} 条")
        return success_count
    
    def _generate_item_id(self, item: Dict) -> str:
        """
        生成项目的唯一ID（用于去重）
        
        Args:
            item: 信息字典
            
        Returns:
            唯一ID字符串
        """
        # 优先使用URL作为唯一ID
        url = item.get('url', '')
        if url:
            return f"url:{url}"
        
        # 否则使用标题+来源的哈希
        title = item.get('title', '')
        source = item.get('source', '')
        content = item.get('content', '')
        
        # 简单哈希（实际使用可以用hashlib.md5）
        import hashlib
        hash_input = f"{title}|{source}|{content[:100]}"
        hash_value = hashlib.md5(hash_input.encode('utf-8')).hexdigest()
        
        return f"hash:{hash_value}"
    
    def _build_notion_properties(self, item: Dict) -> Dict:
        """
        构建Notion页面属性
        
        Args:
            item: 信息字典
            
        Returns:
            Notion properties字典
        """
        properties = {}
        
        # 标题 → Name（title类型）
        title = item.get('title', '') or item.get('name', '') or '未命名'
        properties['Name'] = {
            'title': [{'text': {'content': str(title)}}]
        }
        
        # 来源 → 来源（select类型）
        source = item.get('source', '') or item.get('来源', '')
        if source:
            properties['来源'] = {
                'select': {'name': str(source)}
            }
        
        # 时间 → 日期（date类型）
        time_val = item.get('time', '') or item.get('日期', '') or item.get('date', '')
        if time_val:
            # 尝试标准化日期格式
            try:
                from notion_manager import NotionManager
                date_str = NotionManager._normalize_date(str(time_val))
                properties['日期'] = {
                    'date': {'start': date_str}
                }
            except:
                pass
        
        # 内容 → 内容（rich_text类型）
        content = item.get('content', '') or item.get('内容', '')
        if content:
            truncated = str(content)[:2000]  # Notion限制
            properties['内容'] = {
                'rich_text': [{'text': {'content': truncated}}]
            }
        
        # URL → 链接（url类型）
        url = item.get('url', '') or item.get('链接', '')
        if url:
            properties['链接'] = {'url': str(url)}
        
        # 分类 → 分类（select类型）
        category = item.get('category', '') or item.get('分类', '')
        if category:
            properties['分类'] = {
                'select': {'name': str(category)}
            }
        
        return properties
    
    def sync_today_data(self, date: str = None, sync_notion: bool = True, sync_ima: bool = True) -> Dict:
        """
        同步今天采集的数据到Notion和IMA
        
        Args:
            date: 日期字符串（格式：YYYY-MM-DD），默认为今天
            sync_notion: 是否同步到Notion
            sync_ima: 是否同步到IMA
            
        Returns:
            同步结果字典
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        logger.info(f"📊 开始同步 {date} 的数据...")
        
        # 读取当天的数据
        archive = get_archive()
        items = archive.read_by_date(date)
        
        if not items:
            logger.info(f"📭 {date} 没有数据需要同步")
            return {'notion': 0, 'ima': 0, 'total': 0}
        
        logger.info(f"📊 共 {len(items)} 条数据需要同步")
        
        result = {'total': len(items), 'notion': 0, 'ima': 0}
        
        # 同步到Notion
        if sync_notion and self.notion_manager:
            result['notion'] = self.sync_to_notion(items)
        
        # 同步到IMA
        if sync_ima and self.ima_manager:
            result['ima'] = self.sync_to_ima(items)
        
        logger.info(f"✅ 数据同步完成：Notion {result['notion']} 条，IMA {result['ima']} 条")
        
        return result
    
    def retry_failed_sync(self) -> Dict:
        """
        重试失败的同步任务（从本地缓存）
        
        Returns:
            重试结果字典
        """
        logger.info("🔄 开始重试失败的同步任务...")
        
        result = {'notion': 0, 'ima': 0}
        
        # 重试Notion同步（如果有缓存机制）
        # ...
        
        # 重试IMA同步
        if self.ima_manager:
            ima_result = self.ima_manager.retry_pending()
            result['ima'] = ima_result.get('url_success', 0)
        
        logger.info(f"✅ 重试完成：IMA {result['ima']} 条")
        
        return result


def run_daily_sync():
    """运行每日同步任务（主函数）"""
    logger.info("=" * 60)
    logger.info("每日数据同步任务")
    logger.info("=" * 60)
    
    sync_manager = DataSyncManager()
    
    # 同步今天的数据
    result = sync_manager.sync_today_data()
    
    # 重试失败的同步
    retry_result = sync_manager.retry_failed_sync()
    
    logger.info("=" * 60)
    logger.info(f"同步结果：今日同步 {result['total']} 条，Notion {result['notion']} 条，IMA {result['ima']} 条")
    logger.info(f"重试结果：IMA {retry_result['ima']} 条")
    logger.info("=" * 60)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    run_daily_sync()
