"""
超级秘书 - 初始化脚本
检查环境并初始化数据库
"""

import sys
import json
import sqlite3
import requests
from pathlib import Path


def check_python():
    """检查Python版本"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"❌ Python版本过低: {version.major}.{version.minor}")
        print("   需要Python 3.8或更高版本")
        return False
    print(f"✅ Python版本: {version.major}.{version.minor}.{version.micro}")
    return True


def check_dependencies():
    """检查依赖包"""
    required = ['requests']
    missing = []

    for package in required:
        try:
            __import__(package)
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package} 未安装")
            missing.append(package)

    if missing:
        print(f"\n请运行: pip install {' '.join(missing)}")
        return False
    return True


def check_napcat():
    """检查NapCat连接"""
    print("\n检查NapCat连接...")

    config_path = Path(__file__).parent / 'config.json'
    if not config_path.exists():
        print("❌ 配置文件不存在")
        return False

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    napcat = config.get('napcat', {})
    api_url = napcat.get('api_url', 'http://localhost:3001')
    token = napcat.get('token', '')

    try:
        response = requests.get(
            f"{api_url}/get_login_info",
            headers={'Authorization': token},
            timeout=5
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'ok':
                print(f"✅ NapCat已连接")
                bot_qq = data.get('data', {}).get('user_id', '')
                print(f"   机器人QQ: {bot_qq}")
                return True
        print(f"❌ NapCat连接失败")
        print(f"   状态码: {response.status_code}")
        return False
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到NapCat")
        print("   请确保NapCat服务正在运行")
        print("   提示: 运行 NapCat_SystemQQ.bat 启动")
        return False
    except Exception as e:
        print(f"❌ NapCat连接异常: {e}")
        return False


def check_ollama():
    """检查Ollama连接"""
    print("\n检查Ollama连接...")

    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)

        if response.status_code == 200:
            models = response.json().get('models', [])
            print(f"✅ Ollama已连接")
            print(f"   可用模型数量: {len(models)}")

            if models:
                print("   模型列表:")
                for m in models[:5]:
                    print(f"     - {m.get('name')}")

            return True
        print(f"❌ Ollama连接失败")
        return False
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到Ollama")
        print("   请确保Ollama服务正在运行")
        print("   提示: 在Y7000P上运行 'ollama serve'")
        return False
    except Exception as e:
        print(f"❌ Ollama连接异常: {e}")
        return False


def init_database():
    """初始化数据库"""
    print("\n初始化数据库...")

    config_path = Path(__file__).parent / 'config.json'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    db_path = config.get('storage', {}).get('db_path',
        Path(__file__).parent / 'data' / 'secretary.db')

    # 创建目录
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # 创建表
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 群消息表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            group_name TEXT,
            sender_id TEXT,
            sender_name TEXT,
            content TEXT,
            message_id TEXT UNIQUE,
            timestamp INTEGER,
            message_type TEXT,
            raw_data TEXT,
            is_processed INTEGER DEFAULT 0,
            category TEXT,
            summary TEXT,
            importance TEXT,
            tags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 群文件表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            file_id TEXT UNIQUE,
            file_name TEXT,
            file_size INTEGER,
            file_url TEXT,
            sender_id TEXT,
            sender_name TEXT,
            download_path TEXT,
            is_downloaded INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 扫描进度表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scan_progress (
            group_id TEXT PRIMARY KEY,
            last_msg_id TEXT,
            last_scan_time INTEGER,
            total_messages INTEGER DEFAULT 0
        )
    ''')

    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_group_id ON group_messages(group_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON group_messages(timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON group_messages(category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_importance ON group_messages(importance)')

    conn.commit()
    conn.close()

    print(f"✅ 数据库初始化完成")
    print(f"   数据库路径: {db_path}")

    return True


def main():
    """主函数"""
    print("="*50)
    print("  超级秘书 - 环境检查")
    print("="*50)

    results = {
        'python': check_python(),
        'dependencies': check_dependencies(),
        'database': False
    }

    # 初始化数据库（不依赖外部服务）
    results['database'] = init_database()

    # 可选检查（需要外部服务）
    print("\n" + "-"*50)
    print("  可选服务检查")
    print("-"*50)

    results['napcat'] = check_napcat()
    results['ollama'] = check_ollama()

    # 总结
    print("\n" + "="*50)
    print("  检查结果总结")
    print("="*50)

    required_ok = results['python'] and results['dependencies'] and results['database']

    print(f"\n必要项:")
    print(f"  Python环境: {'✅' if results['python'] else '❌'}")
    print(f"  依赖包: {'✅' if results['dependencies'] else '❌'}")
    print(f"  数据库: {'✅' if results['database'] else '❌'}")

    print(f"\n可选服务:")
    print(f"  NapCat: {'✅ 已连接' if results.get('napcat') else '⚠️ 未连接'}")
    print(f"  Ollama: {'✅ 已连接' if results.get('ollama') else '⚠️ 未连接'}")

    print("\n" + "="*50)

    if required_ok:
        print("✅ 环境检查通过，可以开始使用超级秘书")
        print("\n下一步:")
        print("  1. 确保NapCat和Ollama已启动")
        print("  2. 运行 BuddySecretary.bat 进入交互模式")
        print("  3. 先运行 'fullscan' 全量扫描所有群消息")
    else:
        print("❌ 环境检查未通过，请修复上述问题")

    print("="*50)


if __name__ == '__main__':
    main()
