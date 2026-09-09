"""
环境引导程序 (Environment Bootstrap)
=====================================
在主程序执行前后自动管理依赖服务的生命周期。

设计理念：
  用户只需运行本脚本 → 自动启动所需环境 → 执行主任务 → 自动清理
  无需手动管理 Ollama / NapCat 等服务的启停。

使用方式：
  # 方式1: 直接运行完整任务（推荐）
  python environment_bootstrap.py

  # 方式2: 只做环境检查（不执行任务）
  python environment_bootstrap.py --check-only

  # 方式3: 指定要执行的模式
  python environment_bootstrap.py --mode collect
  python environment_bootstrap.py --mode analyze
  python environment_bootstrap.py --mode all

  # 方式4: 跳过某些服务（调试用）
  python environment_bootstrap.py --skip-ollama
  python environment_bootstrap.py --skip-napcat

退出码：
  0 = 全部成功
  1 = 部分功能降级成功（如 Ollama 未启动但任务仍完成）
  2 = 致命失败

生命周期：
  ┌──────────┐    ┌──────────────┐    ┌────────────┐    ┌──────────┐
  │ 前置检查  │ -> │ 启动依赖服务 │ -> │ 执行主任务  │ -> │ 清理关闭 │
  └──────────┘    └──────────────┘    └────────────┘    └──────────┘
     preflight        bootstrap           execute          teardown
"""

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# 配置日志
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            PROJECT_ROOT / "logs" / "bootstrap.log",
            encoding="utf-8"
        ) if (PROJECT_ROOT / "logs").exists() else logging.NullHandler(),
    ],
)
logger = logging.getLogger("Bootstrap")


