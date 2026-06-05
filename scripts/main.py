"""
主控制脚本 - 合工大信息监控系统
功能：整合所有模块，提供统一入口

修复：
- 配置加载异常增强（支持 json.JSONDecodeError）
- 日志配置防重复 handler
- scheduler job 引用方式优化
"""

import json
import logging
import importlib
from datetime import datetime
import time
import sys
import os

# 添加 scripts 目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# 配置日志（防止重复添加 handler）
def _setup_logger():
    """配置日志系统，避免重复 handler"""
    logger = logging.getLogger('HFUT_Monitor')
    
    # 如果已有 handler，说明已配置过，跳过
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 文件 handler
    try:
        fh = logging.FileHandler(os.path.join(LOG_DIR, 'monitor.log'), encoding='utf-8')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except (IOError, OSError) as e:
        print(f"⚠️ 无法创建日志文件：{e}")
    
    # 控制台 handler
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    
    return logger

logger = _setup_logger()

# 加载配置（带详细错误提示）
CONFIG_PATH = os.path.join(BASE_DIR, 'config', 'config.json')

try:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print(f"❌ 配置文件不存在：{CONFIG_PATH}")
    print("   请先复制 config/config.example.json 为 config/config.json 并填入配置")
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f"❌ 配置文件 JSON 格式错误：{e}")
    print(f"   请检查 {CONFIG_PATH} 的格式是否正确")
    sys.exit(1)


def import_modules():
    """
    延迟导入模块，处理缺失依赖
    
    Returns:
        dict: 模块字典 {'filter': {...}, 'push': {...}, ...}
    """
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
    
    try:
        from hfut_web_scraper import scrape_all as scrape_web, quick_scrape
        modules['scraper'] = {
            'scrape_all': scrape_web,
            'quick_scrape': quick_scrape,
        }
        logger.info("✅ hfut_web_scraper 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  hfut_web_scraper 模块加载失败：{e}（需要 beautifulsoup4）")
        modules['scraper'] = None
    
    # 新增：QQ 群监控
    try:
        from qq_group_monitor import monitor_qq_groups
        modules['qq_group'] = monitor_qq_groups
        logger.info("✅ qq_group_monitor 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  qq_group_monitor 模块加载失败：{e}")
        modules['qq_group'] = None
    
    # 新增：微信公众号增强监控
    try:
        from wechat_monitor_enhanced import monitor_wechat_official
        modules['wechat_enhanced'] = monitor_wechat_official
        logger.info("✅ wechat_monitor_enhanced 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  wechat_monitor_enhanced 模块加载失败：{e}")
        modules['wechat_enhanced'] = None

    # 新增：知识库模块
    try:
        modules['knowledge_base'] = importlib.import_module('knowledge_base')
        logger.info("✅ knowledge_base 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  knowledge_base 模块加载失败：{e}")
        modules['knowledge_base'] = None

    # 新增：QQ群消息采集器（轻量版）
    try:
        modules['qq_group_collector'] = importlib.import_module('qq_group_collector')
        logger.info("✅ qq_group_collector 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  qq_group_collector 模块加载失败：{e}")
        modules['qq_group_collector'] = None

    # 新增：OCR 提取器
    try:
        modules['ocr_extractor'] = importlib.import_module('ocr_extractor')
        logger.info("✅ ocr_extractor 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  ocr_extractor 模块加载失败：{e}")
        modules['ocr_extractor'] = None

    # v2 新增：二手物品检测器
    try:
        modules['secondhand_detector'] = importlib.import_module('secondhand_detector')
        logger.info("✅ secondhand_detector 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  secondhand_detector 模块加载失败：{e}")
        modules['secondhand_detector'] = None

    # v2 新增：Ollama 本地 AI 管理器
    try:
        modules['ollama_manager'] = importlib.import_module('ollama_manager')
        logger.info("✅ ollama_manager 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  ollama_manager 模块加载失败：{e}")
        modules['ollama_manager'] = None

    # v2 新增：Notion 管理器
    try:
        modules['notion_manager'] = importlib.import_module('notion_manager')
        logger.info("✅ notion_manager 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  notion_manager 模块加载失败：{e}")
        modules['notion_manager'] = None

    # v2 新增：统一存储管理器
    try:
        modules['storage_manager'] = importlib.import_module('storage_manager')
        logger.info("✅ storage_manager 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  storage_manager 模块加载失败：{e}")
        modules['storage_manager'] = None

    # v2 新增：容错编排器
    try:
        modules['fault_tolerance'] = importlib.import_module('fault_tolerance')
        logger.info("✅ fault_tolerance 模块加载成功")
    except ImportError as e:
        logger.warning(f"⚠️  fault_tolerance 模块加载失败：{e}")
        modules['fault_tolerance'] = None

    return modules


def _check_ocr_dependencies():
    """检测已安装的 OCR 依赖包"""
    available = []
    try:
        import pytesseract  # noqa: F401
        available.append('pytesseract')
    except ImportError:
        pass
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        available.append('paddleocr')
    except ImportError:
        pass
    return available


