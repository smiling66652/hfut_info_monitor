"""
配置加载模块 - 统一管理项目路径和配置
"""

import json
import os
from pathlib import Path

def get_base_dir():
    """获取项目根目录"""
    # 当前文件在 scripts/ 目录下，所以父目录的父目录是项目根目录
    return Path(__file__).parent.parent

def get_config_path():
    """获取配置文件路径"""
    return get_base_dir() / 'config' / 'config.json'

def get_data_dir():
    """获取数据目录路径"""
    return get_base_dir() / 'data'

def get_log_dir():
    """获取日志目录路径"""
    return get_base_dir() / 'logs'

def load_config():
    """加载配置文件"""
    config_path = get_config_path()
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_db_path():
    """获取数据库文件路径"""
    return get_data_dir() / 'hfut_archive.db'

def get_json_path():
    """获取JSON归档文件路径"""
    return get_data_dir() / 'hfut_archive.json'

# 确保目录存在
os.makedirs(get_data_dir(), exist_ok=True)
os.makedirs(get_log_dir(), exist_ok=True)
