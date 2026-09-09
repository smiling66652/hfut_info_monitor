"""
任务编排脚本 - 统一管理所有服务的启动和关闭
功能：
1. 任务开始前启动所有需要的服务（Ollama、NapCat等）
2. 运行所有数据采集任务
3. 任务结束后关闭所有服务
4. 支持保存登录状态，避免重复登录
5. 集成 OllamaManager 单例进行 Ollama 服务管理

架构说明（本地 vs 云端 AI 分工）：
┌─────────────────────────────────────────────────────┐
│  L1 规则引擎（无AI）                                  │
│  · EnhancedDetector 关键词匹配 / 分类                │
│  · filter.py 垃圾过滤 / 竞赛-二手-有用信息判定       │
│  · smart_digest 关键词评分（0-10分）                 │
├─────────────────────────────────────────────────────┤
│  L2 本地 Ollama（qwen:1.7b / qwen2.5:0.5b）        │
│  · 每日摘要生成（ollama_manager.generate_summary）   │
│  · 内容重要性评分（smart_digest.analyze_with_local） │
│  · 文本分类/聚类                                     │
├─────────────────────────────────────────────────────┤
│  L3 云端 DeepSeek API（回退/增强）                   │
│  · Ollama 不可用时的摘要回退                         │
│  · 高分内容深度分析（score≥7，预留）                  │
│  · 复杂推理任务（预留）                              │
└─────────────────────────────────────────────────────┘
"""

import json
import logging
import os
import sys
import time
import subprocess
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Set

logger = logging.getLogger('TaskOrchestrator')