def check_components(config) -> dict:
    """启动时检查所有组件状态

    Returns:
        {
            "napcat": {"status": "ok/error", "detail": "..."},
            "knowledge_base": {"status": "ok/error", "detail": "..."},
            "qzone_cookies": {"status": "ok/error", "detail": "..."},
            "ocr": {"status": "ok/none", "detail": "..."}
        }
    """
    results = {}

    # 1. 检查 NapCat 连接
    try:
        import requests as _req
        napcat_cfg = config.get('napcat', {})
        base_url = napcat_cfg.get('api_url', napcat_cfg.get('base_url', 'http://localhost:3001'))
        token = napcat_cfg.get('token', '')

        headers = {}
        if token:
            headers['Authorization'] = f'Bearer {token}'

        resp = _req.get(f'{base_url}/get_login_info', headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            nickname = data.get('data', {}).get('nickname', '未知')
            results['napcat'] = {"status": "ok", "detail": f"已连接，账号: {nickname}"}
        else:
            results['napcat'] = {"status": "error", "detail": f"HTTP {resp.status_code}"}
    except Exception as e:
        results['napcat'] = {"status": "error", "detail": str(e)}

    # 2. 检查知识库连接
    try:
        modules = import_modules()
        kb_mod = modules.get('knowledge_base')
        if kb_mod and hasattr(kb_mod, 'KnowledgeBaseManager'):
            mgr = kb_mod.KnowledgeBaseManager()
            ok = mgr.health_check()
            results['knowledge_base'] = {"status": "ok" if ok else "error", "detail": "连接正常" if ok else "连接失败"}
        elif kb_mod:
            results['knowledge_base'] = {"status": "ok", "detail": "模块已加载（无 KnowledgeBaseManager 类）"}
        else:
            results['knowledge_base'] = {"status": "error", "detail": "knowledge_base 模块未加载"}
    except Exception as e:
        results['knowledge_base'] = {"status": "error", "detail": str(e)}

    # 3. 检查 QQ 空间 Cookie
    try:
        qq_accounts = config.get('qq_accounts', [])
        # 兼容新旧格式：旧格式为字符串列表，新格式为对象数组
        qzone_cfg = config.get('qzone', {})
        cookie_file_cfg = qzone_cfg.get('cookie_file', '')

        if cookie_file_cfg:
            # 使用配置中指定的 cookie 文件路径
            cookie_path = os.path.join(BASE_DIR, cookie_file_cfg)
            if os.path.exists(cookie_path):
                results['qzone_cookies'] = {"status": "ok", "detail": f"Cookie 文件存在（{cookie_file_cfg}）"}
            else:
                results['qzone_cookies'] = {"status": "error", "detail": f"Cookie 文件缺失（{cookie_file_cfg}）"}
        elif qq_accounts:
            # 回退：检查 data/qzone_cookies/{qq}.json
            cookie_dir = os.path.join(BASE_DIR, 'data', 'qzone_cookies')
            has_cookies = False
            found_qq = []
            for account in qq_accounts:
                qq = account.get('qq', account) if isinstance(account, dict) else account
                cookie_f = os.path.join(cookie_dir, f'{qq}.json')
                if os.path.exists(cookie_f):
                    has_cookies = True
                    found_qq.append(str(qq))
            if has_cookies:
                results['qzone_cookies'] = {"status": "ok", "detail": f"已找到 Cookie（{', '.join(found_qq[:3])}{'...' if len(found_qq) > 3 else ''}）"}
            else:
                results['qzone_cookies'] = {"status": "error", "detail": f"Cookie 文件缺失（{cookie_dir}）"}
        else:
            results['qzone_cookies'] = {"status": "error", "detail": "未配置 qq_accounts"}
    except Exception as e:
        results['qzone_cookies'] = {"status": "error", "detail": str(e)}

    # 4. 检测 OCR 可用方案
    try:
        modules = import_modules()
        ocr_mod = modules.get('ocr_extractor')
        if ocr_mod and hasattr(ocr_mod, 'OCRExtractor'):
            try:
                ext = ocr_mod.OCRExtractor()
                method = ext.detect_available_method()
                if method and method != 'none':
                    results['ocr'] = {"status": "ok", "detail": f"OCR 方法: {method}"}
                else:
                    results['ocr'] = {"status": "none", "detail": f"OCR 方法: {method or '无可用方案'}"}
            except OSError as oe:
                # 可能是 temp_dir 创建被环境限制（如沙箱）
                results['ocr'] = {"status": "none", "detail": f"模块可用，初始化受限（{oe}），检测 OCR 依赖..."}
                # 回退检测
                available = _check_ocr_dependencies()
                if available:
                    results['ocr']['detail'] += f" 可用: {', '.join(available)}"
                else:
                    results['ocr']['detail'] += " 无 OCR 依赖"
        elif ocr_mod:
            results['ocr'] = {"status": "ok", "detail": "模块已加载（无 OCRExtractor 类）"}
        else:
            available = _check_ocr_dependencies()
            if available:
                results['ocr'] = {"status": "ok", "detail": f"可用引擎: {', '.join(available)}"}
            else:
                results['ocr'] = {"status": "none", "detail": "无 OCR 依赖（pytesseract/paddleocr 均未安装）"}
    except Exception as e:
        results['ocr'] = {"status": "error", "detail": str(e)}

    # 5. 检测 Ollama 本地 AI（v2 新增）
    try:
        modules = import_modules()
        ollama_mod = modules.get('ollama_manager')
        if ollama_mod and hasattr(ollama_mod, 'check_health'):
            ok, detail = ollama_mod.check_health()
            results['ollama'] = {"status": "ok" if ok else "none", "detail": detail}
        elif ollama_mod and hasattr(ollama_mod, 'OllamaManager'):
            mgr = ollama_mod.OllamaManager()
            ok, detail = mgr.check_health()
            results['ollama'] = {"status": "ok" if ok else "none", "detail": detail}
        else:
            results['ollama'] = {"status": "none", "detail": "Ollama 未安装或模块未加载"}
    except Exception as e:
        results['ollama'] = {"status": "error", "detail": str(e)}

    # 6. 检测 Notion 连接（v2 新增）
    try:
        modules = import_modules()
        notion_mod = modules.get('notion_manager')
        if notion_mod and hasattr(notion_mod, 'NotionManager'):
            mgr = notion_mod.NotionManager()
            if mgr.api_key:
                ok, detail = mgr.health_check()
                results['notion'] = {"status": "ok" if ok else "error", "detail": detail}
            else:
                results['notion'] = {"status": "none", "detail": "Notion 未配置 API Key（可选）"}
        else:
            results['notion'] = {"status": "none", "detail": "Notion 模块未加载（可选）"}
    except Exception as e:
        results['notion'] = {"status": "error", "detail": str(e)}

    # 7. 检测统一存储管理器（v2 新增）
    try:
        modules = import_modules()
        storage_mod = modules.get('storage_manager')
        if storage_mod and hasattr(storage_mod, 'StorageManager'):
            mgr = storage_mod.StorageManager()
            health = mgr.health_check()
            results['storage'] = {"status": "ok", "detail": f"IMA={health.get('ima',{}).get('status','?')} | Notion={health.get('notion',{}).get('status','?')} | Archive={health.get('local_archive',{}).get('status','?')}"}
        else:
            results['storage'] = {"status": "none", "detail": "Storage 模块未加载"}
    except Exception as e:
        results['storage'] = {"status": "error", "detail": str(e)}

    # 8. 检测容错编排器（v2 新增）
    try:
        modules = import_modules()
        ft_mod = modules.get('fault_tolerance')
        if ft_mod:
            results['fault_tolerance'] = {"status": "ok", "detail": "容错链就绪（collect/filter/push/store/ocr）"}
        else:
            results['fault_tolerance'] = {"status": "none", "detail": "容错模块未加载"}
    except Exception as e:
        results['fault_tolerance'] = {"status": "error", "detail": str(e)}

    # 打印结果
    logger.info("━━ 组件健康检查 ━━")
    for name, info in results.items():
        status_icon = "✅" if info['status'] == 'ok' else ("⚠️" if info['status'] == 'none' else "❌")
        logger.info(f"  {status_icon} {name}: {info['detail']}")

    return results


def test_configuration():
    """测试配置（使用 config_loader 以支持 .env 环境变量覆盖）"""
    # 使用 config_loader 加载配置（支持 .env 覆盖）
    try:
        from config_loader import load_config
        CONFIG = load_config()
    except ImportError:
        logger.warning("⚠️  config_loader 不可用，回退到直接读取")
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                CONFIG = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"配置文件 JSON 解析失败: {e}")
            return

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
    qq_accounts = CONFIG.get('qq_accounts', [])
    print(f"  QQ空间：{len(qq_accounts)} 个账号")
    for qq in qq_accounts:
        print(f"    - {qq}")

    wechat_accounts = CONFIG.get('wechat_accounts', [])
    print(f"  微信公众号：{len(wechat_accounts)} 个")
    for account in wechat_accounts:
        print(f"    - {account}")

    # 检查推送配置
    print()
    print("📋 推送配置：")
    push_method = CONFIG.get('push_method', {})
    print(f"  方式：{push_method.get('smtp_server', '未配置')}")
    print(f"  发件人：{push_method.get('sender_email', '未配置')}")
    print(f"  收件人：{push_method.get('receiver_email', '未配置')}")

    sender_pwd = push_method.get('sender_password', '')
    if not sender_pwd or sender_pwd in ("你的邮箱授权码", ""):
        print("  ⚠️  邮箱授权码未配置！（请在 .env 中设置 SENDER_PASSWORD）")
    else:
        print("  ✅ 邮箱授权码已配置")

    # NapCat 状态
    napcat = CONFIG.get('napcat', {})
    if napcat.get('enable'):
        napcat_token = napcat.get('token', '')
        if not napcat_token or napcat_token == "NapCat_API_Token":
            print("  ⚠️  NapCat 已启用但 token 未配置（请在 .env 中设置 NAPCAT_TOKEN）")
        else:
            print("  ✅ NapCat 已配置（token: {}...)".format(napcat_token[:6]))
    else:
        print("  ℹ️  NapCat 未启用")

    # QQ群状态
    qq_groups = CONFIG.get('qq_groups', [])
    print()
    print("📋 QQ群监控：")
    if qq_groups:
        print(f"  已配置 {len(qq_groups)} 个群")
        for g in qq_groups:
            print(f"    - {g}")
    else:
        print("  ⚠️  未配置任何QQ群（待办 T12）")

    print()
    print("=" * 60)


