"""
超级秘书 - 自动化任务脚本
用于定时执行扫描和处理
"""

import sys
import json
import time
from datetime import datetime
from pathlib import Path

from qq_collector import QQGroupCollector
from ai_processor import MessageProcessor


def run_full_pipeline():
    """完整流程: 扫描 -> AI处理 -> 生成报告"""
    print(f"\n{'='*60}")
    print(f"  超级秘书自动化任务")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    config_path = Path(__file__).parent / 'config.json'

    # 初始化
    collector = QQGroupCollector(str(config_path))
    processor = MessageProcessor(str(config_path))

    results = {
        'start_time': datetime.now().isoformat(),
        'scan_results': {},
        'process_count': 0,
        'errors': []
    }

    # Step 1: 检查连接
    print("\n[1/4] 检查服务状态...")

    if not collector.check_connection():
        msg = "NapCat未连接"
        print(f"  错误: {msg}")
        results['errors'].append(msg)
        return results

    if not processor.ollama.check_connection():
        msg = "Ollama未连接，AI处理将跳过"
        print(f"  警告: {msg}")
        results['errors'].append(msg)

    # Step 2: 增量扫描
    print("\n[2/4] 增量扫描群消息...")
    try:
        results['scan_results'] = collector.incremental_scan_all_groups(num=30)
        new_messages = sum(results['scan_results'].values())
        print(f"  新增消息: {new_messages}条")
    except Exception as e:
        msg = f"扫描失败: {e}"
        print(f"  错误: {msg}")
        results['errors'].append(msg)

    # Step 3: AI处理
    print("\n[3/4] AI处理消息...")
    try:
        if processor.ollama.check_connection():
            results['process_count'] = processor.process_all_pending(
                limit=100,
                delay=0.5
            )
            print(f"  处理完成: {results['process_count']}条")
        else:
            print("  跳过: Ollama未连接")
    except Exception as e:
        msg = f"AI处理失败: {e}"
        print(f"  错误: {msg}")
        results['errors'].append(msg)

    # Step 4: 生成报告
    print("\n[4/4] 生成报告...")
    try:
        report = processor.generate_daily_summary()

        # 保存报告
        report_path = Path(__file__).parent / 'reports' / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_path.parent.mkdir(exist_ok=True)

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({
                'generated_at': datetime.now().isoformat(),
                'summary': report,
                'scan_results': results['scan_results'],
                'process_count': results['process_count']
            }, f, ensure_ascii=False, indent=2)

        print(f"  报告已保存: {report_path.name}")
    except Exception as e:
        print(f"  报告生成失败: {e}")

    # 完成
    results['end_time'] = datetime.now().isoformat()
    elapsed = (datetime.fromisoformat(results['end_time']) -
               datetime.fromisoformat(results['start_time'])).total_seconds()

    print(f"\n{'='*60}")
    print(f"  任务完成")
    print(f"  结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  总耗时: {elapsed:.1f}秒")
    print(f"  新增消息: {sum(results['scan_results'].values())}条")
    print(f"  AI处理: {results['process_count']}条")
    if results['errors']:
        print(f"  错误: {len(results['errors'])}个")
    print(f"{'='*60}")

    return results


def run_full_scan():
    """全量扫描(一次性任务)"""
    print(f"\n{'='*60}")
    print(f"  全量扫描任务")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    config_path = Path(__file__).parent / 'config.json'
    collector = QQGroupCollector(str(config_path))

    if not collector.check_connection():
        print("错误: NapCat未连接")
        return

    print("\n开始全量扫描所有群...")
    results = collector.full_scan_all_groups(batch_size=100, delay=2.0)

    total = sum(results.values())
    print(f"\n全量扫描完成: {total}条消息")

    return results


def run_ai_processing():
    """仅运行AI处理"""
    print(f"\n开始AI处理...")

    config_path = Path(__file__).parent / 'config.json'
    processor = MessageProcessor(str(config_path))

    if not processor.ollama.check_connection():
        print("错误: Ollama未连接")
        return

    count = processor.process_all_pending(limit=200, delay=0.5)
    print(f"AI处理完成: {count}条消息")

    return count


if __name__ == '__main__':
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == 'full':
            run_full_pipeline()
        elif command == 'fullscan':
            run_full_scan()
        elif command == 'process':
            run_ai_processing()
        else:
            print(f"未知命令: {command}")
            print("用法: python auto_tasks.py [full|fullscan|process]")
    else:
        run_full_pipeline()