class TaskOrchestrator:
    """任务编排器 - 统一管理任务执行与服务生命周期"""

    # 项目根目录（scripts/ 的上一级）
    PROJECT_ROOT = Path(__file__).parent.parent

    # NapCat 默认启动脚本路径（项目内置）
    DEFAULT_NAPCAT_SCRIPT = PROJECT_ROOT / "NapCat" / "NapCat_SystemQQ.bat"

    def __init__(self, config_path: str = None):
        """
        初始化任务编排器

        Args:
            config_path: 配置文件路径，默认为 ../config/config.json
        """
        # 提前加载 .env 到 os.environ，确保子进程继承环境变量
        self._ensure_dotenv_loaded()

        if config_path is None:
            config_path = self.PROJECT_ROOT / "config" / "config.json"

        self.config_path = Path(config_path)
        self.config = self._load_config()

        # 服务状态跟踪
        self.services: Dict[str, dict] = {
            'ollama': {'process': None, 'started_by_us': False},
            'napcat': {'process': None, 'started_by_us': False},
            'wechat_decrypt': {'process': None, 'started_by_us': False},
        }

        # 外部控制标志（由 bootstrap 设置）
        self.skip_ollama = False
        self.skip_napcat = False

        # OllamaManager 单例引用（延迟初始化）
        self._ollama_mgr = None

        # 登录状态保存路径
        self.login_state_dir = self.PROJECT_ROOT / "data" / "login_state"
        self.login_state_dir.mkdir(parents=True, exist_ok=True)

        logger.info("任务编排器初始化完成 (项目根目录: %s)", self.PROJECT_ROOT)
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    @staticmethod
    def _ensure_dotenv_loaded() -> None:
        """确保 .env 已加载到 os.environ（子进程继承用）"""
        try:
            from dotenv import load_dotenv
            env_path = Path(__file__).parent.parent / '.env'
            if env_path.exists():
                load_dotenv(env_path, override=True)
        except ImportError:
            # 如果没装 python-dotenv，手动解析 .env（简单版）
            env_path = Path(__file__).parent.parent / '.env'
            if env_path.exists():
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, _, val = line.partition('=')
                            key, val = key.strip(), val.strip().strip('"').strip("'")
                            if key and val:
                                os.environ[key] = val
    
    # ═══════════════════════════════════════════════════════════════
    # Ollama 管理（委托给 OllamaManager 单例）
    # ═══════════════════════════════════════════════════════════════

    @property
    def ollama_mgr(self):
        """延迟获取 OllamaManager 单例"""
        if self._ollama_mgr is None:
            try:
                from ollama_manager import get_manager
                self._ollama_mgr = get_manager()
                logger.debug("OllamaManager 单例已加载")
            except ImportError:
                logger.warning("ollama_manager 模块不可用，将使用内置 fallback")
                self._ollama_mgr = None
        return self._ollama_mgr

    def check_ollama(self) -> bool:
        """检查Ollama是否运行（优先用 OllamaManager）"""
        if self.ollama_mgr:
            return self.ollama_mgr.is_running()
        # Fallback: 直接 HTTP 探测
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=3)
            if response.status_code == 200:
                logger.info("✅ Ollama 已运行（fallback 探测）")
                return True
        except Exception:
            pass
        logger.info("⚠️ Ollama 未运行")
        return False

    def start_ollama(self) -> bool:
        """
        启动Ollama服务（优先通过 OllamaManager）
        """
        if self.check_ollama():
            logger.info("Ollama 已经在运行，跳过启动")
            return True

        logger.info("🚀 正在启动 Ollama...")

        if self.ollama_mgr:
            success = self.ollama_mgr.start()
            if success:
                self.services['ollama']['started_by_us'] = True
                logger.info("✅ Ollama 通过 OllamaManager 启动成功")
                return True
            else:
                logger.error("❌ OllamaManager 启动失败")
                return False

        # Fallback：手动启动
        try:
            result = subprocess.run(
                ["where", "ollama.exe"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                process = subprocess.Popen(
                    ["ollama.exe", "serve"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=True,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
                )
                self.services['ollama']['process'] = process
                self.services['ollama']['started_by_us'] = True

                for i in range(30):
                    if self.check_ollama():
                        logger.info("✅ Ollama 启动成功（手动模式，耗时 %ds）", i + 1)
                        return True
                    time.sleep(1)

                logger.error("❌ Ollama 启动超时（30s）")
                return False
            else:
                logger.error("❌ 未找到 ollama.exe，请确保已安装 Ollama")
                return False
        except Exception as e:
            logger.error("❌ 启动 Ollama 失败: %s", e)
            return False

    def stop_ollama(self) -> bool:
        """停止Ollama服务（仅当我们启动的才停止）"""
        if not self.services['ollama']['started_by_us']:
            logger.info("Ollama 非本脚本启动，跳过停止")
            return True

        logger.info("🛑 正在停止 Ollama...")

        # 优先用 OllamaManager 停止
        if self.ollama_mgr and self._ollama_mgr._process:
            try:
                self.ollama_mgr.stop()
                logger.info("✅ Ollama 已停止（via OllamaManager）")
                return True
            except Exception as e:
                logger.warning("OllamaManager 停止失败，尝试手动终止: %s", e)

        # Fallback: 手动终止
        try:
            process = self.services['ollama']['process']
            if process:
                process.terminate()
                process.wait(timeout=10)
                logger.info("✅ Ollama 已停止（子进程终止）")
                return True
        except Exception as e:
            logger.error("❌ 停止 Ollama 失败: %s", e)
            return False
    
    # ═══════════════════════════════════════════════════════════════
    # NapCat 管理
    # ═══════════════════════════════════════════════════════════════

    def _get_napcat_api_url(self) -> str:
        """从配置获取 NapCat HTTP API 地址（默认 localhost:3001）"""
        return self.config.get('napcat', {}).get(
            'api_url', 'http://localhost:3001'
        )

    def _get_napcat_script_path(self) -> Path:
        """
        获取 NapCat 启动脚本路径
        优先级：config.napcat.start_script > 项目内置路径
        """
        configured = self.config.get('napcat', {}).get('start_script')
        if configured:
            p = Path(configured)
            if p.exists():
                return p

        # 项目内置默认路径
        default = self.DEFAULT_NAPCAT_SCRIPT
        if default.exists():
            return default

        # 全部找不到，返回默认值（调用方会报错）
        logger.warning("NapCat 启动脚本未找到: config=%s, default=%s", configured, default)
        return default

    def _check_napcat_onebot11_ready(self) -> bool:
        """检查 OneBot11 HTTP API 是否就绪（端口 3001，QQ 已登录）"""
        api_url = self._get_napcat_api_url()
        try:
            response = requests.get(api_url, timeout=3)
            return response.status_code in (200, 404, 401)
        except Exception:
            return False

    def check_napcat(self) -> bool:
        """
        检查NapCat是否运行（OneBot11 HTTP API 可访问）
        注意：3001 是 OneBot11 HTTP API 端口（登录后才就绪）
             6099 是 NapCat WebUI 端口（启动后立即可用，登录前需要验证码）
        """
        api_url = self._get_napcat_api_url()
        try:
            response = requests.get(api_url, timeout=3)
            if response.status_code in (200, 404, 401):
                logger.info("✅ NapCat 已运行 (%s)", api_url)
                return True
        except Exception:
            pass

        # 检查 WebUI 端口（NapCat 进程在运行但未登录/需要验证码）
        try:
            response = requests.get("http://127.0.0.1:6099", timeout=3)
            logger.info("⚠️ NapCat 进程运行中，但 OneBot11 API 未就绪（可能需要验证码登录）")
            logger.info("   请访问 WebUI: http://127.0.0.1:6099/webui/web_login")
            return True  # 表示进程在运行，只是未完全就绪
        except Exception:
            pass

        logger.info("⚠️ NapCat 未运行 (检查 %s / :6099)", api_url)
        return False

    def start_napcat(self) -> bool:
        """启动NapCat服务"""
        if self.check_napcat():
            logger.info("NapCat 已经在运行，跳过启动")
            return True

        logger.info("🚀 正在启动 NapCat...")

        napcat_script = self._get_napcat_script_path()

        if not napcat_script.exists():
            logger.error("❌ NapCat 启动脚本不存在: %s", napcat_script)
            logger.error("   请确认 NapCat 已部署在项目目录下: %s", self.DEFAULT_NAPCAT_SCRIPT)
            return False

        # 直接运行 NapCatWinBootMain.exe（绕过 .bat 的 pause 和路径问题）
        napcat_exe = napcat_script.parent / "NapCat.44498.Shell" / "NapCatWinBootMain.exe"
        if not napcat_exe.exists():
            logger.error("❌ NapCatWinBootMain.exe 不存在: %s", napcat_exe)
            return False

        try:
            # 启动 NapCat（不阻塞主进程）
            # 用 DEVNULL 而非 PIPE：DETACHED_PROCESS 下管道缓冲区满会阻塞进程
            # 重定向到日志文件以便调试
            napcat_log = self.PROJECT_ROOT / "logs" / "napcat_startup.log"
            napcat_log.parent.mkdir(parents=True, exist_ok=True)
            napcat_log_fh = open(str(napcat_log), 'a')
            popen_kwargs = {
                'stdout': napcat_log_fh,
                'stderr': subprocess.STDOUT,
            }
            if os.name == 'nt':
                popen_kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

            # 显式传递 NapCat 所需的环境变量（密码登录、token等）
            napcat_env = os.environ.copy()
            # 自动化模式：跳过 .bat 中的 pause 等待
            napcat_env['NAPCAT_AUTO'] = '1'
            # 确保关键变量存在（从 .env 已加载到 os.environ）
            if 'NAPCAT_QUICK_PASSWORD' in os.environ:
                napcat_env['NAPCAT_QUICK_PASSWORD'] = os.environ['NAPCAT_QUICK_PASSWORD']
                logger.debug("   NAPCAT_QUICK_PASSWORD 已传递给子进程")
            else:
                logger.warning("   ⚠️ NAPCAT_QUICK_PASSWORD 未设置，NapCat 将回退到二维码登录")
            popen_kwargs['env'] = napcat_env

            process = subprocess.Popen(
                [str(napcat_exe), "<测试QQ号>"],
                cwd=str(napcat_exe.parent),
                **popen_kwargs
            )

            self.services['napcat']['process'] = process
            self.services['napcat']['started_by_us'] = True

            logger.info("   NapCat 进程已启动 (PID=%d), 等待服务就绪...", process.pid)

            # 等待启动（最多120秒 — NapCat + QQ 登录需要时间）
            login_hint_shown = False
            for i in range(120):
                if self._check_napcat_onebot11_ready():
                    logger.info("✅ NapCat 启动成功（耗时 %ds），OneBot11 API 就绪", i + 1)
                    return True

                # 每5秒检查是否 WebUI 已就绪但需要登录
                if i > 5 and i % 5 == 0:
                    try:
                        requests.get("http://127.0.0.1:6099", timeout=2)
                        if not login_hint_shown:
                            login_hint_shown = True
                            logger.warning("   ⚠️ NapCat WebUI 已就绪，但需完成 QQ 验证码/扫码登录")
                            logger.info("   📋 请访问 http://127.0.0.1:6099/webui/web_login 完成验证")
                            logger.info("   ⏳ 继续等待 QQ 登录完成...（最多 120s）")
                    except Exception:
                        pass

                time.sleep(1)

            logger.error("❌ NapCat 启动超时（120s），QQ 登录可能被安全风控拦截")
            logger.info("   📋 请手动访问 WebUI 完成验证: http://127.0.0.1:6099/webui/web_login")
            return False

        except Exception as e:
            logger.error("❌ 启动 NapCat 失败: %s", e)
            return False
    
    def stop_napcat(self) -> bool:
        """
        停止 NapCat 服务（仅当我们启动的才停止）
        策略：优先使用配置的 stop_script > 进程 terminate > Windows taskkill
        """
        if not self.services['napcat']['started_by_us']:
            logger.info("NapCat 非本脚本启动，跳过停止")
            return True

        logger.info("🛑 正在停止 NapCat...")

        try:
            # 策略1: 配置了 stop_script 且文件存在
            configured_stop = self.config.get('napcat', {}).get('stop_script')
            if configured_stop:
                stop_path = Path(configured_stop)
                if stop_path.exists():
                    result = subprocess.run(
                        [str(stop_path)],
                        capture_output=True, text=True, timeout=15
                    )
                    logger.info("✅ NapCat 已停止（via stop_script, rc=%d）", result.returncode)
                    # 重置状态
                    self.services['napcat']['started_by_us'] = False
                    self.services['napcat']['process'] = None
                    return True
                else:
                    logger.warning("配置的 stop_script 不存在: %s", configured_stop)

            # 策略2: 终止我们保存的进程句柄
            process = self.services['napcat'].get('process')
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning("NapCat 进程优雅终止超时，强制 kill")
                    process.kill()
                    process.wait(timeout=5)

                logger.info("✅ NapCat 已停止（进程终止）")
                self.services['napcat']['started_by_us'] = False
                self.services['napcat']['process'] = None
                return True

            # 策略3 (Windows): 通过进程名查找并终止 NapCat 相关进程
            if os.name == 'nt':
                import ctypes
                # 尝试用 taskkill 终止 NapCat Shell / QQ 相关子进程
                for proc_name in ['NapCat.Shell.exe', 'NapCat.Host.exe']:
                    try:
                        result = subprocess.run(
                            ['taskkill', '/F', '/IM', proc_name],
                            capture_output=True, text=True, timeout=5
                        )
                        if result.returncode == 0 or '成功' in result.stdout:
                            logger.info("   已终止 %s", proc_name)
                    except Exception as e:
                        logger.debug("终止 %s 失败: %s", proc_name, e)

                logger.info("✅ NapCat 已停止（Windows taskkill）")
                self.services['napcat']['started_by_us'] = False
                self.services['napcat']['process'] = None
                return True

            # Linux/macOS: 用 pkill
            else:
                result = subprocess.run(
                    ['pkill', '-f', 'NapCat'],
                    capture_output=True, text=True, timeout=5
                )
                logger.info("✅ NapCat 已停止（pkill）")
                self.services['napcat']['started_by_us'] = False
                self.services['napcat']['process'] = None
                return True

        except Exception as e:
            logger.error("❌ 停止 NapCat 失败: %s", e)
            return False

        logger.warning("⚠️ 无法确定如何停止 NapCat（无进程、无 stop_script）")
        return False
    
    def save_login_state(self, service_name: str, state_data: dict):
        """
        保存登录状态（避免重复登录）
        
        Args:
            service_name: 服务名称（如 'napcat', 'qq', 'wechat'）
            state_data: 登录状态数据（cookies、token等）
        """
        state_file = self.login_state_dir / f"{service_name}_login_state.json"
        
        try:
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
            
            logger.info("✅ 已保存 %s 登录状态", service_name)
            return True
        except Exception as e:
            logger.error("❌ 保存登录状态失败: %s", e)
            return False
    
    def load_login_state(self, service_name: str) -> Optional[dict]:
        """
        加载登录状态
        
        Args:
            service_name: 服务名称
            
        Returns:
            登录状态数据，失败返回None
        """
        state_file = self.login_state_dir / f"{service_name}_login_state.json"
        
        if not state_file.exists():
            logger.info("⚠️ 未找到 %s 登录状态", service_name)
            return None
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state_data = json.load(f)
            
            logger.info("✅ 已加载 %s 登录状态", service_name)
            return state_data
        except Exception as e:
            logger.error("❌ 加载登录状态失败: %s", e)
            return None
    
    def start_all_services(self) -> bool:
        """
        启动所有需要的服务
        """
        logger.info("🚀 开始启动所有服务...")
        
        success = True
        
        # 1. 启动Ollama
        if self.skip_ollama:
            logger.info("   ── Ollama: 跳过（用户指定）──")
        elif self.config.get('ai_processing', {}).get('enable', True):
            if not self.start_ollama():
                logger.warning("⚠️ Ollama 启动失败，AI处理功能将不可用")
                success = False
        
        # 2. 启动NapCat
        if self.skip_napcat:
            logger.info("   ── NapCat: 跳过（用户指定）──")
        elif self.config.get('qq_monitor', {}).get('enable', True):
            if not self.start_napcat():
                logger.warning("⚠️ NapCat 启动失败，QQ监控功能将不可用")
                success = False
        
        # 3. 启动其他服务（根据需要扩展）
        # ...
        
        if success:
            logger.info("✅ 所有服务启动完成")
        else:
            logger.warning("⚠️ 部分服务启动失败")
        
        return success
    
    def stop_all_services(self):
        """停止所有由本脚本启动的服务"""
        logger.info("🛑 开始停止所有服务...")
        
        # 1. 停止Ollama
        self.stop_ollama()
        
        # 2. 停止NapCat
        self.stop_napcat()
        
        # 3. 停止其他服务（根据需要扩展）
        # ...
        
        logger.info("✅ 所有服务已停止")
    
    def run_full_task(self) -> bool:
        """
        运行完整的任务流程
        1. 启动所有服务
        2. 运行数据采集
        3. 运行AI分析
        4. 生成并发送汇总邮件
        5. 关闭所有服务
        """
        logger.info("=" * 60)
        logger.info("开始执行完整任务流程")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        try:
            # Step 1: 启动所有服务
            logger.info("\n[Step 1/5] 启动所有服务...")
            self.start_all_services()
            
            # Step 2: 运行数据采集
            logger.info("\n[Step 2/5] 运行数据采集...")
            self._run_data_collection()
            
            # Step 3: 运行AI分析
            logger.info("\n[Step 3/5] 运行AI分析...")
            self._run_ai_analysis()
            
            # Step 4: 生成并发送汇总邮件
            logger.info("\n[Step 4/5] 生成并发送汇总邮件...")
            self._run_digest_generation()
            
            # Step 5: 同步到Notion和IMA
            logger.info("\n[Step 5/5] 同步到知识库...")
            self._sync_to_knowledge_base()
            
            elapsed_time = time.time() - start_time
            logger.info("\n" + "=" * 60)
            logger.info("✅ 任务流程执行完成，耗时 %.1f 秒", elapsed_time)
            logger.info("=" * 60)
            
            return True
        
        except Exception as e:
            logger.error("❌ 任务执行失败: %s", e)
            return False
        
        finally:
            # 无论成功失败，都关闭所有服务
            logger.info("\n[清理] 关闭所有服务...")
            self.stop_all_services()
    
    def _run_data_collection(self):
        """运行所有数据采集任务"""
        # 1. 合工大官网巡检
        logger.info("  [1/4] 合工大官网巡检...")
        try:
            import sys
            # 设置默认参数，避免argparse错误
            sys.argv = [sys.argv[0], '--mode', 'quick']
            from hfut_web_scraper import main as hfut_main
            hfut_main()
        except Exception as e:
            logger.warning("    ⚠️ 合工大官网巡检失败: %s", e)
        
        # 2. QQ群消息提取
        logger.info("  [2/4] QQ群消息提取...")
        try:
            from qq_group_monitor import monitor_qq_groups
            monitor_qq_groups()
        except Exception as e:
            logger.warning("    ⚠️ QQ群消息提取失败: %s", e)
        
        # 3. QQ动态提取
        logger.info("  [3/4] QQ动态提取...")
        try:
            from qzone_monitor import monitor_qzone
            monitor_qzone()
        except Exception as e:
            logger.warning("    ⚠️ QQ动态提取失败: %s", e)
        
        # 4. 微信公众号提取
        logger.info("  [4/4] 微信公众号提取...")
        try:
            from wechat_account_monitor import monitor_wechat_accounts
            monitor_wechat_accounts()
        except Exception as e:
            logger.warning("    ⚠️ 微信公众号提取失败: %s", e)
    
    def _run_ai_analysis(self):
        """运行AI分析"""
        try:
                from ai_secretary import run_secretary
                run_secretary()
                logger.info("  ✅ AI分析完成")
        except Exception as e:
            logger.warning("  ⚠️ AI分析失败: %s", e)
    
    def _run_digest_generation(self):
        """生成并发送汇总邮件"""
        try:
            from smart_digest import run_smart_digest
            run_smart_digest()
            logger.info("  ✅ 汇总邮件发送完成")
        except Exception as e:
            logger.warning("  ⚠️ 汇总邮件发送失败: %s", e)
    
    def _sync_to_knowledge_base(self):
        """同步到 IMA 知识库（Notion 已禁用，跳过）"""
        import json as _json
        from datetime import datetime as _dt

        # Notion 已在 config 中禁用 (enable: false)，跳过
        logger.info("  [1/2] Notion 同步已跳过（config 中已禁用）")

        # ── IMA 知识库同步 ──
        logger.info("  [2/2] 同步到 IMA 知识库...")
        try:
            from knowledge_base import KnowledgeBaseManager

            # 初始化知识库管理器
            kb_config = self.config.get('ima', {})
            kb_id = kb_config.get('kb_id') if kb_config else None
            kb = KnowledgeBaseManager(kb_id=kb_id)

            # 健康检查
            if not kb.health_check():
                logger.warning("    ⚠️ IMA 知识库连接失败，跳过同步")
                return

            # 创建/获取今日文件夹
            today_folder = KnowledgeBaseManager.today_folder_name()
            folder_id = kb.find_or_create_folder(today_folder)
            if folder_id:
                logger.info("    📁 已准备文件夹: %s (id=%s)", today_folder, folder_id[:20])
            else:
                logger.warning("    ⚠️ 文件夹创建失败，将写入根目录")

            sync_stats = {"url": 0, "text": 0, "skipped": 0, "failed": 0}

            # ── 数据源1：官网归档数据 (hfut_archive.json) ──
            archive_file = self.PROJECT_ROOT / "data" / "hfut_archive.json"
            if archive_file.exists():
                items = self._load_archive_items(archive_file)
                logger.info("    📋 加载官网归档: %d 条", len(items))

                for item in items:
                    url = item.get("url", "")
                    title = item.get("title", "")
                    content = item.get("content", "")

                    # 过滤掉模拟数据
                    if "【模拟数据】" in title or "【模拟数据】" in content:
                        sync_stats["skipped"] += 1
                        continue

                    # 有 URL 的条目通过 URL 导入（IMA 会自动抓取内容）
                    if url and url.startswith("http"):
                        if kb.add_from_url(url, title=title, folder_id=folder_id):
                            sync_stats["url"] += 1
                        else:
                            sync_stats["failed"] += 1
                    # 无 URL 但有内容的纯文本条目
                    elif content and len(content) > 10:
                        display_title = title or f"信息_{item.get('time', 'unknown')}"
                        if kb.add_text_note(display_title, content, folder_id=folder_id):
                            sync_stats["text"] += 1
                        else:
                            sync_stats["failed"] += 1

            # ── 数据源2：QQ空间采集结果 (qzone_feed_results.json) ──
            qzone_file = self.PROJECT_ROOT / "data" / "qzone_feed_results.json"
            if qzone_file.exists():
                try:
                    with open(qzone_file, 'r', encoding='utf-8') as f:
                        qzone_data = _json.load(f)
                    qzone_items = qzone_data if isinstance(qzone_data, list) else qzone_data.get("feeds", [])
                    logger.info("    📋 加载QQ空间动态: %d 条", len(qzone_items))

                    for item in qzone_items:
                        qz_content = item.get("content", "") or item.get("text", "")
                        qz_title = item.get("title", "") or f"QQ动态_{item.get('qq', '?')}"

                        if "【模拟数据】" in qz_content or "【模拟数据】" in qz_title:
                            sync_stats["skipped"] += 1
                            continue

                        if qz_content and len(str(qz_content)) > 5:
                            if kb.add_text_note(qz_title[:50], str(qz_content), folder_id=folder_id):
                                sync_stats["text"] += 1
                            else:
                                sync_stats["failed"] += 1
                except Exception as e:
                    logger.warning("    ⚠️ QQ空间数据加载失败: %s", e)

            # ── 尝试重试之前失败的缓存任务 ──
            retry_stats = kb.retry_pending()
            sync_stats["url"] += retry_stats.get("url_success", 0)
            sync_stats["failed"] += retry_stats.get("url_failed", 0)

            logger.info(
                "    ✅ IMA 同步完成 — URL导入:%d, 文本:%d, 跳过(模拟):%d, 失败:%d",
                sync_stats["url"], sync_stats["text"], sync_stats["skipped"], sync_stats["failed"],
            )

        except ImportError as e:
            logger.warning("    ⚠️ knowledge_base 模块导入失败: %s", e)
            logger.warning("       请确认 scripts/knowledge_base.py 存在且依赖完整")
        except Exception as e:
            logger.warning("    ⚠️ IMA 同步失败: %s", e)

    @staticmethod
    def _load_archive_items(archive_file: Path) -> list[dict]:
        """加载归档数据文件（支持 JSON Lines 格式）"""
        items = []
        try:
            with open(archive_file, 'r', encoding='utf-8') as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        items.append(item)
                    except json.JSONDecodeError:
                        logger.debug("      归档第 %d 行 JSON 解析跳过", line_no)
        except Exception as e:
            logger.warning("    ⚠️ 归档文件读取异常: %s", e)
        return items


def main():
    """主函数 - CLI入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='任务编排器 - 统一管理所有服务的启动和关闭')
    parser.add_argument('action', choices=['start', 'stop', 'restart', 'run', 'status'], 
                       help='操作：start（启动所有服务）/ stop（停止所有服务）/ restart（重启）/ run（运行完整任务）/ status（查看状态）')
    parser.add_argument('--config', '-c', default=None, help='配置文件路径')
    
    args = parser.parse_args()
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 创建任务编排器
    orchestrator = TaskOrchestrator(config_path=args.config)
    
    # 执行操作
    if args.action == 'start':
        orchestrator.start_all_services()
    elif args.action == 'stop':
        orchestrator.stop_all_services()
    elif args.action == 'restart':
        orchestrator.stop_all_services()
        time.sleep(2)
        orchestrator.start_all_services()
    elif args.action == 'run':
        orchestrator.run_full_task()
    elif args.action == 'status':
        print("\n" + "=" * 50)
        print("服务状态检查")
        print("=" * 50)
        print(f"Ollama: {'✅ 运行中' if orchestrator.check_ollama() else '❌ 未运行'}")
        print(f"NapCat: {'✅ 运行中' if orchestrator.check_napcat() else '❌ 未运行'}")
        print("=" * 50)


if __name__ == '__main__':
    main()