def run_monitor():
    """运行监控（主函数）— 完整流程：采集 → 过滤 → 归档 → 推送"""
    logger.info("=" * 60)
    logger.info("合工大信息监控系统 - 启动")
    logger.info("=" * 60)
    logger.info(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    
    # 导入模块
    modules = import_modules()
    
    # ═══ Phase 1: 数据采集（官网爬虫为主）═══
    all_raw_infos = []
    
    if modules.get('scraper'):
        logger.info("🌐 [Phase 1/4] 采集合工大官网数据...")
        try:
            raw_items = modules['scraper']['quick_scrape']()
            all_raw_infos.extend(raw_items)
            logger.info(f"   官网采集到 {len(raw_items)} 条原始数据")
        except Exception as e:
            logger.error(f"❌ 官网采集失败：{e}")
    else:
        logger.warning("⚠️  官网爬虫模块未加载，跳过官网采集")
    
    # 可选：微信公众号监控（补充数据源）
    if modules.get('wechat'):
        logger.info("📱 [Phase 1 补充] 微信公众号监控...")
        try:
            modules['wechat']()
        except Exception as e:
            logger.error(f"❌ 微信公众号监控失败：{e}")
    
    # 可选：QQ空间监控（补充数据源）
    if modules.get('qzone'):
        logger.info("💬 [Phase 1 补充] QQ空间监控...")
        try:
            modules['qzone']()
        except Exception as e:
            logger.error(f"❌ QQ空间监控失败：{e}")
    
    # 新增：QQ 群监控
    if modules.get('qq_group'):
        logger.info("💬 [Phase 1e] QQ 群消息监控...")
        try:
            qq_items = modules['qq_group'](headless=True)
            if qq_items:
                all_raw_infos.extend(qq_items)
                logger.info(f"   QQ 群监控到 {len(qq_items)} 条消息")
        except Exception as e:
            logger.error(f"❌ QQ 群监控失败：{e}")
    
    # 新增：QQ群消息采集（从配置中选关键群，通过 NapCat API 轻量采集）
    if modules.get('qq_group_collector'):
        qq_groups_cfg = CONFIG.get('qq_groups', [])
        if qq_groups_cfg:
            logger.info(f"💬 [Phase 1d] QQ群消息采集（NapCat API）... 共 {len(qq_groups_cfg)} 个群")
            try:
                collector = modules['qq_group_collector']
                if hasattr(collector, 'collect_groups'):
                    qq_collected = collector.collect_groups(qq_groups_cfg)
                    if qq_collected:
                        all_raw_infos.extend(qq_collected)
                        logger.info(f"   QQ群采集到 {len(qq_collected)} 条消息")
                    else:
                        logger.info("   QQ群无新消息")
                else:
                    logger.warning("   qq_group_collector 缺少 collect_groups 方法")
            except Exception as e:
                logger.error(f"❌ QQ群消息采集失败：{e}")
        else:
            logger.info("ℹ️  未配置 qq_groups，跳过 QQ群消息采集")

    # 将采集到的数据存入知识库
    if modules.get('knowledge_base') and all_raw_infos:
        logger.info(f"📚 [Phase 1e] 存入知识库（{len(all_raw_infos)} 条）...")
        try:
            kb = modules['knowledge_base']
            if hasattr(kb, 'batch_store'):
                stored = kb.batch_store(all_raw_infos)
                logger.info(f"   知识库存储成功: {stored} 条")
            elif hasattr(kb, 'store'):
                success = 0
                for item in all_raw_infos:
                    try:
                        kb.store(item)
                        success += 1
                    except Exception:
                        pass
                logger.info(f"   知识库存储成功: {success}/{len(all_raw_infos)} 条")
            else:
                logger.warning("   knowledge_base 缺少 batch_store/store 方法")
        except Exception as e:
            logger.error(f"❌ 知识库存储失败：{e}")

    # 新增：微信公众号增强监控（weixin_search_mcp 方案）
    if modules.get('wechat_enhanced'):
        logger.info("📲 [Phase 1f] 微信公众号增强监控（搜狗搜索）...")
        try:
            wechat_items = modules['wechat_enhanced']()
            if wechat_items:
                all_raw_infos.extend(wechat_items)
                logger.info(f"   微信公众号获得 {len(wechat_items)} 条")
        except Exception as e:
            logger.error(f"❌ 微信公众号增强监控失败：{e}")
    
    # ═══ Phase 2: 过滤 + 分析（AI处理）═══
    filtered_infos = []
    important_infos = []
    
    if modules.get('filter') and all_raw_infos:
        logger.info(f"🔍 [Phase 2/4] 过滤分析 {len(all_raw_infos)} 条数据...")
        
        filter_fn = modules['filter']['filter_and_extract']
        is_important_fn = modules['filter'].get('is_important', lambda x: False)
        
        from archive import save_to_archive
        
        for item in all_raw_infos:
            title = item.get('title', '')
            content = item.get('content', title)  # 无正文时用标题
            
            try:
                is_pass, info = filter_fn(content)
                if is_pass:
                    # 合并爬虫数据和过滤结果
                    info.update({
                        'source': item.get('source', '未知'),
                        'time': item.get('time', datetime.now().strftime('%Y-%m-%d')),
                        'url': item.get('url', ''),
                    })
                    
                    if is_important_fn(content):
                        important_infos.append(info)
                        logger.info(f"   🚨 重要: {info.get('title', '')[:40]}")
                    else:
                        filtered_infos.append(info)
                        save_to_archive(info)
            except Exception as e:
                logger.warning(f"   处理条目失败: {title[:30]}... ({e})")
        
        logger.info(f"   过滤结果: {len(important_infos)} 条重要, {len(filtered_infos)} 条普通")
    
    # ═══ Phase 3: 即时推送重要信息═══
    if modules.get('push') and important_infos:
        logger.info(f"📤 [Phase 3/4] 推送 {len(important_infos)} 条重要信息...")
        push_immediate_fn = modules['push']['push_immediate']
        for info in important_infos:
            try:
                push_immediate_fn(info)
            except Exception as e:
                logger.error(f"   推送失败: {info.get('title', '')[:30]}... ({e})")
    
    # ═══ Phase 4: 生成每日摘要═══
    if modules.get('ai'):
        logger.info("📊 [Phase 4/4] 生成每日摘要...")
        try:
            digest = modules['ai']['generate_daily_digest']()
            if digest:
                logger.info("✅ 每日摘要已生成并推送")
            else:
                logger.info("📭 今天没有新信息")
        except Exception as e:
            logger.error(f"❌ 生成每日摘要失败：{e}")
    
    # 汇总
    total = len(all_raw_infos)
    logger.info("")
    logger.info(f"━━ 运行汇总 ━━")
    logger.info(f"  采集: {total} 条 | 重要: {len(important_infos)} | 普通: {len(filtered_infos)}")
    logger.info(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)


def setup_scheduler():
    """
    配置定时任务（需要安装 apscheduler）
    
    🔴 修复：scheduler job 使用 lambda 延迟引用，
    避免 generate_daily_digest_if_needed 在定义时就被调用求值
    """
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
        
        # 每天晚上8点生成摘要（使用 lambda 延迟绑定）
        scheduler.add_job(
            lambda: _generate_evening_digest(import_modules()),
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


def _generate_evening_digest(modules):
    """晚间摘要任务的实际执行函数"""
    if modules and modules.get('ai'):
        try:
            digest = modules['ai']['generate_daily_digest']()
            if digest:
                logger.info("✅ 晚间摘要已生成并推送")
            else:
                logger.info("📭 今天没有新信息")
        except Exception as e:
            logger.error(f"❌ 晚间摘要生成失败：{e}")


def cleanup(config):
    """任务完成后清理资源

    - 清理 OCR 临时文件
    - 压缩旧日志
    - 统计并报告今日运行数据
    """
    logger.info("🧹 开始清理...")

    # 1. 清理 OCR 临时文件
    ocr_temp_dir = os.path.join(BASE_DIR, 'data', 'ocr_temp')
    if os.path.isdir(ocr_temp_dir):
        try:
            count = 0
            for fname in os.listdir(ocr_temp_dir):
                fpath = os.path.join(ocr_temp_dir, fname)
                # 清理超过 1 小时的临时文件
                if os.path.isfile(fpath):
                    age_hours = (time.time() - os.path.getmtime(fpath)) / 3600
                    if age_hours > 1:
                        os.remove(fpath)
                        count += 1
            logger.info(f"  OCR 临时文件清理: 删除 {count} 个过期文件")
        except Exception as e:
            logger.warning(f"  OCR 临时文件清理失败: {e}")
    else:
        logger.info("  OCR 临时目录不存在，跳过")

    # 2. 压缩旧日志（保留最近 7 天的原始日志）
    try:
        import gzip
        log_files = [f for f in os.listdir(LOG_DIR) if f.endswith('.log') and not f.endswith('.gz')]
        for fname in log_files:
            fpath = os.path.join(LOG_DIR, fname)
            age_days = (time.time() - os.path.getmtime(fpath)) / 86400
            if age_days > 7:
                gz_path = fpath + '.gz'
                if not os.path.exists(gz_path):
                    with open(fpath, 'rb') as fin:
                        with gzip.open(gz_path, 'wb') as fout:
                            fout.write(fin.read())
                    os.remove(fpath)
                    logger.info(f"  日志压缩: {fname} -> {fname}.gz")
    except Exception as e:
        logger.warning(f"  日志压缩失败: {e}")

    # 3. 统计今日运行数据
    today_str = datetime.now().strftime('%Y-%m-%d')
    archive_dir = os.path.join(BASE_DIR, 'data', 'archive')
    today_count = 0
    if os.path.isdir(archive_dir):
        for fname in os.listdir(archive_dir):
            if fname.startswith(today_str):
                today_count += 1
    logger.info(f"  今日归档数据: {today_count} 条")
    logger.info(f"  日志目录: {LOG_DIR}")
    logger.info("🧹 清理完成")


def run_all_pipeline():
    """完整的端到端自动化流程

    1. check_components  — 检查所有组件
    2. Phase 1: 数据采集（官网 + QQ群 + QQ空间增量）
    3. Phase 2: 过滤+分析
    4. Phase 3: 推送重要信息
    5. Phase 4: 每日摘要
    6. 知识库存储
    7. 清理临时文件
    """
    start_time = time.time()
    logger.info("━" * 60)
    logger.info("run_all — 完整端到端流程")
    logger.info(f"启动时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("━" * 60)

    # ── Step 1: 组件健康检查 ──
    logger.info("")
    logger.info("【Step 1/7】组件健康检查")
    health = check_components(CONFIG)
    critical_errors = [k for k, v in health.items()
                       if v['status'] == 'error' and k in ('napcat',)]
    if critical_errors:
        logger.warning(f"  关键组件异常: {', '.join(critical_errors)}，继续运行但部分功能不可用")

    # 导入模块（只导入一次，后续复用）
    modules = import_modules()

    # ── Step 2: Phase 1 — 数据采集 ──
    logger.info("")
    logger.info("【Step 2/7】数据采集")
    all_raw_infos = []

    # 官网爬虫
    if modules.get('scraper'):
        logger.info("🌐 采集合工大官网数据...")
        try:
            raw_items = modules['scraper']['quick_scrape']()
            all_raw_infos.extend(raw_items)
            logger.info(f"   官网采集到 {len(raw_items)} 条原始数据")
        except Exception as e:
            logger.error(f"❌ 官网采集失败：{e}")
    else:
        logger.warning("⚠️  官网爬虫模块未加载，跳过")

    # QQ群消息采集（NapCat API 轻量版）
    if modules.get('qq_group_collector'):
        qq_groups_cfg = CONFIG.get('qq_groups', [])
        if qq_groups_cfg:
            logger.info(f"💬 QQ群消息采集... 共 {len(qq_groups_cfg)} 个群")
            try:
                collector_mod = modules['qq_group_collector']
                collector = collector_mod.QQGroupCollector()
                # 获取群ID列表
                group_ids = [g['id'] for g in qq_groups_cfg if isinstance(g, dict) and 'id' in g]
                group_ids_int = [int(gid) for gid in group_ids if gid.isdigit()]
                if group_ids_int and hasattr(collector, 'collect_from_groups'):
                    qq_collected = collector.collect_from_groups(group_ids_int, count=50) or []
                    if isinstance(qq_collected, dict):
                        all_raw_infos.extend(qq_collected.get('messages', []))
                    elif isinstance(qq_collected, list):
                        all_raw_infos.extend(qq_collected)
                    logger.info(f"   QQ群采集到 {len(all_raw_infos)} 条消息")
                else:
                    logger.warning("   qq_group_collector 缺少 collect_from_groups 方法")
            except Exception as e:
                logger.error(f"❌ QQ群消息采集失败：{e}")

    # v2 新增：二手物品检测
    if modules.get('secondhand_detector'):
        try:
            shd_mod = modules['secondhand_detector']
            if hasattr(shd_mod, 'SecondHandDetector'):
                detector = shd_mod.SecondHandDetector()
                secondhand_config = CONFIG.get('secondhand_detection', {})
                if secondhand_config.get('enable', True):
                    logger.info("🔍 二手物品检测中...")
                    # 只筛查 general_notice 和 interest 类别的群消息
                    texts = []
                    for item in all_raw_infos:
                        txt = item.get('content') or item.get('title', '')
                        if txt and isinstance(txt, str):
                            texts.append(txt)
                    if texts:
                        sh_results = detector.detect_batch(texts)
                        sh_count = sum(1 for r in sh_results if r and r.get('is_secondhand'))
                        if sh_count > 0:
                            logger.info(f"   🏷️ 发现 {sh_count} 条疑似二手交易信息")
                            # 高置信度标记
                            high_value = detector.get_high_value(texts, min_confidence=0.7)
                            if high_value:
                                logger.info(f"   💎 其中 {len(high_value)} 条高价值二手信息")
        except Exception as e:
            logger.warning(f"⚠️ 二手物品检测失败: {e}")

    # QQ空间增量采集
    if modules.get('qzone'):
        logger.info("💬 QQ空间增量监控...")
        try:
            modules['qzone']()
        except Exception as e:
            logger.error(f"❌ QQ空间监控失败：{e}")

    logger.info(f"   采集汇总: {len(all_raw_infos)} 条原始数据")

    # ── Step 3: Phase 2 — 过滤+分析 ──
    logger.info("")
    logger.info("【Step 3/7】过滤+分析")
    filtered_infos = []
    important_infos = []

    if modules.get('filter') and all_raw_infos:
        logger.info(f"🔍 过滤分析 {len(all_raw_infos)} 条数据...")
        filter_fn = modules['filter']['filter_and_extract']
        is_important_fn = modules['filter'].get('is_important', lambda x: False)

        try:
            from archive import save_to_archive
        except ImportError:
            save_to_archive = None

        for item in all_raw_infos:
            title = item.get('title', '')
            content = item.get('content', title)
            try:
                is_pass, info = filter_fn(content)
                if is_pass:
                    info.update({
                        'source': item.get('source', '未知'),
                        'time': item.get('time', datetime.now().strftime('%Y-%m-%d')),
                        'url': item.get('url', ''),
                    })
                    if is_important_fn(content):
                        important_infos.append(info)
                        logger.info(f"   🚨 重要: {info.get('title', '')[:40]}")
                    else:
                        filtered_infos.append(info)
                        if save_to_archive:
                            save_to_archive(info)
            except Exception as e:
                logger.warning(f"   处理条目失败: {title[:30]}... ({e})")

        logger.info(f"   过滤结果: {len(important_infos)} 条重要, {len(filtered_infos)} 条普通")
    else:
        logger.warning("⚠️  无数据或 filter 模块未加载，跳过")

    # ── Step 4: Phase 3 — 推送重要信息 ──
    logger.info("")
    logger.info("【Step 4/7】推送重要信息")
    if modules.get('push') and important_infos:
        logger.info(f"📤 推送 {len(important_infos)} 条重要信息...")
        push_immediate_fn = modules['push']['push_immediate']
        for info in important_infos:
            try:
                push_immediate_fn(info)
            except Exception as e:
                logger.error(f"   推送失败: {info.get('title', '')[:30]}... ({e})")
    else:
        logger.info("📭 无重要信息需要推送")

    # ── Step 5: Phase 4 — 每日摘要（优先 Ollama，备选 ai_secretary）──
    logger.info("")
    logger.info("【Step 5/7】生成每日摘要")
    digest = None
    # v2: 优先尝试 Ollama
    ollama_mod = modules.get('ollama_manager')
    if ollama_mod and (important_infos or filtered_infos):
        try:
            if hasattr(ollama_mod, 'generate_summary'):
                all_texts = [i.get('title', '') + ': ' + (i.get('content', '') or '')[:300] for i in (important_infos + filtered_infos)[:30]]
                digest = ollama_mod.generate_summary(all_texts)
                if digest:
                    logger.info("✅ 每日摘要已生成（Ollama）")
        except Exception as e:
            logger.warning(f"⚠️ Ollama 摘要失败：{e}，回退到 ai_secretary")
    if not digest and modules.get('ai'):
        try:
            digest = modules['ai']['generate_daily_digest']()
            if digest:
                logger.info("✅ 每日摘要已生成（ai_secretary）")
            else:
                logger.info("📭 今天没有新信息")
        except Exception as e:
            logger.error(f"❌ 生成每日摘要失败：{e}")
    elif not digest:
        logger.info("📭 摘要模块均不可用，跳过")

    # ── Step 6: 知识库存储（v2: 使用 storage_manager 双写）──
    logger.info("")
    logger.info("【Step 6/7】知识库存储")
    all_storable = important_infos + filtered_infos
    # v2: 优先 storage_manager
    storage_mod = modules.get('storage_manager')
    if storage_mod and hasattr(storage_mod, 'StorageManager') and all_storable:
        try:
            sm = storage_mod.StorageManager()
            stored_count = 0
            for item in all_storable:
                result = sm.store(item, targets=["ima", "notion"])
                if result.get('ima', {}).get('success') or result.get('notion', {}).get('success'):
                    stored_count += 1
            logger.info(f"   双写存储成功: {stored_count}/{len(all_storable)} 条")
        except Exception as e:
            logger.error(f"❌ 双写存储失败：{e}，回退到 IMA 单写")
            # 回退到 IMA
            _store_via_ima(modules, all_storable)
    elif modules.get('knowledge_base') and all_storable:
        _store_via_ima(modules, all_storable)
    else:
        logger.info("📭 无数据需要存入知识库")

    # ── Step 7: 清理 ──
    logger.info("")
    logger.info("【Step 7/7】清理资源")
    cleanup(CONFIG)

    # ── 汇总 ──
    elapsed = time.time() - start_time
    logger.info("")
    logger.info("━" * 60)
    logger.info("run_all — 流程完成")
    logger.info(f"  采集: {len(all_raw_infos)} 条 | 重要: {len(important_infos)} | 普通: {len(filtered_infos)}")
    logger.info(f"  耗时: {elapsed:.1f}s")
    logger.info(f"  结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("━" * 60)


def _store_via_ima(modules, items):
    """v2: IMA 知识库单写（storage_manager 回退方案）"""
    kb = modules.get('knowledge_base')
    if not kb or not items:
        return 0
    try:
        if hasattr(kb, 'KnowledgeBaseManager'):
            mgr = kb.KnowledgeBaseManager()
            stored = 0
            for item in items:
                try:
                    title = item.get('title', '无标题')[:100]
                    content = item.get('content', str(item))[:1000]
                    if mgr.add_text_note(title, content):
                        stored += 1
                except Exception:
                    pass
            logger.info(f"   知识库存储成功: {stored}/{len(items)} 条（IMA 回退）")
            return stored
        elif hasattr(kb, 'batch_store'):
            stored = kb.batch_store(items)
            logger.info(f"   知识库存储成功: {stored} 条")
            return stored
        elif hasattr(kb, 'store'):
            success = sum(1 for item in items if _safe_store(kb, item))
            logger.info(f"   知识库存储成功: {success}/{len(items)} 条")
            return success
        else:
            logger.warning("   knowledge_base 缺少存储方法")
            return 0
    except Exception as e:
        logger.error(f"❌ 知识库存储失败：{e}")
        return 0


def _safe_store(kb, item):
    """安全地存储单条数据到知识库"""
    try:
        kb.store(item)
        return True
    except Exception:
        return False


def main():
    """主函数"""
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == 'test':
            test_configuration()
        
        elif command == 'push-test':
            logger.info("📧 测试QQ邮箱推送...")
            modules = import_modules()
            if modules.get('push'):
                result = modules['push']['push_test']()
                # push_test 返回 None，通过日志判断结果
            else:
                logger.error("❌ push 模块未加载")
        
        elif command == 'run':
            run_monitor()
        
        elif command == 'scrape':
            logger.info("🕷️ 测试官网爬虫...")
            modules = import_modules()
            if modules.get('scraper'):
                results = modules['scraper']['quick_scrape']()
                print(f"\n📊 采集结果: {len(results)} 条新信息\n")
                for i, item in enumerate(results[:20], 1):
                    print(f"  [{i}] {item.get('source', '?')} | {item.get('time', '?')}")
                    print(f"      {item.get('title', 'N/A')[:60]}")
                if len(results) > 20:
                    print(f"\n  ... 还有 {len(results) - 20} 条")
            else:
                logger.error("❌ scraper 模块未加载")

        elif command == 'wechat':
            logger.info("📱 测试微信公众号搜索...")
            modules = import_modules()
            if modules.get('wechat_enhanced'):
                results = modules['wechat_enhanced']()
                print(f"\n📊 微信公众号搜索结果: {len(results)} 条\n")
                for i, item in enumerate(results[:15], 1):
                    print(f"  [{i}] {item.get('account_name', '?')} | {item.get('time', '?')}")
                    print(f"      {item.get('title', 'N/A')[:55]}")
                    preview = item.get('content', '')[:60].replace('\n', ' ')
                    print(f"      摘要: {preview}...")
                if len(results) > 15:
                    print(f"\n  ... 还有 {len(results) - 15} 条")
            else:
                logger.error("❌ wechat_enhanced 模块未安装（需要 weixin_search_mcp）")
        
        elif command == 'scheduler':
            setup_scheduler()

        elif command == 'run_all':
            run_all_pipeline()

        elif command == 'check':
            # 检查所有组件状态
            logger.info("🔍 检查组件状态...")
            health = check_components(CONFIG)
            # 退出码：全部 ok 返回 0，否则返回 1
            all_ok = all(v['status'] in ('ok', 'none') for v in health.values())
            if not all_ok:
                sys.exit(1)

        elif command == 'help':
            show_help()
        
        else:
            logger.error(f"未知命令：{command}")
            show_help()
    
    else:
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
    print("  check       - 检查所有组件状态（NapCat/知识库/Cookie/OCR）")
    print("  scrape      - 测试官网爬虫")
    print("  wechat      - 测试微信公众号搜索")
    print("  push-test   - 测试QQ邮箱推送")
    print("  run         - 单次运行完整监控（采集→过滤→归档→推送）")
    print("  run_all     - 完整端到端流程（健康检查→采集→分析→推送→摘要→存储→清理）")
    print("  scheduler   - 定时运行（需要 apscheduler）")
    print("  help        - 显示此帮助")
    print()
    print("环境变量：")
    print("  SENDER_PASSWORD      - QQ邮箱SMTP授权码")
    print("  NAPCAT_TOKEN         - NapCat API Token")
    print("  NAPCAT_DEFAULT_RECEIVER - 默认QQ接收者")
    print()
    print("示例：")
    print("  python main.py test")
    print("  python main.py push-test")
    print("  python main.py run")
    print()
    print("=" * 60)


if __name__ == '__main__':
    main()
