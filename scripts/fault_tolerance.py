"""
多级容错编排模块 - FaultTolerance
===================================

为系统 5 条关键流程设计 Plan A → B → C 降级策略：

  collect_chain — 数据采集（官网 + QQ群 + QQ空间 + 微信）
  filter_chain  — 信息过滤（AI 分析 → 关键词 → 全部通过）
  push_chain    — 消息推送（双通道 → 邮箱 → 延迟队列）
  store_chain   — 数据存储（双写 → IMA → 本地归档）
  ocr_chain     — 图片识别（PaddleOCR → 云 API → 脱标）

特性：
- 每个 Plan 独立 try/except，失败自动降级
- Plan 间 2 秒冷却，避免连续调用打崩下游
- 统计 Plan A / B / C 使用次数
- Plan C 触发时记录警告日志
- 模块级单例，通过 get_ft() 获取实例

依赖：仅标准库（json, logging, time, os, sys, pathlib）
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger('FaultTolerance')

# 确保 scripts 目录在 sys.path 中（与项目其他模块一致）
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _ensure_scripts_path():
    """确保 scripts 目录在 sys.path 中（为 lazy import 准备）"""
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)


# ============================================================
# 容错链注册表
# ============================================================

# 每条链的 Plan 名称列表（A, B, C）
_CHAIN_PLANS = {
    'collect_chain': ['scrapers_collect', 'archive_cache', 'empty_fallback'],
    'filter_chain':  ['ollama_ai', 'keyword_rules', 'pass_all'],
    'push_chain':    ['dual_channel', 'email_only', 'pending_queue'],
    'store_chain':   ['dual_write', 'ima_only', 'local_archive'],
    'ocr_chain':     ['paddleocr', 'cloud_api', 'mark_image_only'],
}

# Plan 标签映射（0→A, 1→B, 2→C）
_PLAN_LABELS = {0: 'A', 1: 'B', 2: 'C'}


# ============================================================
# FaultTolerance 单例
# ============================================================

class FaultTolerance:
    """
    多级容错管理器（模块级单例）

    用法：
        ft = get_ft()
        result = ft.execute_chain('collect_chain')
        status = ft.get_status()
    """

    _instance: Optional['FaultTolerance'] = None

    def __new__(cls):
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._initialized = False
            cls._instance = obj
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self._config: dict = {}
        self._load_config()
        self._init_stats()

    # ─── 配置加载 ─────────────────────────────────────

    def _load_config(self):
        """从 config.json 读取 fault_tolerance 配置节"""
        try:
            base_dir = Path(__file__).resolve().parent.parent
            config_path = base_dir / 'config' / 'config.json'

            if not config_path.exists():
                logger.warning(f"配置文件不存在：{config_path}，使用默认配置")
                self._config = {}
                return

            with open(config_path, 'r', encoding='utf-8') as f:
                full_config = json.load(f)

            self._config = full_config.get('fault_tolerance', {})
            logger.info("容错配置加载成功")

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"容错配置加载失败：{e}，使用默认值")
            self._config = {}

    def _init_stats(self):
        """
        初始化统计字典

        结构：
            {
                'collect_chain': {
                    'plan_a': 0, 'plan_b': 0, 'plan_c': 0,
                    'total_runs': 0, 'total_success': 0,
                },
                ...
            }
        """
        self.stats = {}
        for chain_name, plan_names in _CHAIN_PLANS.items():
            chain_stats = {
                'total_runs': 0,
                'total_success': 0,
            }
            for idx, plan_name in enumerate(plan_names):
                label = f"plan_{_PLAN_LABELS[idx].lower()}"
                chain_stats[label] = 0
            self.stats[chain_name] = chain_stats

    # ─── 便捷属性 ─────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """容错机制是否启用"""
        return self._config.get('enable', True)

    @property
    def plan_timeout(self) -> int:
        """单个 Plan 超时秒数"""
        return self._config.get('plan_timeout_seconds', 30)

    @property
    def plan_cooldown(self) -> float:
        """Plan 间冷却秒数"""
        return self._config.get('plan_cooldown_seconds', 2)

    @property
    def max_retries(self) -> int:
        """每个 Plan 最大重试次数"""
        return self._config.get('max_retries_per_plan', 1)

    @property
    def _alert_on_plan_c(self) -> bool:
        """Plan C 触发时是否告警"""
        return self._config.get('alert_on_plan_c', True)

    # ============================================================
    # Plan 实现方法
    #
    # 每个方法名对应 _CHAIN_PLANS 中注册的 Plan 名称。
    # 方法签名：def _plan_xxx(self, *args, **kwargs) -> Any
    # 返回值将被传递给下一条链 / 作为 execute_chain 的 result。
    # ============================================================

    # ─── 采集链 (collect_chain) ───────────────────────

    def _plan_scrapers_collect(self, *args, **kwargs):
        """
        Plan A: 全量采集（官网 + QQ群 + QQ空间 + 微信）

        依次调用各采集模块，汇总所有原始数据。
        """
        all_infos = []

        # ① 官网爬虫
        try:
            _ensure_scripts_path()
            from hfut_web_scraper import quick_scrape
            items = quick_scrape(*args, **kwargs)
            if items:
                all_infos.extend(items)
            logger.info(f"   ✅ 官网采集：{len(items) if items else 0} 条")
        except Exception as e:
            logger.warning(f"   ⚠️ 官网采集失败：{e}")

        # ② QQ群采集
        try:
            _ensure_scripts_path()
            from qq_group_collector import QQGroupCollector
            collector = QQGroupCollector()
            items = collector.full_scan_groups(
                max_messages_per_group=kwargs.get('max_messages_per_group', 500)
            )
            if items:
                all_infos.extend(items)
            logger.info(f"   ✅ QQ群采集：{len(items) if items else 0} 条")
        except Exception as e:
            logger.warning(f"   ⚠️ QQ群采集失败：{e}")

        # ③ QQ空间采集
        try:
            _ensure_scripts_path()
            from qzone_monitor import monitor_qzone
            items = monitor_qzone()
            if items:
                all_infos.extend(items)
            logger.info(f"   ✅ QQ空间采集：{len(items) if items else 0} 条")
        except Exception as e:
            logger.warning(f"   ⚠️ QQ空间采集失败：{e}")

        # ④ 微信公众号采集
        try:
            _ensure_scripts_path()
            from wechat_monitor import monitor_wechat
            items = monitor_wechat()
            if items:
                all_infos.extend(items)
            logger.info(f"   ✅ 微信采集：{len(items) if items else 0} 条")
        except Exception as e:
            logger.warning(f"   ⚠️ 微信采集失败：{e}")

        return all_infos

    def _plan_archive_cache(self, *args, **kwargs):
        """Plan B: 读取本地缓存 hfut_archive.json 最新条目"""
        _ensure_scripts_path()
        from archive import get_archive
        archive = get_archive()
        all_data = archive.read_all()
        if not all_data:
            raise ValueError("本地归档为空，无法回退")
        # 返回最近 30 条
        return all_data[-30:]

    def _plan_empty_fallback(self, *args, **kwargs):
        """Plan C: 返回空列表 + 日志"""
        logger.warning("⚠️ [采集链] 降级到 Plan C：返回空列表")
        return []

    # ─── 过滤链 (filter_chain) ───────────────────────

    def _plan_ollama_ai(self, text: str = None, *args, **kwargs):
        """Plan A: Ollama AI 分析"""
        if not text:
            raise ValueError("文本内容为空，无法分析")
        _ensure_scripts_path()
        from ai_secretary import analyze_with_ollama
        return analyze_with_ollama(text, *args, **kwargs)

    def _plan_keyword_rules(self, text: str = None, *args, **kwargs):
        """Plan B: 关键词规则过滤"""
        if not text:
            raise ValueError("文本内容为空，无法过滤")
        _ensure_scripts_path()
        from filter import filter_and_extract
        passed, info = filter_and_extract(text)
        return {'passed': passed, 'info': info, 'method': 'keyword_rules'}

    def _plan_pass_all(self, text: str = None, *args, **kwargs):
        """Plan C: 全部通过（不过滤）"""
        logger.warning("⚠️ [过滤链] 降级到 Plan C：全部通过")
        if not text:
            return {'passed': True, 'info': None, 'method': 'pass_all'}
        return {
            'passed': True,
            'info': {
                'title': text[:50] if len(text) > 50 else text,
                'content': text,
                'category': '普通信息',
                'is_important': False,
                'summary': text[:100],
                'keywords': [],
            },
            'method': 'pass_all',
        }

    # ─── 推送链 (push_chain) ─────────────────────────

    def _plan_dual_channel(self, info: dict = None, *args, **kwargs):
        """Plan A: QQ邮箱 + NapCat 双推送"""
        if not info:
            raise ValueError("推送内容为空")
        _ensure_scripts_path()
        from push import push_immediate
        push_immediate(info, use_qq=True, use_email=True)
        return {'channel': 'dual', 'success': True}

    def _plan_email_only(self, info: dict = None, *args, **kwargs):
        """Plan B: 仅 QQ 邮箱推送"""
        if not info:
            raise ValueError("推送内容为空")
        _ensure_scripts_path()
        from push import send_email

        title = info.get('title', '无标题')[:50]
        content = info.get('content', '') or json.dumps(info, ensure_ascii=False)
        source = info.get('source', '未知')
        time_str = info.get('time', '未知')

        email_body = f"""合工大信息监控系统 - 推送通知

