"""
超级秘书 - 主控制器
统一调度各模块，提供CLI界面
"""

import json
import sys
import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from qq_collector import QQGroupCollector
from ai_processor import MessageProcessor


class BuddySecretaryCLI:
    """超级秘书命令行界面"""

    def __init__(self, config_path: str):
        self.config_path = config_path

        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.collector = QQGroupCollector(config_path)
        self.processor = MessageProcessor(config_path)

        self.groups = self.config['qq_groups']

    def status(self) -> Dict:
        """查看系统状态"""
        print("\n" + "="*50)
        print("  超级秘书 - 系统状态")
        print("="*50)

        # NapCat状态
        napcat_ok = self.collector.check_connection()
        print(f"\n[NapCat] {'✅ 已连接' if napcat_ok else '❌ 未连接'}")

        # Ollama状态
        ollama_ok = self.processor.ollama.check_connection()
        print(f"[Ollama] {'✅ 已连接' if ollama_ok else '❌ 未连接'}")

        # 统计数据
        stats = self.collector.get_stats()
        print(f"\n[数据统计]")
        print(f"  总消息数: {stats['total_messages']}")
        print(f"  已处理: {stats['processed_messages']}")
        print(f"  总文件数: {stats['total_files']}")

        print(f"\n[群组监控]")
        print(f"  监控群数量: {len(self.groups)}")

        if stats['messages_by_group']:
            print(f"\n  消息分布 (前5):")
            for gid, cnt in list(stats['messages_by_group'].items())[:5]:
                print(f"    {gid}: {cnt} 条")

        return {
            'napcat': napcat_ok,
            'ollama': ollama_ok,
            'stats': stats
        }

    def full_scan(self, batch_size: int = 100, delay: float = 2.0) -> Dict:
        """
        全量扫描所有群

        Args:
            batch_size: 每批消息数
            delay: 批次间隔秒数
        """
        print("\n" + "="*50)
        print("  全量扫描模式")
        print("="*50)
        print(f"  批次大小: {batch_size}")
        print(f"  批次间隔: {delay}秒")
        print(f"  群组数量: {len(self.groups)}")
        print("="*50)

        start_time = time.time()
        results = {}

        for i, group_id in enumerate(self.groups, 1):
            print(f"\n[{i}/{len(self.groups)}] 扫描群 {group_id}...")

            group_info = self.collector.get_group_info(group_id)
            group_name = group_info.get('group_name', '未知') if group_info else '未知'
            print(f"    群名称: {group_name}")

            count = self.collector.full_scan_group(group_id, batch_size, delay)
            results[group_id] = count

            elapsed = time.time() - start_time
            print(f"    完成: {count} 条消息 (已用时: {elapsed:.1f}秒)")

        total = sum(results.values())
        elapsed = time.time() - start_time

        print("\n" + "="*50)
        print("  全量扫描完成")
        print("="*50)
        print(f"  总耗时: {elapsed:.1f}秒")
        print(f"  总消息: {total}条")
        print(f"  平均速度: {total/elapsed:.1f}条/秒" if elapsed > 0 else "")

        return results

    def incremental_scan(self, num: int = 20) -> Dict:
        """增量扫描所有群"""
        print("\n增量扫描模式...")

        results = {}
        for group_id in self.groups:
            count = self.collector.incremental_scan_group(group_id, num)
            results[group_id] = count
            if count > 0:
                print(f"  {group_id}: +{count}条")

        total = sum(results.values())
        print(f"\n本次新增: {total}条消息")

        return results

    def process_ai(self, limit: int = 50, delay: float = 1.0) -> int:
        """AI处理未处理消息"""
        print("\n" + "="*50)
        print("  AI信息处理")
        print("="*50)

        if not self.processor.ollama.check_connection():
            print("Ollama未连接，无法进行AI处理")
            print("请在Y7000P上启动: ollama serve")
            return 0

        print(f"处理限制: {limit}条/批次")
        print(f"处理间隔: {delay}秒")
        print("="*50)

        total = self.processor.process_all_pending(limit, delay)

        print(f"\nAI处理完成: {total}条消息")

        return total

    def daily_report(self) -> Dict:
        """生成每日摘要报告"""
        print("\n" + "="*50)
        print("  每日摘要报告")
        print("="*50)

        report = self.processor.generate_daily_summary()

        print(f"\n📅 日期: {report['date']}")
        print(f"📊 今日消息: {report['total_messages']}条")

        if report['category_stats']:
            print("\n📈 分类统计:")
            for cat, cnt in report['category_stats'].items():
                bar = '█' * min(cnt, 20)
                print(f"   {cat}: {cnt} {bar}")

        if report['urgent_messages']:
            print("\n🚨 紧急事项:")
            for content, summary, gid, ts in report['urgent_messages'][:5]:
                print(f"   [{gid}] {summary}")
        else:
            print("\n🚨 紧急事项: 无")

        if report['important_messages']:
            print("\n⭐ 重要内容:")
            for content, summary, gid, cat, ts in report['important_messages'][:5]:
                print(f"   [{gid}][{cat}] {summary}")

        return report

    def search(self, keyword: str = None, category: str = None,
               importance: str = None, limit: int = 20) -> List:
        """搜索消息"""
        results = self.processor.search_messages(
            keyword=keyword,
            category=category,
            importance=importance,
            limit=limit
        )

        print(f"\n搜索结果: {len(results)}条")

        for msg in results:
            print(f"\n[{msg['group_id']}] {msg['timestamp']}")
            print(f"  分类: {msg.get('category', '未知')} | 重要性: {msg.get('importance', '未知')}")
            print(f"  {msg['content'][:100]}...")

        return results

    def scan_files(self) -> int:
        """扫描所有群文件"""
        print("\n扫描群文件...")

        total = 0
        for group_id in self.groups:
            count = self.collector.scan_group_files(group_id)
            total += count
            if count > 0:
                print(f"  {group_id}: {count}个文件")

        print(f"\n共发现 {total} 个群文件")

        return total

    def interactive(self):
        """交互式模式"""
        print("\n" + "="*50)
        print("  超级秘书 - 交互模式")
        print("="*50)
        print("可用命令:")
        print("  status     - 查看系统状态")
        print("  fullscan   - 全量扫描所有群")
        print("  scan       - 增量扫描新消息")
        print("  process    - AI处理消息")
        print("  report     - 生成每日报告")
        print("  search     - 搜索消息")
        print("  files      - 扫描群文件")
        print("  quit       - 退出")
        print("="*50)

        while True:
            try:
                cmd = input("\n> ").strip().lower()

                if cmd == 'quit':
                    print("再见!")
                    break
                elif cmd == 'status':
                    self.status()
                elif cmd == 'fullscan':
                    self.full_scan()
                elif cmd == 'scan':
                    self.incremental_scan()
                elif cmd == 'process':
                    self.process_ai()
                elif cmd == 'report':
                    self.daily_report()
                elif cmd == 'search':
                    keyword = input("搜索关键词 (回车跳过): ").strip()
                    self.search(keyword=keyword if keyword else None)
                elif cmd == 'files':
                    self.scan_files()
                else:
                    print("未知命令，输入 'help' 查看帮助")
            except KeyboardInterrupt:
                print("\n\n退出中...")
                break
            except Exception as e:
                print(f"错误: {e}")


