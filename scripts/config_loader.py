"""
配置加载模块 - 统一管理项目路径和配置
支持环境变量覆盖敏感信息
"""

import json
import os
from pathlib import Path

# 尝试加载 python-dotenv（可选依赖）
try:
    from dotenv import load_dotenv
    _ENV_LOADED = load_dotenv(os.path.join(Path(__file__).parent.parent, '.env'))
except ImportError:
    _ENV_LOADED = False


def get_base_dir():
    """获取项目根目录"""
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


def _get_env(key: str, default: str = '') -> str:
    """安全获取环境变量"""
    return os.environ.get(key, default)


def load_config() -> dict:
    """
    加载配置文件，敏感字段优先从环境变量读取
    
    Returns:
        dict: 配置字典
        
    Raises:
        FileNotFoundError: 配置文件不存在
        json.JSONDecodeError: JSON 格式错误
    """
    config_path = get_config_path()
    
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 敏感信息从环境变量覆盖（如果已设置）
    _apply_env_overrides(config)
    
    return config


def _apply_env_overrides(config: dict) -> None:
    """用环境变量覆盖配置中的敏感信息"""
    # 邮箱密码
    env_pwd = _get_env('SENDER_PASSWORD')
    if env_pwd and config.get('push_method'):
        config['push_method']['sender_password'] = env_pwd
    
    # NapCat 配置
    napcat = config.get('napcat', {})
    if isinstance(napcat, dict):
        env_token = _get_env('NAPCAT_TOKEN')
        if env_token:
            napcat['token'] = env_token
        
        env_bot_qq = _get_env('NAPCAT_BOT_QQ')
        if env_bot_qq:
            napcat['bot_qq'] = env_bot_qq
        
        env_receiver = _get_env('NAPCAT_DEFAULT_RECEIVER')
        if env_receiver:
            napcat['default_receiver'] = env_receiver


def get_db_path():
    """获取数据库文件路径"""
    return get_data_dir() / 'hfut_archive.db'


def get_json_path():
    """获取JSON归档文件路径"""
    return get_data_dir() / 'hfut_archive.json'


# 确保目录存在
os.makedirs(get_data_dir(), exist_ok=True)
os.makedirs(get_log_dir(), exist_ok=True)
