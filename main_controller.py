"""
合工大官网巡检系统 v4.0 - 统一主控脚本
功能整合：
1. 合工大官网巡检
2. QQ群消息提取
3. QQ动态提取
4. 微信公众号信息提取（新增）
5. 智能邮件汇总（一天只发一封）
6. 数据同步到Notion和IMA
7. 统一任务编排（启动/关闭所有服务）

使用方式：
python main_controller.py --mode all    # 运行完整流程
python main_controller.py --mode collect  # 只采集数据
python main_controller.py --mode analyze  # 只分析数据
python main_controller.py --mode digest   # 只生成汇总邮件
python main_controller.py --mode sync     # 只同步到知识库
python main_controller.py --mode status   # 查看系统状态
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('MainController')

# 添加scripts目录到路径
sys.path.append(str(Path(__file__).parent / "scripts"))

from task_orchestrator import TaskOrchestrator


def setup_logging(log_level=logging.INFO):
    """配置日志"""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / f"main_controller_{datetime.now().strftime('%Y-%m-%d')}.log"
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def run_all_mode(orchestrator: TaskOrchestrator):
    """
    运行完整流程模式
    """
    logger.info("=" * 60)
    logger.info("模式：完整流程")
    logger.info("=" * 60)
    
    success = orchestrator.run_full_task()
    
    if success:
        logger.info("✅ 完整流程执行成功")
    else:
        logger.error("❌ 完整流程执行失败")

    # === 新模块：增强检测 + v2 摘要 ===
    logger.info("\n[增强模块] 运行增强检测 + v2 每日摘要...")
    try:
        from enhanced_detector import EnhancedDetector
        from daily_digest_v2 import DailyDigestV2
        from archive import get_archive

        archive = get_archive()
        today_messages = archive.read_today()

        if today_messages:
            detector = EnhancedDetector()
            categorized = detector.detect_and_categorize(today_messages)

            digest = DailyDigestV2()
            report = digest.generate(categorized)
            digest.send(report)

            status = detector.get_status()
            logger.info("   增强检测器状态: %s", status)
            logger.info("✅ 增强检测 + v2 摘要完成")
        else:
            logger.info("   今日无归档消息，跳过增强检测")
    except ImportError as e:
        logger.warning("⚠️ 增强模块导入失败（非致命）: %s", e)
    except Exception as e:
        logger.warning("⚠️ 增强检测失败（非致命）: %s", e)

    # === QQ空间好友动态监控 ===
    logger.info("\n[QQ空间] 采集好友动态 Feed...")
    try:
        import asyncio
        from qzone_friend_feed import QzoneFriendFeedOrchestrator

        orchestrator_feed = QzoneFriendFeedOrchestrator()
        feed_result = asyncio.run(orchestrator_feed.collect(count=30))

        if feed_result['success']:
            logger.info("✅ QQ空间好友动态: 获取 %d 条 (方案: %s)", feed_result['total'], feed_result['plan'])

            # 用增强检测器分析好友动态
            if feed_result['data']:
                try:
                    from enhanced_detector import EnhancedDetector
                    detector = EnhancedDetector()
                    feed_messages = [{
                        'content': item.get('content', ''),
                        'source': item.get('source_type', 'QQ空间动态'),
                        'author': item.get('author_name', ''),
                        'time': item.get('publish_time', ''),
                    } for item in feed_result['data']]
                    categorized_feed = detector.detect_and_categorize(feed_messages)

                    competition_count = sum(1 for c in categorized_feed if c.get('category') == 'competition')
                    secondhand_count = sum(1 for c in categorized_feed if c.get('category') == 'secondhand')
                    useful_count = sum(1 for c in categorized_feed if c.get('category') == 'useful_info')
                    logger.info("   🏆 竞赛 %d | 🛒 二手 %d | 📢 有用信息 %d", competition_count, secondhand_count, useful_count)
                except Exception as e:
                    logger.warning("   好友动态分析跳过: %s", e)
        else:
            logger.warning("⚠️ QQ空间好友动态采集失败 (方案链: %s): %s",
                          ' → '.join(feed_result.get('plan_history', [])), feed_result.get('message', ''))
    except ImportError as e:
        logger.warning("⚠️ qzone_friend_feed 模块导入失败（非致命）: %s", e)
    except Exception as e:
        logger.warning("⚠️ 好友动态采集失败（非致命）: %s", e)
    
    return success


def run_collect_mode(orchestrator: TaskOrchestrator):
    """
    只运行数据采集模式
    """
    logger.info("=" * 60)
    logger.info("模式：数据采集")
    logger.info("=" * 60)
    
    try:
        orchestrator._run_data_collection()
        logger.info("✅ 数据采集完成")
        return True
    except Exception as e:
        logger.error(f"❌ 数据采集失败: {e}")
        return False


def run_analyze_mode(orchestrator: TaskOrchestrator):
    """
    只运行AI分析模式
    """
    logger.info("=" * 60)
    logger.info("模式：AI分析")
    logger.info("=" * 60)
    
    try:
        orchestrator._run_ai_analysis()
        logger.info("✅ AI分析完成")
        return True
    except Exception as e:
        logger.error(f"❌ AI分析失败: {e}")
        return False


def run_digest_mode(orchestrator: TaskOrchestrator):
    """
    只运行邮件汇总模式
    """
    logger.info("=" * 60)
    logger.info("模式：邮件汇总")
    logger.info("=" * 60)
    
    try:
        orchestrator._run_digest_generation()
        logger.info("✅ 邮件汇总完成")
        return True
    except Exception as e:
        logger.error(f"❌ 邮件汇总失败: {e}")
        return False


def run_sync_mode(orchestrator: TaskOrchestrator):
    """
    只运行数据同步模式
    """
    logger.info("=" * 60)
    logger.info("模式：数据同步")
    logger.info("=" * 60)
    
    try:
        orchestrator._sync_to_knowledge_base()
        logger.info("✅ 数据同步完成")
        return True
    except Exception as e:
        logger.error(f"❌ 数据同步失败: {e}")
        return False


def run_status_mode(orchestrator: TaskOrchestrator):
    """
    查看系统状态模式
    """
    logger.info("=" * 60)
    logger.info("模式：系统状态")
    logger.info("=" * 60)
    
    # 检查服务状态
    print("\n📊 服务状态检查")
    print("-" * 60)
    
    ollama_status = "✅ 运行中" if orchestrator.check_ollama() else "❌ 未运行"
    napcat_status = "✅ 运行中" if orchestrator.check_napcat() else "❌ 未运行"
    
    print(f"Ollama: {ollama_status}")
    print(f"NapCat: {napcat_status}")
    
    # 检查模块状态
    print("\n📦 模块状态检查")
    print("-" * 60)
    
    modules = [
        ("hfut_web_scraper", "合工大官网巡检"),
        ("qq_group_monitor", "QQ群消息提取"),
        ("qzone_monitor", "QQ动态提取"),
        ("wechat_account_monitor", "微信公众号提取"),
        ("smart_digest", "智能邮件汇总"),
        ("data_sync", "数据同步"),
        ("enhanced_detector", "增强型智能检测器"),
        ("daily_digest_v2", "v2每日摘要"),
        ("qzone_friend_feed", "QQ空间好友动态"),
        ("playwright_scraper", "Playwright抓取器"),
    ]
    
    for module_name, module_desc in modules:
        try:
            __import__(module_name)
            print(f"{module_desc}: ✅ 可用")
        except ImportError:
            print(f"{module_desc}: ❌ 不可用")
    
    # 检查配置
    print("\n⚙️ 配置状态检查")
    print("-" * 60)
    
    try:
        from config_loader import load_config
        config = load_config()
        
        # 检查关键配置
        has_notion = bool(config.get('notion', {}).get('api_key'))
        has_ima = bool(config.get('ima', {}).get('kb_id'))
        # 邮件配置可能在 push_method 或 email 段
        email_cfg = config.get('push_method', config.get('email', {}))
        has_email = bool(email_cfg.get('sender_password') or email_cfg.get('smtp_password'))
        
        print(f"Notion配置: {'✅ 已配置' if has_notion else '❌ 未配置'}")
        print(f"IMA配置: {'✅ 已配置' if has_ima else '❌ 未配置'}")
        print(f"邮件配置: {'✅ 已配置' if has_email else '❌ 未配置'}")
        
    except Exception as e:
        print(f"配置检查失败: {e}")
    
    # 增强模块状态
    print("\n🚀 增强模块状态检查")
    print("-" * 60)
    
    try:
        from enhanced_detector import EnhancedDetector
        detector = EnhancedDetector()
        status = detector.get_status()
        print(f"增强型智能检测器: ✅ 可用")
        for name, info in status.items():
            print(f"  - {name}: {info}")
    except ImportError:
        print(f"增强型智能检测器: ❌ 不可用")
    
    try:
        from daily_digest_v2 import DailyDigestV2
        digest = DailyDigestV2()
        print(f"v2每日摘要: ✅ 可用 (top_n={digest.top_n_per_category}, push_time={digest.push_time})")
    except ImportError:
        print(f"v2每日摘要: ❌ 不可用")
    
    try:
        from playwright_scraper import PlaywrightScraper
        scraper = PlaywrightScraper()
        pw_status = "✅ Playwright模式" if scraper.is_available else "⚠️ 降级模式(requests)"
        print(f"Playwright抓取器: {pw_status}")
    except ImportError:
        print(f"Playwright抓取器: ❌ 不可用")
    
    try:
        from filter import get_category, is_competition, is_secondhand
        print(f"过滤器v4.1: ✅ 可用 (含竞赛/二手/分类)")
    except ImportError:
        print(f"过滤器v4.1: ❌ 不可用")
    
    print("\n" + "=" * 60)
    
    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='合工大官网巡检系统 v4.0 - 统一主控脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python main_controller.py --mode all       # 运行完整流程
  python main_controller.py --mode collect   # 只采集数据
  python main_controller.py --mode analyze  # 只分析数据
  python main_controller.py --mode digest   # 只生成汇总邮件
  python main_controller.py --mode sync     # 只同步到知识库
  python main_controller.py --mode status   # 查看系统状态
  python main_controller.py --mode services # 启动所有服务
  python main_controller.py --mode stop     # 停止所有服务
        """
    )
    
    parser.add_argument(
        '--mode', '-m',
        choices=['all', 'collect', 'analyze', 'digest', 'sync', 'status', 'services', 'stop'],
        default='all',
        help='运行模式'
    )
    
    parser.add_argument(
        '--config', '-c',
        default=None,
        help='配置文件路径'
    )
    
    parser.add_argument(
        '--log-level', '-l',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='日志级别'
    )
    
    args = parser.parse_args()
    
    # 配置日志
    log_level = getattr(logging, args.log_level)
    setup_logging(log_level)
    
    logger.info("合工大官网巡检系统 v4.0 启动")
    logger.info(f"运行模式: {args.mode}")
    
    # 创建任务编排器
    orchestrator = TaskOrchestrator(config_path=args.config)
    
    # 根据模式执行
    start_time = time.time()
    success = False
    
    if args.mode == 'all':
        success = run_all_mode(orchestrator)
    elif args.mode == 'collect':
        success = run_collect_mode(orchestrator)
    elif args.mode == 'analyze':
        success = run_analyze_mode(orchestrator)
    elif args.mode == 'digest':
        success = run_digest_mode(orchestrator)
    elif args.mode == 'sync':
        success = run_sync_mode(orchestrator)
    elif args.mode == 'status':
        success = run_status_mode(orchestrator)
    elif args.mode == 'services':
        success = orchestrator.start_all_services()
    elif args.mode == 'stop':
        orchestrator.stop_all_services()
        success = True
    
    elapsed_time = time.time() - start_time
    
    logger.info("=" * 60)
    logger.info(f"任务执行完成，耗时 {elapsed_time:.1f} 秒")
    logger.info("=" * 60)
    
    # 返回退出码
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