class EnvironmentBootstrap:
    """
    环境引导器 — 管理主程序执行前后的服务生命周期。

    职责：
      1. Preflight: 检查 Python 环境、依赖包、配置文件
      2. Bootstrap: 按需启动 Ollama / NapCat 等外部服务
      3. Execute: 调用 main_controller 执行指定模式
      4. Teardown: 关闭由本引导器启动的所有服务
    """

    # 各模式需要的服务依赖映射
    MODE_SERVICE_DEPS = {
        "collect": {"napcat": False, "ollama": False},
        "analyze": {"napcat": False, "ollama": True},
        "digest": {"napcat": False, "ollama": True},
        "sync": {"napcat": False, "ollama": False},
        "status": {"napcat": False, "ollama": False},
        "all": {"napcat": True, "ollama": True},
        "services-start": {"napcat": True, "ollama": True},
        "services-stop": {"napcat": False, "ollama": False},
    }

    def __init__(
        self,
        mode: str = "all",
        skip_ollama: bool = False,
        skip_napcat: bool = False,
        check_only: bool = False,
    ):
        self.mode = mode
        self.skip_ollama = skip_ollama
        self.skip_napcat = skip_napcat
        self.check_only = check_only

        # 提前加载 .env 到 os.environ（子进程和 orchestrator 均依赖）
        self._ensure_dotenv_loaded()

        # 延迟导入的 orchestrator 引用
        self._orchestrator = None

        # 运行状态跟踪
        self.preflight_results: dict = {}
        self.bootstrap_results: dict = {}
        self.exit_code = 0
        self._interrupted = False

        # 注册信号处理（优雅中断）
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ── 属性：延迟加载 TaskOrchestrator ──

    @property
    def orch(self):
        if self._orchestrator is None:
            from task_orchestrator import TaskOrchestrator
            self._orchestrator = TaskOrchestrator()
            # 传递 skip 标志给 orchestrator，确保 execute 阶段也尊重
            self._orchestrator.skip_ollama = self.skip_ollama
            self._orchestrator.skip_napcat = self.skip_napcat
        return self._orchestrator

    @staticmethod
    def _ensure_dotenv_loaded() -> None:
        """确保 .env 已加载到 os.environ"""
        try:
            from dotenv import load_dotenv
            env_path = PROJECT_ROOT / '.env'
            if env_path.exists():
                load_dotenv(env_path, override=True)
        except ImportError:
            env_path = PROJECT_ROOT / '.env'
            if env_path.exists():
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, _, val = line.partition('=')
                            key, val = key.strip(), val.strip().strip('"').strip("'")
                            if key and val:
                                os.environ[key] = val

    # ── 信号处理 ──

    def _signal_handler(self, signum, frame):
        """优雅处理 Ctrl+C / SIGTERM"""
        logger.warning("\n收到中断信号 (%d)，正在清理...", signum)
        self._interrupted = True
        self.teardown()
        sys.exit(130)  # 128 + SIGINT

    # ════════════════════════════════════════════════════════
    # Phase 1: Pre-flight Check（前置检查）
    # ════════════════════════════════════════════════════════

    def preflight(self) -> bool:
        """
        前置环境检查
        检查项：Python版本、关键依赖、配置文件、数据目录
        """
        logger.info("=" * 60)
        logger.info("🔍 Phase 1/3: 前置环境检查 (Pre-flight)")
        logger.info("=" * 60)

        all_ok = True

        # 1.1 Python 版本
        py_version = sys.version_info
        ok = py_version >= (3, 8)
        self.preflight_results["python"] = {
            "ok": ok,
            "detail": "Python %d.%d.%d" % (py_version.major, py_version.minor, py_version.micro),
        }
        if ok:
            logger.info("  ✅ Python 版本: %d.%d.%d", py_version.major, py_version.minor, py_version.micro)
        else:
            logger.error("  ❌ Python 版本过低 (>=3.8 required): %d.%d.%d", py_version.major, py_version.minor, py_version.micro)
            all_ok = False

        # 1.2 关键依赖模块
        critical_modules = [
            ("requests", "HTTP 请求库"),
            ("json", "JSON 处理（标准库）"),
        ]
        optional_modules = [
            ("ollama_manager", "Ollama 本地 AI 管理"),
            ("smart_digest", "智能摘要生成"),
            ("qq_group_monitor", "QQ群消息监控"),
            ("qzone_monitor", "QQ空间监控"),
        ]

        logger.info("  📦 检查关键依赖...")
        for mod_name, desc in critical_modules:
            try:
                __import__(mod_name)
                logger.info("    ✅ %s (%s)", mod_name, desc)
            except ImportError:
                logger.error("    ❌ %s (%s) — 缺失！", mod_name, desc)
                all_ok = False

        logger.info("  📦 检查可选依赖...")
        for mod_name, desc in optional_modules:
            try:
                __import__(mod_name)
                logger.info("    ✅ %s (%s)", mod_name, desc)
            except ImportError:
                logger.warning("    ⚠️ %s (%s) — 缺失，相关功能不可用", mod_name, desc)

        # 1.3 配置文件
        config_path = PROJECT_ROOT / "config" / "config.json"
        if config_path.exists():
            logger.info("  ✅ 配置文件: %s", config_path)
            self.preflight_results["config"] = {"ok": True, "detail": str(config_path)}
        else:
            logger.error("  ❌ 配置文件缺失: %s", config_path)
            self.preflight_results["config"] = {"ok": False, "detail": str(config_path)}
            all_ok = False

        # 1.4 .env 文件（敏感信息）
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            logger.info("  ✅ .env 文件存在（含敏感配置）")
            self.preflight_results["env"] = {"ok": True}
        else:
            logger.warning("  ⚠️ .env 不存在 — 邮件密码/NapCat token 等将使用 config.json 中的值")
            self.preflight_results["env"] = {"ok": False, "warn": True}

        # 1.5 数据目录
        data_dir = PROJECT_ROOT / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("  ✅ 数据目录: %s", data_dir)

        logs_dir = PROJECT_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # 汇总
        logger.info("")
        if all_ok:
            logger.info("✅ Pre-flight 通过，所有关键条件满足")
        else:
            logger.warning("⚠️ Pre-flight 存在问题，部分功能可能不可用")

        return all_ok

    # ════════════════════════════════════════════════════════
    # Phase 2: Bootstrap（启动依赖服务）
    # ════════════════════════════════════════════════════════

    def bootstrap(self) -> bool:
        """
        根据当前 mode 的依赖声明，按需启动外部服务。
        仅启动当前模式需要的、且用户未跳过的服务。
        check_only 模式下：只检测状态，不启动任何服务。
        """
        logger.info("")
        logger.info("=" * 60)
        phase_name = "服务状态检查 (Check-only)" if self.check_only else "启动依赖服务 (Bootstrap)"
        logger.info("🚀 Phase 2/3: %s", phase_name)
        logger.info("=" * 60)

        deps = self.MODE_SERVICE_DEPS.get(self.mode, {"ollama": True, "napcat": True})
        needs_ollama = deps.get("ollama", False) and not self.skip_ollama
        needs_napcat = deps.get("napcat", False) and not self.skip_napcat

        logger.info("  当前模式: %s%s", self.mode, " [check-only]" if self.check_only else "")
        logger.info("  服务需求: ollama=%s, napcat=%s", needs_ollama, needs_napcat)
        if self.skip_ollama or self.skip_napcat:
            logger.info("  跳过选项: ollama=%s, napcat=%s", self.skip_ollama, self.skip_napcat)

        all_ok = True

        # ── Ollama ──
        if needs_ollama or (self.check_only and not self.skip_ollama):
            logger.info("")
            label = "Ollama (本地 AI)"
            logger.info("  ── %s ──", label)
            is_running = self.orch.check_ollama()
            if self.check_only:
                # check-only: 只报告状态
                if is_running:
                    logger.info("  ✅ Ollama 正在运行")
                    self.bootstrap_results["ollama"] = "running"
                else:
                    logger.info("  ⚠️ Ollama 未运行（check-only 不自动启动）")
                    self.bootstrap_results["ollama"] = "not_running"
            else:
                # normal: 未运行则启动
                if is_running:
                    logger.info("  ✅ Ollama 已在运行，无需启动")
                    self.bootstrap_results["ollama"] = "already_running"
                else:
                    success = self.orch.start_ollama()
                    if success:
                        self.bootstrap_results["ollama"] = "started"
                        logger.info("  ✅ Ollama 启动成功")
                    else:
                        self.bootstrap_results["ollama"] = "failed"
                        logger.warning("  ⚠️ Ollama 启动失败！AI 功能将降级为云端 API 或纯规则引擎")
                        all_ok = False
        elif self.skip_ollama:
            logger.info("")
            logger.info("  ── Ollama: 跳过（用户指定）──")
            self.bootstrap_results["ollama"] = "skipped"
        elif not needs_ollama:
            logger.info("")
            logger.info("  ── Ollama: 当前模式不需要 ──")
            self.bootstrap_results["ollama"] = "not_needed"

        # ── NapCat ──
        if needs_napcat or (self.check_only and not self.skip_napcat):
            logger.info("")
            label = "NapCat (QQ消息推送)"
            logger.info("  ── %s ──", label)
            is_running = self.orch.check_napcat()
            if self.check_only:
                if is_running:
                    logger.info("  ✅ NapCat 正在运行")
                    self.bootstrap_results["napcat"] = "running"
                else:
                    logger.info("  ⚠️ NapCat 未运行（check-only 不自动启动）")
                    self.bootstrap_results["napcat"] = "not_running"
            else:
                if is_running:
                    logger.info("  ✅ NapCat 已在运行，无需启动")
                    self.bootstrap_results["napcat"] = "already_running"
                else:
                    success = self.orch.start_napcat()
                    if success:
                        self.bootstrap_results["napcat"] = "started"
                        logger.info("  ✅ NapCat 启动成功")
                    else:
                        self.bootstrap_results["napcat"] = "failed"
                        logger.warning("  ⚠️ NapCat 启动失败！QQ 推送功能不可用")
                        all_ok = False
        elif self.skip_napcat:
            logger.info("")
            logger.info("  ── NapCat: 跳过（用户指定） ──")
            self.bootstrap_results["napcat"] = "skipped"
        else:
            logger.info("")
            logger.info("  ── NapCat: 当前模式不需要 ──")
            self.bootstrap_results["napcat"] = "not_needed"

        logger.info("")
        if self.check_only:
            logger.info("✅ 状态检查完成")
        elif all_ok:
            logger.info("✅ Bootstrap 完成，所有需要的服务已就绪")
        else:
            logger.warning("⚠️ Bootstrap 部分失败，将以降级模式继续")

        return all_ok

    # ════════════════════════════════════════════════════════
    # Phase 3: Execute（执行主任务）
    # ════════════════════════════════════════════════════════

    def execute(self) -> bool:
        """调用 main_controller 执行指定的模式"""
        if self.check_only:
            logger.info("")
            logger.info("=" * 60)
            logger.info("📋 Phase 3/3: 仅检查模式，跳过执行 (--check-only)")
            logger.info("=" * 60)
            return True

        logger.info("")
        logger.info("=" * 60)
        logger.info("⚡ Phase 3/3: 执行主任务 (Execute): %s", self.mode)
        logger.info("=" * 60)

        try:
            import main_controller as mc

            # 根据模式路由到对应函数
            mode_method_map = {
                "all": mc.run_all_mode,
                "collect": mc.run_collect_mode,
                "analyze": mc.run_analyze_mode,
                "digest": mc.run_digest_mode,
                "sync": mc.run_sync_mode,
                "status": mc.run_status_mode,
            }

            method = mode_method_map.get(self.mode)
            if method is None:
                logger.error("❌未知模式: %s", self.mode)
                return False

            start = time.time()
            success = method(self.orch)
            elapsed = time.time() - start

            if success:
                logger.info("✅ 任务执行完成（%.1f 秒）", elapsed)
            else:
                logger.warning("⚠️ 任务执行返回失败状态（%.1f 秒）", elapsed)

            return success

        except Exception as e:
            logger.error("❌ 任务执行异常: %s", e, exc_info=True)
            return False

    # ════════════════════════════════════════════════════════
    # Phase 4: Teardown（清理关闭）
    # ════════════════════════════════════════════════════════

    def teardown(self):
        """关闭由本引导器启动的所有服务（check_only 模式跳过）"""
        if self.check_only:
            logger.info("")
            logger.info("🧹 Cleanup: check-only 模式，无需清理")
            return

        logger.info("")
        logger.info("=" * 60)
        logger.info("🧹 Cleanup: 关闭依赖服务 (Teardown)")
        logger.info("=" * 60)

        try:
            self.orch.stop_all_services()
            logger.info("✅ 所有已启动的服务已关闭")
        except Exception as e:
            logger.error("❌ 清理过程中出错: %s", e)

    # ── 主入口流程 ──

    def run(self) -> int:
        """执行完整的四阶段生命周期"""
        total_start = time.time()

        # Phase 1: 前置检查
        preflight_ok = self.preflight()

        if not preflight_ok and not self.check_only:
            logger.warning("前置检查未全部通过，是否继续？(y/n)")
            # 非交互环境下自动继续
            if not sys.stdin.isatty():
                logger.info("非交互环境，自动继续...")

        # Phase 2: 启动服务
        bootstrap_ok = self.bootstrap()

        # 判断是否需要设置降级退出码
        if not bootstrap_ok:
            self.exit_code = 1  # 部分降级

        # Phase 3: 执行任务
        exec_ok = self.execute()
        if not exec_ok and self.exit_code == 0:
            self.exit_code = 1

        # Phase 4: 清理（始终执行）
        self.teardown()

        total_elapsed = time.time() - total_start

        # 最终报告
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 Environment Bootstrap 最终报告")
        logger.info("=" * 60)
        logger.info("  模式:       %s", self.mode)
        logger.info("  Pre-flight: %s", "通过" if preflight_ok else "存在问题")
        logger.info("  Bootstrap:  %s", self.bootstrap_results)
        logger.info("  执行结果:   %s", "成功" if exec_ok else "失败")
        logger.info("  总耗时:     %.1f 秒", total_elapsed)
        logger.info("  退出码:     %d", self.exit_code)
        logger.info("=" * 60)

        return self.exit_code


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="合工大信息监控 v4.0 - 环境引导程序",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python environment_bootstrap.py                 # 完整运行（采集+分析+摘要+同步）
  python environment_bootstrap.py --mode collect   # 仅采集
  python environment_bootstrap.py --check-only     # 仅检查环境和依赖
  python environment_bootstrap.py --skip-ollama    # 跳过 Ollama 启动
  python environment_bootstrap.py --skip-napcat    # 跳过 NapCat 启动
""",
    )

    parser.add_argument(
        "--mode", "-m",
        default="all",
        choices=["all", "collect", "analyze", "digest", "sync", "status",
                 "services-start", "services-stop"],
        help="执行模式（默认: all）",
    )
    parser.add_argument(
        "--skip-ollama",
        action="store_true",
        help="跳过 Ollama 启动/检查",
    )
    parser.add_argument(
        "--skip-napcat",
        action="store_true",
        help="跳过 NapCat 启动/检查",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只做环境检查，不执行任何任务",
    )

    args = parser.parse_args()

    bootstrap = EnvironmentBootstrap(
        mode=args.mode,
        skip_ollama=args.skip_ollama,
        skip_napcat=args.skip_napcat,
        check_only=args.check_only,
    )

    exit_code = bootstrap.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
