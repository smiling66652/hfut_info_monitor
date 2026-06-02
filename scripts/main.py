"""
主控制脚本 - 合工大信息监控系统
功能：整合所有模块，提供统一入口
"""

import json
import logging
from datetime import datetime
import time
import sys
import os

# 添加scripts目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 配置日志
# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'monitor.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('HFUT_Monitor')

# 加载配置
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.json')

try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print(f"❌ 配置文件不存在：{CONFIG_PATH}")
    print("   请先运行 setup.py 或手动创建配置文件")
    sys.exit(1)

# 导入自定义模块（延迟导入，避免依赖问题）
def import_modules():
    """延迟导入模块，处理缺失依赖"""
    modules = {}
    
    try:
        from filter import filter_and_extract, is_important
        modules['filter'] = {
            'filter_and_extract': filter_and_extract,
            'is_important': is_important
        }
        logger.info("✅ filter 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  filter 模块加载失败：{e}")
        modules['filter'] = None
    
    try:
        from push import push_immediate, push_daily_digest, push_test
        modules['push'] = {
            'push_immediate': push_immediate,
            'push_daily_digest': push_daily_digest,
            'push_test': push_test
        }
        logger.info("✅ push 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  push 模块加载失败：{e}")
        modules['push'] = None
    
    # 监控模块（可选）
    try:
        from wechat_monitor import monitor_wechat
        modules['wechat'] = monitor_wechat
        logger.info("✅ wechat_monitor 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  wechat_monitor 模块加载失败：{e}")
        modules['wechat'] = None
    
    try:
        from qzone_monitor import monitor_qzone
        modules['qzone'] = monitor_qzone
        logger.info("✅ qzone_monitor 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  qzone_monitor 模块加载失败：{e}")
        modules['qzone'] = None
    
    try:
        from ai_secretary import run_secretary, generate_daily_digest
        modules['ai'] = {
            'run_secretary': run_secretary,
            'generate_daily_digest': generate_daily_digest
        }
        logger.info("✅ ai_secretary 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  ai_secretary 模块加载失败：{e}")
        modules['ai'] = None
    
    return modules

def test_configuration():
    """测试配置"""
    print("=" * 60)
    print("合工大信息监控系统 - 配置测试")
    print("=" * 60)
    print()
    
    # 检查配置文件
    print("📋 配置文件：")
    print(f"  路径：{CONFIG_PATH}")
    
    # 检查监控目标
    print()
    print("📋 监控目标：")
    print(f"  QQ空间：{len(CONFIG['qq_accounts'])} 个账号")
    for qq in CONFIG['qq_accounts']:
        print(f"    - {qq}")
    
    print(f"  微信公众号：{len(CONFIG['wechat_accounts'])} 个")
    for account in CONFIG['wechat_accounts']:
        print(f"    - {account}")
    
    # 检查推送配置
    print()
    print("📋 推送配置：")
    push_method = CONFIG['push_method']
    print(f"  方式：{push_method['smtp_server']}")
    print(f"  发件人：{push_method['sender_email']}")
    print(f"  收件人：{push_method['receiver_email']}")
    
    if push_method['sender_password'] == "你的邮箱授权码":
        print("  ⚠️  邮箱授权码未配置！")
    
    print()
    print("=" * 60)

def run_monitor():
    """运行监控（主函数）"""
    logger.info("=" * 60)
    logger.info("合工大信息监控系统 - 启动")
    logger.info("=" * 60)
    logger.info(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    
    # 导入模块
    modules = import_modules()
    
    # 运行AI私人秘书（主处理模块）
    if modules['ai']:
        logger.info("🤖 运行AI私人秘书...")
        try:
            modules['ai']['run_secretary']()
        except Exception as e:
            logger.error(f"❌ AI私人秘书运行失败：{e}")
    
    # 可选：运行微信公众号监控
    if modules['wechat']:
        logger.info("📱 运行微信公众号监控...")
        try:
            modules['wechat']()
        except Exception as e:
            logger.error(f"❌ 微信公众号监控失败：{e}")
    
    # 可选：运行QQ空间监控
    if modules['qzone']:
        logger.info("💬 运行QQ空间监控...")
        try:
            modules['qzone']()
        except Exception as e:
            logger.error(f"❌ QQ空间监控失败：{e}")
    
    # 生成每日摘要
    if modules['ai']:
        logger.info("📊 生成每日摘要...")
        try:
            digest = modules['ai']['generate_daily_digest']()
            if digest:
                logger.info("✅ 每日摘要已生成并推送")
            else:
                logger.info("📭 今天没有新信息")
        except Exception as e:
            logger.error(f"❌ 生成每日摘要失败：{e}")
    
    logger.info("")
    logger.info(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

def setup_scheduler():
    """配置定时任务（需要安装 apscheduler）"""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
        
        scheduler = BlockingScheduler()
        
        # 每天早上8点运行监控
        scheduler.add_job(
            run_monitor,
            CronTrigger(hour=8, minute=0),
            id='morning_monitor',
            name='早晨监控'
        )
        
        # 每天晚上8点生成摘要
        scheduler.add_job(
            generate_daily_digest_if_needed,
            CronTrigger(hour=20, minute=0),
            id='evening_digest',
            name='晚间摘要'
        )
        
        logger.info("📅 定时任务已配置")
        logger.info("  早晨监控：每天 08:00")
        logger.info("  晚间摘要：每天 20:00")
        logger.info("  按 Ctrl+C 停止")
        
        scheduler.start()
    
    except ImportError:
        logger.warning("⚠️  apscheduler 未安装，无法使用定时任务")
        logger.warning("   请运行：pip install apscheduler")
        logger.warning("   或手动配置 Windows 任务计划程序")
        
        # 回退到单次运行
        logger.info("🔄 回退到单次运行模式")
        run_monitor()

def generate_daily_digest_if_needed():
    """如果需要，生成每日摘要"""
    modules = import_modules()
    
    if modules['ai']:
        try:
            digest = modules['ai']['generate_daily_digest']()
            return digest
        except Exception as e:
            logger.error(f"❌ 生成每日摘要失败：{e}")
            return None
    return None

def main():
    """主函数"""
    # 解析命令行参数
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == 'test':
            # 测试配置
            test_configuration()
        
        elif command == 'push-test':
            # 测试推送
            print("📧 测试QQ邮箱推送...")
            modules = import_modules()
            if modules['push']:
                result = modules['push']['push_test']()
                if result:
                    print("✅ 测试邮件发送成功，请查收QQ邮箱")
                else:
                    print("❌ 测试邮件发送失败，请检查配置")
            else:
                print("❌ push 模块未加载")
        
        elif command == 'run':
            # 单次运行
            run_monitor()
        
        elif command == 'scheduler':
            # 定时运行
            setup_scheduler()
        
        elif command == 'help':
            # 显示帮助
            show_help()
        
        else:
            print(f"❌ 未知命令：{command}")
            show_help()
    
    else:
        # 默认：显示帮助
        show_help()

def show_help():
    """显示帮助信息"""
    print("=" * 60)
    print("合工大信息监控系统 - 使用帮助")
    print("=" * 60)
    print()
    print("用法：")
    print("  python main.py [command]")
    print()
    print("命令：")
    print("  test        - 测试配置")
    print("  push-test   - 测试QQ邮箱推送")
    print("  run         - 单次运行监控")
    print("  scheduler   - 定时运行（需要 apscheduler）")
    print("  help        - 显示此帮助")
    print()
    print("示例：")
    print("  python main.py test")
    print("  python main.py push-test")
    print("  python main.py run")
    print()
    print("=" * 60)

if __name__ == '__main__':
    main()
