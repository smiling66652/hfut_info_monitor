"""
统一归档模块
功能：统一管理信息归档存储，解决重复代码和并发写入问题
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# 平台兼容：fcntl 仅 Unix 可用，Windows 使用备用方案
try:
    import fcntl
    HAS_FCNTL = True
except (ImportError, ModuleNotFoundError):
    HAS_FCNTL = False

from config_loader import get_json_path, get_db_path, get_log_dir

logger = logging.getLogger('Archive')

# 归档日志单独记录
_archive_logger = logging.getLogger('Archive.FileOps')


class ArchiveManager:
    """
    统一归档管理器
    - 解决三处 save_to_archive 重复定义问题
    - 使用文件锁防止并发写入冲突
    - 支持自动时间戳补充
    - 统一日志记录
    """
    
    def __init__(self):
        self._json_path = get_json_path()
        self._db_path = get_db_path()
    
    def save(self, info: dict) -> bool:
        """
        保存单条信息到归档
        
        Args:
            info: 信息字典，必须包含基本字段
            
        Returns:
            bool: 是否保存成功
        """
        if not info:
            logger.warning("尝试保存空信息，跳过")
            return False
        
        # 自动补充时间戳（如果没有）
        if not info.get('archive_time'):
            info['archive_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 确保数据目录存在
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # 使用文件锁防止并发写入（Windows 兼容）
            self._write_json_line(info)
            _archive_logger.debug(f"信息已归档：{info.get('title', 'N/A')[:30]}")
            return True
        except (IOError, OSError) as e:
            logger.error(f"归档写入失败：{e}")
            return False
        except (TypeError, ValueError) as e:
            logger.error(f"归档数据序列化失败：{e}，数据：{str(info)[:100]}")
            return False
    
    def _write_json_line(self, info: dict) -> None:
        """安全写入一行 JSON（带锁）"""
        if HAS_FCNTL:
            # Unix: 使用文件锁
            try:
                with open(self._json_path, 'a', encoding='utf-8') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    f.write(json.dumps(info, ensure_ascii=False))
                    f.write('\n')
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception as e:
                logger.debug(f"文件锁操作失败，回退到普通写入：{e}")
                with open(self._json_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(info, ensure_ascii=False))
                    f.write('\n')
        else:
            # Windows / 无 fcntl: 普通写入（单进程场景下安全）
            with open(self._json_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(info, ensure_ascii=False))
                f.write('\n')
    
    def read_today(self) -> list[dict]:
        """
        读取当天的归档信息
        兼容多种时间格式：%Y-%m-%d 和 %Y-%m-%d %H:%M:%S
        
        Returns:
            list[dict]: 当天的信息列表
        """
        today = datetime.now().strftime('%Y-%m-%d')
        
        if not self._json_path.exists():
            return []
        
        infos = []
        try:
            with open(self._json_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        info = json.loads(line)
                        # 兼容多种时间格式
                        info_time = info.get('time', '') or info.get('archive_time', '')
                        
                        # 匹配日期（无论是否包含时间部分）
                        if info_time and info_time.startswith(today):
                            infos.append(info)
                    except json.JSONDecodeError as e:
                        _archive_logger.warning(f"第 {line_num} 行 JSON 解析失败：{e}")
                        continue
        except IOError as e:
            logger.error(f"读取归档文件失败：{e}")
        
        return infos
    
    def read_by_date(self, date: str) -> list[dict]:
        """
        读取指定日期的归档信息
        兼容多种时间格式：%Y-%m-%d 和 %Y-%m-%d %H:%M:%S
        
        Args:
            date: 日期字符串，格式为'%Y-%m-%d'
            
        Returns:
            list[dict]: 指定日期的信息列表
        """
        if not self._json_path.exists():
            return []
        
        infos = []
        try:
            with open(self._json_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        info = json.loads(line)
                        # 兼容多种时间格式
                        info_time = info.get('time', '') or info.get('archive_time', '')
                        
                        # 匹配日期（无论是否包含时间部分）
                        if info_time and info_time.startswith(date):
                            infos.append(info)
                    except json.JSONDecodeError as e:
                        _archive_logger.warning(f"第 {line_num} 行 JSON 解析失败：{e}")
                        continue
        except IOError as e:
            logger.error(f"读取归档文件失败：{e}")
        
        return infos
    
    def read_all(self) -> list[dict]:
        """读取所有归档信息"""
        if not self._json_path.exists():
            return []
        
        infos = []
        try:
            with open(self._json_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            infos.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except IOError as e:
            logger.error(f"读取归档文件失败：{e}")
        
        return infos


# 全局单例
_archive_manager: Optional[ArchiveManager] = None


def get_archive() -> ArchiveManager:
    """获取归档管理器单例"""
    global _archive_manager
    if _archive_manager is None:
        _archive_manager = ArchiveManager()
    return _archive_manager


# 向后兼容的便捷函数
def save_to_archive(info: dict) -> bool:
    """便捷函数：保存信息到归档（向后兼容）"""
    return get_archive().save(info)