def main():
    parser = argparse.ArgumentParser(description='超级秘书 - 群消息智能管家')
    parser.add_argument('command', nargs='?', default='interactive',
                       choices=['interactive', 'status', 'fullscan', 'scan',
                               'process', 'report', 'search', 'files'],
                       help='执行命令')
    parser.add_argument('--config', '-c', default=None, help='配置文件路径')
    parser.add_argument('--batch-size', '-b', type=int, default=100,
                       help='批量大小')
    parser.add_argument('--delay', '-d', type=float, default=2.0,
                       help='批次间隔(秒)')
    parser.add_argument('--limit', '-l', type=int, default=50,
                       help='处理限制')
    parser.add_argument('--keyword', '-k', default=None, help='搜索关键词')

    args = parser.parse_args()

    # 确定配置文件路径
    if args.config:
        config_path = args.config
    else:
        config_path = Path(__file__).parent / 'config.json'

    if not Path(config_path).exists():
        print(f"配置文件不存在: {config_path}")
        sys.exit(1)

    # 创建CLI实例
    cli = BuddySecretaryCLI(str(config_path))

    # 执行命令
    if args.command == 'interactive':
        cli.interactive()
    elif args.command == 'status':
        cli.status()
    elif args.command == 'fullscan':
        cli.full_scan(args.batch_size, args.delay)
    elif args.command == 'scan':
        cli.incremental_scan(args.limit)
    elif args.command == 'process':
        cli.process_ai(args.limit, args.delay)
    elif args.command == 'report':
        cli.daily_report()
    elif args.command == 'search':
        cli.search(keyword=args.keyword, limit=args.limit)
    elif args.command == 'files':
        cli.scan_files()


if __name__ == '__main__':
    main()