来源：{source}
时间：{time_str}

{content}

---
此邮件由合工大信息监控系统自动发送
"""
        success = send_email(f"【合工大监控】{title}", email_body)
        return {'channel': 'email_only', 'success': success}

    def _plan_pending_queue(self, info: dict = None, *args, **kwargs):
        """Plan C: 写入 pending_push.json 延迟推送"""
        _ensure_scripts_path()
        from config_loader import get_data_dir

        pending_file = get_data_dir() / 'pending_push.json'

        # 读取已有队列
        pending = []
        if pending_file.exists():
            try:
                with open(pending_file, 'r', encoding='utf-8') as f:
                    pending = json.load(f)
            except (json.JSONDecodeError, IOError):
                pending = []

        # 追加新条目
        if info:
            info['pending_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            pending.append(info)

        # 写入文件
        os.makedirs(pending_file.parent, exist_ok=True)
        with open(pending_file, 'w', encoding='utf-8') as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)

        logger.warning(
            f"⚠️ [推送链] 降级到 Plan C：写入待推送队列（共 {len(pending)} 条）"
        )
        return {'channel': 'pending_queue', 'queued_count': len(pending)}

    # ─── 存储链 (store_chain) ────────────────────────

    def _plan_dual_write(self, info: dict = None, *args, **kwargs):
        """Plan A: IMA + Notion 双写"""
        if not info:
            raise ValueError("存储内容为空")
        _ensure_scripts_path()
        from knowledge_base import KnowledgeBaseManager
        from archive import save_to_archive

        result = {}
        title = info.get('title', '无标题')
        content = info.get('content', '') or json.dumps(info, ensure_ascii=False)

        # IMA 知识库写入
        try:
            kb = KnowledgeBaseManager()
            kb.add_text_note(title=title, content=content)
            result['ima'] = True
        except Exception as e:
            result['ima'] = str(e)
            logger.warning(f"IMA 写入失败：{e}")

        # Notion 写入
        try:
            from notion_manager import NotionManager
            nm = NotionManager()
            nm.create_entry(info)
            result['notion'] = True
        except Exception as e:
            result['notion'] = str(e)
            logger.warning(f"Notion 写入失败：{e}")

        # 本地归档（双写的一部分）
        try:
            save_to_archive(info)
            result['archive'] = True
        except Exception as e:
            result['archive'] = str(e)

        # 判断结果
        ima_ok = result.get('ima') is True
        notion_ok = result.get('notion') is True
        archive_ok = result.get('archive') is True

        if ima_ok and notion_ok:
            result['success'] = True
        elif ima_ok or archive_ok:
            result['success'] = 'partial'
        else:
            raise RuntimeError(f"存储全部失败：{result}")

        return result

    def _plan_ima_only(self, info: dict = None, *args, **kwargs):
        """Plan B: 仅 IMA 知识库"""
        if not info:
            raise ValueError("存储内容为空")
        _ensure_scripts_path()
        from knowledge_base import KnowledgeBaseManager

        kb = KnowledgeBaseManager()
        title = info.get('title', '无标题')
        content = info.get('content', '') or json.dumps(info, ensure_ascii=False)
        kb.add_text_note(title=title, content=content)
        return {'method': 'ima_only', 'success': True}

    def _plan_local_archive(self, info: dict = None, *args, **kwargs):
        """Plan C: 本地 JSON 归档"""
        if not info:
            raise ValueError("存储内容为空")
        _ensure_scripts_path()
        from archive import save_to_archive

        save_to_archive(info)
        logger.warning("⚠️ [存储链] 降级到 Plan C：仅本地归档")
        return {'method': 'local_archive', 'success': True}

    # ─── OCR 链 (ocr_chain) ──────────────────────────

    def _plan_paddleocr(self, image_path: str = None, *args, **kwargs):
        """Plan A: 本地 PaddleOCR"""
        if not image_path:
            raise ValueError("图片路径为空")
        _ensure_scripts_path()
        from ocr_extractor import OCRExtractor
        extractor = OCRExtractor()
        return extractor._extract_with_paddleocr(image_path)

    def _plan_cloud_api(self, image_path: str = None, *args, **kwargs):
        """Plan B: 百度/腾讯云 OCR API"""
        if not image_path:
            raise ValueError("图片路径为空")
        _ensure_scripts_path()
        from ocr_extractor import OCRExtractor
        extractor = OCRExtractor()
        return extractor._extract_with_online_api(image_path)

    def _plan_mark_image_only(self, image_path: str = None, *args, **kwargs):
        """Plan C: 标记 [图片]未OCR"""
        logger.warning("⚠️ [OCR链] 降级到 Plan C：标记未OCR")
        return "[图片]未OCR"

    # ============================================================
    # 核心公开方法
    # ============================================================

    def _get_plan_method(self, plan_name: str):
        """
        根据 Plan 名称获取对应的实例方法

        Plan 命名约定：配置中的 Plan 名 → 方法名 _plan_{plan_name}
        例如：'scrapers_collect' → self._plan_scrapers_collect
        """
        method_name = f'_plan_{plan_name}'
        method = getattr(self, method_name, None)
        if method is None:
            raise AttributeError(f"未找到 Plan 方法：{method_name}")
        return method

    def execute_chain(self, chain_name: str, *args, **kwargs) -> dict:
        """
        执行容错链，依次尝试 Plan A → B → C

        Args:
            chain_name: 链名称，可选值：
                collect_chain / filter_chain / push_chain /
                store_chain / ocr_chain
            *args, **kwargs: 传递给各 Plan 的参数

        Returns:
            {
                'result': Any,          # 执行结果
                'plan_used': str,       # 最终成功的 Plan 名称
                'plan_label': str,      # 最终成功的 Plan 标签（A/B/C）
                'error': str | None,    # 错误信息（成功时为 None）
                'attempts': int,        # 尝试次数
                'success': bool,        # 是否成功
            }
        """
        # 检查容错开关
        if not self.enabled:
            logger.debug(f"容错机制已禁用，跳过链：{chain_name}")
            return {
                'result': None,
                'plan_used': None,
                'plan_label': None,
                'error': 'fault_tolerance_disabled',
                'attempts': 0,
                'success': False,
            }

        # 校验链名
        valid_chains = list(_CHAIN_PLANS.keys())
        if chain_name not in valid_chains:
            return {
                'result': None,
                'plan_used': None,
                'plan_label': None,
                'error': f"未知容错链：{chain_name}，可用链：{valid_chains}",
                'attempts': 0,
                'success': False,
            }

        plan_names = _CHAIN_PLANS[chain_name]
        last_error = None

        # 更新统计
        self.stats[chain_name]['total_runs'] += 1

        for attempt_idx, plan_name in enumerate(plan_names):
            plan_label = _PLAN_LABELS[attempt_idx]  # A, B, C
            stat_key = f"plan_{plan_label.lower()}"  # plan_a, plan_b, plan_c

            logger.info(f"🔗 [{chain_name}] 尝试 Plan {plan_label}：{plan_name}")

            # 获取 Plan 方法（延迟绑定）
            try:
                plan_method = self._get_plan_method(plan_name)
            except AttributeError as e:
                last_error = str(e)
                logger.error(f"❌ [{chain_name}] {last_error}")
                continue

            # 执行 Plan（最多重试 max_retries + 1 次）
            for retry in range(self.max_retries + 1):
                # 重试日志
                if retry > 0:
                    logger.info(
                        f"   ↻ [{chain_name}] Plan {plan_label} 重试 {retry}/{self.max_retries}"
                    )
                    time.sleep(1)  # 重试前短暂等待

                try:
                    result = plan_method(*args, **kwargs)

                    # 成功
                    self.stats[chain_name][stat_key] += 1
                    self.stats[chain_name]['total_success'] += 1

                    logger.info(
                        f"✅ [{chain_name}] Plan {plan_label} ({plan_name}) 执行成功"
                    )
                    return {
                        'result': result,
                        'plan_used': plan_name,
                        'plan_label': plan_label,
                        'error': None,
                        'attempts': attempt_idx + 1,
                        'success': True,
                    }

                except TimeoutError as e:
                    last_error = f"超时（>{self.plan_timeout}秒）"
                    logger.warning(
                        f"⏱️ [{chain_name}] Plan {plan_label}：{last_error}"
                    )
                    break  # 超时不再重试，直接降级

                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                    logger.warning(
                        f"❌ [{chain_name}] Plan {plan_label}（试探 {retry + 1}）：{last_error}"
                    )
                    # 如果还有重试机会，继续循环；否则降级

            # Plan 间冷却（最后一个 Plan 不需要冷却）
            if attempt_idx < len(plan_names) - 1:
                cooldown = self.plan_cooldown
                logger.debug(f"⏳ Plan 间冷却 {cooldown} 秒...")
                time.sleep(cooldown)

        # 所有 Plan 均失败
        if self._alert_on_plan_c:
            logger.error(
                f"🚨 [{chain_name}] 所有 Plan 均失败！最终错误：{last_error}"
            )

        return {
            'result': None,
            'plan_used': None,
            'plan_label': None,
            'error': last_error,
            'attempts': len(plan_names),
            'success': False,
        }

    def get_status(self) -> dict:
        """
        返回所有容错链的状态统计

        Returns:
            {
                'collect_chain': {
                    'plan_a': 8, 'plan_b': 2, 'plan_c': 0,
                    'total_runs': 10, 'total_success': 10,
                },
                ...
            }
        """
        return self.stats.copy()

    def reset_stats(self):
        """重置所有统计信息"""
        for chain_name in self.stats:
            self.stats[chain_name] = {
                k: 0 for k in self.stats[chain_name]
            }
        logger.info("🔄 容错统计已重置")


# ============================================================
# 模块级单例访问函数
# ============================================================

def get_ft() -> FaultTolerance:
    """获取 FaultTolerance 单例"""
    return FaultTolerance()


# ============================================================
# 独立测试
# ============================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )

    print("=" * 60)
    print("容错机制单元测试")
    print("=" * 60)

    ft = get_ft()

    # 1. 查看初始状态
    print("\n[1] 初始状态：")
    status = ft.get_status()
    for chain_name, info in status.items():
        print(f"  {chain_name}: 已运行 {info['total_runs']} 次")
        for key in ('plan_a', 'plan_b', 'plan_c'):
            if info.get(key, 0) > 0:
                print(f"    {key}: {info[key]}")

    # 2. 测试 collect_chain
    print("\n[2] 测试 collect_chain...")
    result = ft.execute_chain('collect_chain')
    print(
        f"  结果: success={result['success']}, "
        f"plan={result['plan_used']}, attempts={result['attempts']}"
    )
    if result['success'] and result['result'] is not None:
        print(f"  采集到 {len(result['result'])} 条数据")

    # 3. 测试 filter_chain（关键词过滤）
    print("\n[3] 测试 filter_chain（关键词过滤）...")
    result = ft.execute_chain(
        'filter_chain',
        text="【通知】明天上午9点开班会，请准时参加"
    )
    print(
        f"  结果: success={result['success']}, "
        f"plan={result['plan_used']}"
    )
    if result['result']:
        print(f"  分析: {result['result']}")

    # 4. 测试不存在的链
    print("\n[4] 测试不存在的链...")
    result = ft.execute_chain('invalid_chain')
    print(f"  结果: success={result['success']}, error={result['error']}")

    # 5. 查看最终状态
    print("\n[5] 最终状态：")
    status = ft.get_status()
    for chain_name, info in status.items():
        runs = info['total_runs']
        if runs > 0:
            success = info['total_success']
            rate = round(success / runs * 100, 1) if runs > 0 else 0
            print(f"  {chain_name}: {runs} 次运行, 成功 {success}, 成功率 {rate}%")
            for key in ('plan_a', 'plan_b', 'plan_c'):
                if info.get(key, 0) > 0:
                    print(f"    {key}: {info[key]} 次")

    # 6. 重置统计
    ft.reset_stats()
    print("\n[6] 重置后状态：")
    status = ft.get_status()
    for chain_name, info in status.items():
        assert info['total_runs'] == 0, f"{chain_name} 未正确重置"
    print("  ✅ 所有统计已清零")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
