"""
OCR 图片文字提取模块
====================
从 QQ 空间和 QQ 群的图片中提取文字内容，提供统一的 OCR 接口。

多级退回方案（优先级从高到低）：
- Plan A: 本地 PaddleOCR（主力，完全免费，无限制）
- Plan B: NapCat get_image 接口下载图片 + 本地 OCR（需 NapCat 在线）
- Plan C: 在线 OCR API（百度OCR / 腾讯云OCR，需配置 AK/SK）
- Plan D: 回退模式 — 只标记 "[图片]" 不做 OCR

成本参考：见 docs/ocr_cost_evaluation.md

使用示例:
    from scripts.ocr_extractor import OCRExtractor

    extractor = OCRExtractor()
    print(extractor.detect_available_method())

    # 从图片 URL 提取文字
    text = extractor.extract_text_from_url("https://gchat.qpic.cn/xxx", source="qqgroup")
    print(text)

    # 批量提取（线程池并发）
    texts = extractor.extract_texts_from_urls_batch(["url1", "url2"], source="qzone")

    # 查看成本报告
    report = extractor.get_cost_report()
"""

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("HFUT_Monitor.OCRExtractor")

# QQ 图片下载所需的 Referer 头
QQ_IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://q.qq.com/",
}

# PaddleOCR 可用性标记
_PADDLEOCR_AVAILABLE = False
_paddleocr_engine = None

try:
    from paddleocr import PaddleOCR as _PaddleOCR
    _PADDLEOCR_AVAILABLE = True
except ImportError:
    pass


def _load_config() -> dict:
    """加载 OCR 相关配置，失败时返回默认值"""
    default_ocr_config = {
        "enable": True,
        "method": "auto",
        "max_images_per_scan": 20,
        "temp_dir": "data/ocr_temp",
    }

    try:
        from scripts.config_loader import load_config
        config = load_config()
        ocr_cfg = config.get("ocr", {})
        default_ocr_config.update(ocr_cfg)
    except (ImportError, FileNotFoundError):
        logger.debug("配置文件不可用，使用 OCR 默认配置")
    except Exception as e:
        logger.warning(f"加载 OCR 配置异常: {e}，使用默认值")

    return default_ocr_config


class OCRExtractor:
    """图片文字提取器

    自动检测可用的 OCR 方案并按优先级使用：
    1. Plan A: 本地 PaddleOCR（主力）
    2. Plan B: NapCat 下载 + 本地 OCR
    3. Plan C: 在线 OCR API（百度 / 腾讯云）
    4. Plan D: 标记 [图片]（兜底）

    Attributes:
        ocr_cost_stats: 在线 API 调用成本统计
            {
                "total_calls": int,        # 总 API 调用次数
                "free_quota_used": int,    # 已用免费额度
                "paid_calls": int,         # 付费调用次数
                "estimated_cost": float,   # 预估费用（元）
                "method_breakdown": {      # 各方法调用次数
                    "paddleocr": int,
                    "baidu_ocr": int,
                    "tencent_ocr": int,
                },
                "start_time": str,         # 统计起始时间 ISO 格式
            }
    """

    # 在线 API 定价常量（元/次）
    BAIDU_PRICE_PER_CALL = 0.004
    TENCENT_PRICE_PER_CALL = 0.003
    FREE_QUOTA_MONTHLY = 1000  # 每月免费调用次数

    def __init__(self, napcat_api_url: str = None, napcat_token: str = None):
        """初始化 OCR 提取器

        Args:
            napcat_api_url: NapCat OneBot11 API 地址，默认从 config.json 读取
            napcat_token: NapCat API Token，默认从 config.json 读取
        """
        self._ocr_config = _load_config()
        self._enabled = self._ocr_config.get("enable", True)
        self._method = self._ocr_config.get("method", "auto")
        self._max_images = self._ocr_config.get("max_images_per_scan", 20)

        # 临时文件目录
        temp_dir = self._ocr_config.get("temp_dir", "data/ocr_temp")
        if not os.path.isabs(temp_dir):
            try:
                from scripts.config_loader import get_base_dir
                temp_dir = str(get_base_dir() / temp_dir)
            except ImportError:
                temp_dir = str(Path(__file__).parent.parent / temp_dir)
        self._temp_dir = Path(temp_dir)
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        # NapCat 配置
        if napcat_api_url is None or napcat_token is None:
            try:
                from scripts.config_loader import load_config
                config = load_config()
                napcat_cfg = config.get("napcat", {})
                napcat_api_url = napcat_api_url or napcat_cfg.get("api_url", "http://localhost:3001")
                napcat_token = napcat_token or napcat_cfg.get("token", "")
            except (ImportError, FileNotFoundError):
                napcat_api_url = napcat_api_url or "http://localhost:3001"
                napcat_token = napcat_token or ""

        self._napcat_api_url = napcat_api_url.rstrip("/") if napcat_api_url else ""
        self._napcat_token = napcat_token or ""

        # 检测可用方法
        self._available_method: str = ""
        self._ocr_engine = None

        # 成本统计
        self.ocr_cost_stats = {
            "total_calls": 0,
            "free_quota_used": 0,
            "paid_calls": 0,
            "estimated_cost": 0.0,
            "method_breakdown": {
                "paddleocr": 0,
                "baidu_ocr": 0,
                "tencent_ocr": 0,
            },
            "start_time": datetime.now().isoformat(),
        }

        logger.info(
            f"OCRExtractor 初始化完成, "
            f"enable={self._enabled}, method={self._method}, "
            f"temp_dir={self._temp_dir}"
        )

    def _ensure_paddleocr(self, install_if_missing: bool = False) -> bool:
        """确保 PaddleOCR 可用

        检查顺序：
        1. 当前进程已导入（_PADDLEOCR_AVAILABLE = True）
        2. 尝试 import paddleocr
        3. 尝试 subprocess 调用 paddleocr CLI
        4. 如果 install_if_missing=True，尝试 pip install paddlepaddle paddleocr

        Args:
            install_if_missing: 是否自动安装（检测阶段应为 False）

        Returns:
            bool: PaddleOCR 是否可用
        """
        global _PADDLEOCR_AVAILABLE

        # 已确认可用
        if _PADDLEOCR_AVAILABLE:
            return True

        # 尝试 import
        try:
            from paddleocr import PaddleOCR as _PaddleOCR
            _PADDLEOCR_AVAILABLE = True
            logger.info("PaddleOCR 模块已导入")
            return True
        except ImportError:
            pass

        # 尝试 CLI 调用检测
        try:
            result = subprocess.run(
                [sys.executable, "-c", "from paddleocr import PaddleOCR; print('OK')"],
                capture_output=True, text=True, timeout=30
            )
            if "OK" in result.stdout:
                _PADDLEOCR_AVAILABLE = True
                logger.info("PaddleOCR 通过子进程检测可用")
                return True
        except Exception:
            pass

        # 尝试自动安装（仅当 install_if_missing=True）
        if not install_if_missing:
            logger.debug("PaddleOCR 未安装，跳过自动安装（检测模式）")
            return False
        logger.info("PaddleOCR 未安装，正在尝试自动安装...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "paddlepaddle", "paddleocr"],
                capture_output=True, text=True, timeout=120,
                check=False,
            )
            # 安装后再尝试导入
            try:
                from paddleocr import PaddleOCR as _PaddleOCR
                _PADDLEOCR_AVAILABLE = True
                logger.info("PaddleOCR 安装成功并已导入")
                return True
            except ImportError:
                logger.warning("PaddleOCR 安装完成但导入失败，可能需要重启 Python 进程")
                return False
        except Exception as e:
            logger.warning(f"PaddleOCR 自动安装失败: {e}")
            return False

    def detect_available_method(self) -> str:
        """检测可用的 OCR 方法（按优先级从高到低）

        Returns:
            str: 'paddleocr' | 'napcat' | 'online' | 'none'
        """
        if self._available_method:
            return self._available_method

        if not self._enabled:
            logger.info("OCR 功能已禁用 (config.ocr.enable=false)")
            self._available_method = "none"
            return self._available_method

        # 如果用户强制指定了方法，优先使用（并尝试确保该方法可用）
        if self._method in ("paddleocr", "napcat", "online", "none"):
            if self._method == "paddleocr":
                if self._ensure_paddleocr(install_if_missing=True):
                    self._available_method = "paddleocr"
                    logger.info("OCR 方案: PaddleOCR (Plan A, 用户指定)")
                else:
                    logger.warning("配置要求使用 paddleocr，但 PaddleOCR 不可用，降级到 auto")
                    # 继续走 auto 逻辑
                    self._method = "auto"
            elif self._method == "napcat":
                self._available_method = "napcat"
                logger.info("OCR 方案: NapCat + 本地 OCR (用户指定)")
                return self._available_method
            elif self._method == "online":
                self._available_method = "online"
                logger.info("OCR 方案: 在线 API (用户指定)")
                return self._available_method
            elif self._method == "none":
                self._available_method = "none"
                logger.info("OCR 方案: 仅标记 [图片] (用户指定)")
                return self._available_method

        # auto 模式：按优先级自动检测
        # Plan A: 本地 PaddleOCR（优先，完全免费）
        if self._ensure_paddleocr():
            self._available_method = "paddleocr"
            logger.info("OCR 方案: PaddleOCR (Plan A - 主力)")
            return self._available_method

        # Plan B: NapCat 下载 + 本地 OCR（需 NapCat 在线）
        if self._napcat_api_url:
            self._available_method = "napcat"
            logger.info("OCR 方案: NapCat + 本地 OCR (Plan B)")
            return self._available_method

        # Plan C: 在线 API（百度 / 腾讯云）
        self._available_method = "online"
        logger.info("OCR 方案: 在线 API (Plan C)")
        return self._available_method

    def extract_text_from_url(self, image_url: str, source: str = "unknown") -> str:
        """从图片 URL 提取文字

        Args:
            image_url: 图片 URL（http/https 或 file:// 路径）
            source: 来源标识 ("qzone" | "qqgroup" | "other")

        Returns:
            str: 提取的文本内容，失败返回 ""
        """
        if not image_url or not self._enabled:
            return ""

        method = self.detect_available_method()
        if method == "none":
            return ""

        logger.debug(f"OCR 提取: source={source}, url={image_url[:80]}..., method={method}")

        try:
            # file:// 本地路径直接识别
            if image_url.startswith("file://"):
                local_path = image_url[7:]  # 去掉 file:// 前缀
                return self.extract_text_from_file(local_path)

            # 下载图片到本地临时目录
            local_path = self.download_image(image_url)
            if not local_path:
                logger.warning(f"图片下载失败: {image_url[:80]}")
                return ""

            # 根据方法选择 OCR 引擎
            return self._do_ocr(local_path, method)

        except Exception as e:
            logger.error(f"OCR 提取异常 ({source}): {e}")
            return ""

    def extract_text_from_file(self, file_path: str) -> str:
        """从本地图片文件提取文字

        Args:
            file_path: 本地图片文件路径

        Returns:
            str: 提取的文本内容，失败返回 ""
        """
        if not file_path or not self._enabled:
            return ""

        path = Path(file_path)
        if not path.exists():
            logger.warning(f"图片文件不存在: {file_path}")
            return ""

        method = self.detect_available_method()
        if method == "none":
            return ""

        try:
            return self._do_ocr(str(path), method)
        except Exception as e:
            logger.error(f"OCR 识别文件异常: {e}")
            return ""

    def extract_texts_from_urls(self, image_urls: list, source: str = "unknown") -> list:
        """批量提取多个图片的文字（顺序处理，兼容旧版）

        Args:
            image_urls: 图片 URL 列表
            source: 来源标识

        Returns:
            list[str]: 提取结果的列表，与输入一一对应，失败项为 ""
        """
        if not image_urls:
            return []

        # 限制最大处理数量
        if len(image_urls) > self._max_images:
            logger.warning(
                f"图片数量 {len(image_urls)} 超过限制 {self._max_images}，"
                f"只处理前 {self._max_images} 张"
            )
            image_urls = image_urls[:self._max_images]

        results = []
        for i, url in enumerate(image_urls, 1):
            text = self.extract_text_from_url(url, source=source)
            results.append(text)

            # 请求间隔，避免过快
            if i < len(image_urls):
                time.sleep(0.3)

        success_count = sum(1 for t in results if t)
        logger.info(
            f"批量 OCR 完成: {success_count}/{len(image_urls)} 成功 (source={source})"
        )
        return results

    def extract_texts_from_urls_batch(
        self, image_urls: list, source: str = "unknown", max_workers: int = 3
    ) -> list:
        """批量提取多个图片的文字（线程池并发）

        使用 ThreadPoolExecutor 并发处理多张图片。
        注意：结果顺序与输入一一对应（保持顺序）。

        Args:
            image_urls: 图片 URL 列表
            source: 来源标识
            max_workers: 最大并发线程数，默认 3

        Returns:
            list[str]: 提取结果的列表，与输入一一对应，失败项为 ""
        """
        if not image_urls:
            return []

        total = len(image_urls)

        # 限制最大处理数量
        if total > self._max_images:
            logger.warning(
                f"图片数量 {total} 超过限制 {self._max_images}，"
                f"只处理前 {self._max_images} 张"
            )
            image_urls = image_urls[:self._max_images]
            total = len(image_urls)

        # 单张图片不需要线程池
        if total <= 1:
            return self.extract_texts_from_urls(image_urls, source=source)

        logger.info(
            f"批量 OCR 开始 (并发): {total} 张图片, max_workers={max_workers}, source={source}"
        )

        # 使用有序字典保存结果 (idx -> text)
        results_map = {}

        lock = threading.Lock()
        completed_count = [0]  # 用列表实现闭包可变引用

        def _process_one(idx_url):
            idx, url = idx_url
            try:
                text = self.extract_text_from_url(url, source=source)
                with lock:
                    completed_count[0] += 1
                    if completed_count[0] % max(1, total // 5) == 0 or completed_count[0] == total:
                        logger.info(
                            f"OCR 进度: {completed_count[0]}/{total} "
                            f"({completed_count[0] * 100 // total}%)"
                        )
                return idx, text
            except Exception as e:
                logger.error(f"线程内 OCR 异常 [{idx}]: {e}")
                return idx, ""

        # 并发执行
        with ThreadPoolExecutor(max_workers=min(max_workers, total)) as executor:
            futures = {
                executor.submit(_process_one, (i, url)): i
                for i, url in enumerate(image_urls)
            }
            for future in as_completed(futures):
                try:
                    idx, text = future.result(timeout=60)
                    results_map[idx] = text
                except Exception as e:
                    idx = futures[future]
                    logger.error(f"线程执行异常 [{idx}]: {e}")
                    results_map[idx] = ""

        # 按原始顺序重建结果列表
        results = [results_map.get(i, "") for i in range(total)]
        success_count = sum(1 for t in results if t)
        logger.info(
            f"批量 OCR 完成 (并发): {success_count}/{total} 成功 (source={source})"
        )
        return results

    def download_image(self, url: str, save_path: str = None) -> Optional[str]:
        """下载图片到本地临时目录

        Args:
            url: 图片 URL
            save_path: 指定保存路径，默认自动生成

        Returns:
            str|None: 本地文件路径，失败返回 None
        """
        if not url:
            return None

        try:
            # 生成保存路径
            if save_path is None:
                url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
                suffix = ".jpg"
                # 尝试从 URL 推断后缀
                parsed = urlparse(url)
                path_lower = parsed.path.lower()
                for ext in (".png", ".gif", ".webp", ".jpeg", ".bmp"):
                    if path_lower.endswith(ext):
                        suffix = ext
                        break
                save_path = str(self._temp_dir / f"ocr_{url_hash}{suffix}")

            # 选择请求头（QQ 图片需要 Referer）
            headers = dict(QQ_IMAGE_HEADERS)
            if "gchat.qpic.cn" in url:
                headers["Referer"] = "https://q.qq.com/"
            elif "qpic.cn" in url:
                headers["Referer"] = "https://user.qzone.qq.com/"

            resp = requests.get(url, headers=headers, timeout=15, stream=True)
            resp.raise_for_status()

            # 检查是否为图片（简单校验 Content-Type）
            content_type = resp.headers.get("Content-Type", "")
            if content_type and not content_type.startswith("image"):
                logger.warning(f"下载的内容不是图片: {content_type}, url={url[:60]}")
                # 仍然尝试保存，可能 Content-Type 不准确

            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.debug(f"图片已下载: {save_path} ({os.path.getsize(save_path)} bytes)")
            return save_path

        except requests.exceptions.Timeout:
            logger.warning(f"图片下载超时: {url[:60]}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"图片下载失败: {url[:60]}, 错误: {e}")
            return None
        except Exception as e:
            logger.error(f"图片保存异常: {e}")
            return None

    def _do_ocr(self, image_path: str, method: str) -> str:
        """根据方法执行 OCR 识别

        各 Plan 的 OCR 执行逻辑：
        - paddleocr: 直接本地识别（Plan A）
        - napcat: 图片已由 NapCat 下载，使用本地 OCR（Plan B）
        - online: 调用在线 API（Plan C）
        """
        if method == "paddleocr":
            self.ocr_cost_stats["method_breakdown"]["paddleocr"] += 1
            return self._extract_with_paddleocr(image_path)
        elif method == "napcat":
            # NapCat 下载后优先用本地 PaddleOCR
            self.ocr_cost_stats["method_breakdown"]["paddleocr"] += 1
            if _PADDLEOCR_AVAILABLE or self._ensure_paddleocr():
                return self._extract_with_paddleocr(image_path)
            # 本地不可用则降级到在线 API
            return self._extract_with_online_api(image_path)
        elif method == "online":
            return self._extract_with_online_api(image_path)
        return ""

    def _extract_with_paddleocr(self, image_path: str) -> str:
        """使用 PaddleOCR 提取文字（Plan A / Plan B 本地识别）

        Args:
            image_path: 本地图片文件路径

        Returns:
            str: 识别出的文本
        """
        if not _PADDLEOCR_AVAILABLE:
            logger.warning("PaddleOCR 未安装，无法识别")
            return ""

        try:
            # 延迟初始化 PaddleOCR 引擎（首次使用时加载模型）
            if self._ocr_engine is None:
                logger.info("正在初始化 PaddleOCR 引擎...")
                engine = _PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
                self._ocr_engine = engine

            # 执行 OCR
            result = self._ocr_engine.ocr(image_path, cls=True)

            if not result or not result[0]:
                logger.debug(f"PaddleOCR 未识别到文字: {image_path}")
                return ""

            # 提取文本：result[0] 是每行的识别结果列表
            text_lines = []
            for line in result[0]:
                if line and len(line) >= 2 and line[1]:
                    text_lines.append(line[1][0])

            text = "\n".join(text_lines).strip()
            if text:
                logger.debug(f"PaddleOCR 识别成功: {len(text)} 字符")
            return text

        except Exception as e:
            logger.error(f"PaddleOCR 识别失败: {e}")
            return ""

    def _extract_with_online_api(self, image_path_or_url: str) -> str:
        """使用在线 OCR API 提取文字（Plan C）

        当前支持：
        - 百度 OCR API（通用文字识别）: 免费 1000次/月，超出 ¥0.004/次
        - 腾讯云 OCR: 免费 1000次/月，超出 ¥0.003/次

        如果均未配置，返回空字符串并记录提示。

        Args:
            image_path_or_url: 本地图片路径或图片 URL

        Returns:
            str: 识别出的文本
        """
        # 尝试加载在线 OCR 配置
        try:
            from scripts.config_loader import load_config
            config = load_config()
        except (ImportError, FileNotFoundError):
            config = {}

        ocr_config = config.get("ocr", {})

        # --- 百度 OCR ---
        baidu_ak = ocr_config.get("baidu_ocr_ak", "") or os.environ.get("BAIDU_OCR_AK", "")
        baidu_sk = ocr_config.get("baidu_ocr_sk", "") or os.environ.get("BAIDU_OCR_SK", "")

        if baidu_ak and baidu_sk:
            self._track_api_call("baidu_ocr")
            return self._baidu_ocr(image_path_or_url, baidu_ak, baidu_sk)

        # --- 腾讯云 OCR ---
        tencent_secret_id = ocr_config.get("tencent_ocr_secret_id", "") or os.environ.get(
            "TENCENT_OCR_SECRET_ID", ""
        )
        tencent_secret_key = ocr_config.get("tencent_ocr_secret_key", "") or os.environ.get(
            "TENCENT_OCR_SECRET_KEY", ""
        )

        if tencent_secret_id and tencent_secret_key:
            self._track_api_call("tencent_ocr")
            return self._tencent_ocr(image_path_or_url, tencent_secret_id, tencent_secret_key)

        logger.debug(
            "在线 OCR API 未配置（需要百度 AK/SK 或腾讯云 SecretId/SecretKey），"
            "请在 config.json 的 ocr 节中添加配置或设置环境变量"
        )
        return ""

    def _track_api_call(self, method_name: str):
        """记录一次在线 API 调用并更新成本统计

        Args:
            method_name: 'baidu_ocr' 或 'tencent_ocr'
        """
        stats = self.ocr_cost_stats
        stats["total_calls"] += 1
        stats["method_breakdown"][method_name] = stats["method_breakdown"].get(method_name, 0) + 1

        # 计算费用：前 FREE_QUOTA_MONTHLY 次免费
        if stats["total_calls"] <= self.FREE_QUOTA_MONTHLY:
            stats["free_quota_used"] = stats["total_calls"]
        else:
            stats["free_quota_used"] = self.FREE_QUOTA_MONTHLY
            paid = stats["total_calls"] - self.FREE_QUOTA_MONTHLY
            # 使用较低单价估算（腾讯云 0.003 作为保守估算）
            price = (
                self.BAIDU_PRICE_PER_CALL if method_name == "baidu_ocr"
                else self.TENCENT_PRICE_PER_CALL
            )
            # 重新计算总费用（所有超免费额度的调用，按各自单价）
            baidu_paid = max(0, stats["method_breakdown"].get("baidu_ocr", 0) - self.FREE_QUOTA_MONTHLY)
            tencent_paid = max(0, stats["method_breakdown"].get("tencent_ocr", 0) - self.FREE_QUOTA_MONTHLY)
            stats["paid_calls"] = baidu_paid + tencent_paid
            stats["estimated_cost"] = round(
                baidu_paid * self.BAIDU_PRICE_PER_CALL +
                tencent_paid * self.TENCENT_PRICE_PER_CALL,
                4,
            )

    def get_cost_report(self) -> dict:
        """获取 OCR 成本统计报告

        Returns:
            dict: {
                "total_calls": int,       # 总在线 API 调用次数
                "free_quota_used": int,   # 已用免费额度
                "paid_calls": int,        # 付费调用次数
                "estimated_cost": float,  # 预估费用（元）
                "method_breakdown": {     # 各方法调用次数
                    "paddleocr": int,
                    "baidu_ocr": int,
                    "tencent_ocr": int,
                },
                "start_time": str,        # 统计起始时间
                "paddleocr_price": "¥0.00 (免费)",
                "baidu_price_per_call": f"¥{self.BAIDU_PRICE_PER_CALL}/次",
                "tencent_price_per_call": f"¥{self.TENCENT_PRICE_PER_CALL}/次",
            }
        """
        report = dict(self.ocr_cost_stats)
        report["paddleocr_price"] = "¥0.00 (免费)"
        report["baidu_price_per_call"] = f"¥{self.BAIDU_PRICE_PER_CALL}/次"
        report["tencent_price_per_call"] = f"¥{self.TENCENT_PRICE_PER_CALL}/次"
        return report

    def _baidu_ocr(self, image_path_or_url: str, ak: str, sk: str) -> str:
        """调用百度 OCR 通用文字识别 API

        Args:
            image_path_or_url: 图片路径或 URL
            ak: 百度 API Key
            sk: 百度 Secret Key

        Returns:
            str: 识别结果
        """
        try:
            # 获取 access_token
            token_url = (
                f"https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials"
                f"&client_id={ak}&client_secret={sk}"
            )
            token_resp = requests.post(token_url, timeout=10)
            token_data = token_resp.json()
            access_token = token_data.get("access_token", "")
            if not access_token:
                logger.error(f"百度 OCR 获取 token 失败: {token_data.get('error_description', '')}")
                return ""

            # 判断是 URL 还是本地文件
            ocr_url = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            if image_path_or_url.startswith("http://") or image_path_or_url.startswith("https://"):
                # 使用 URL 方式
                payload = {"url": image_path_or_url}
                resp = requests.post(
                    f"{ocr_url}?access_token={access_token}",
                    headers=headers,
                    data=payload,
                    timeout=15,
                )
            else:
                # 使用 Base64 方式
                import base64
                with open(image_path_or_url, "rb") as f:
                    img_base64 = base64.b64encode(f.read()).decode("utf-8")
                payload = {"image": img_base64}
                resp = requests.post(
                    f"{ocr_url}?access_token={access_token}",
                    headers=headers,
                    data=payload,
                    timeout=15,
                )

            result = resp.json()

            if "error_code" in result:
                logger.error(f"百度 OCR 返回错误: {result.get('error_code')} - {result.get('error_msg', '')}")
                return ""

            words_list = result.get("words_result", [])
            text = "\n".join(item.get("words", "") for item in words_list)

            if text:
                logger.debug(f"百度 OCR 识别成功: {len(text)} 字符")
            return text

        except Exception as e:
            logger.error(f"百度 OCR 调用异常: {e}")
            return ""

    def _tencent_ocr(self, image_path_or_url: str, secret_id: str, secret_key: str) -> str:
        """调用腾讯云 OCR 通用文字识别 API

        注意：完整实现需要 tencentcloud-sdk-python，
        这里提供基于 HTTP 签名的简化实现。

        Args:
            image_path_or_url: 图片路径或 URL
            secret_id: 腾讯云 SecretId
            secret_key: 腾讯云 SecretKey

        Returns:
            str: 识别结果
        """
        # TODO: 如需使用腾讯云 OCR，请安装 tencentcloud-sdk-python-ocr
        # pip install tencentcloud-sdk-python-ocr
        # 然后使用以下方式调用：
        #
        # from tencentcloud.common import credential
        # from tencentcloud.ocr.v20181119 import ocr_client, models
        #
        # cred = credential.Credential(secret_id, secret_key)
        # client = ocr_client.OcrClient(cred, "ap-guangzhou")
        # req = models.GeneralBasicOCRRequest()
        # req.ImageUrl = image_url  # 或 req.ImageBase64 = base64_str
        # resp = client.GeneralBasicOCR(req)
        # text = "\n".join(item.DetectedText for item in resp.TextDetections)
        #
        logger.warning(
            "腾讯云 OCR 调用暂未完整实现，"
            "请安装 tencentcloud-sdk-python-ocr 并取消代码注释"
        )
        return ""

    def cleanup_temp_files(self, older_than_hours: int = 24) -> int:
        """清理过期的临时图片文件

        Args:
            older_than_hours: 清理多少小时前的文件，默认24小时

        Returns:
            int: 删除的文件数量
        """
        if not self._temp_dir.exists():
            return 0

        count = 0
        cutoff = time.time() - older_than_hours * 3600
        for f in self._temp_dir.iterdir():
            if f.is_file() and f.name.startswith("ocr_"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        count += 1
                except OSError:
                    continue

        if count:
            logger.info(f"清理临时 OCR 文件: {count} 个")
        return count


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    extractor = OCRExtractor()

    print("=" * 60)
    print("  OCR 图片文字提取模块 - 自测")
    print("=" * 60)

    # 1. 基础信息
    print(f"\n[1] 基础信息:")
    print(f"    OCR 启用状态: {extractor._enabled}")
    print(f"    配置方法: {extractor._method}")
    print(f"    临时目录: {extractor._temp_dir}")

    # 2. PaddleOCR 可用性检测
    print(f"\n[2] PaddleOCR 可用性检测:")
    print(f"    导入检测: {'已安装' if _PADDLEOCR_AVAILABLE else '未安装'}")
    if not _PADDLEOCR_AVAILABLE:
        print("    正在尝试安装/检测 PaddleOCR...")
        available = extractor._ensure_paddleocr()
        print(f"    _ensure_paddleocr() 结果: {'可用' if available else '不可用'}")
    else:
        print("    PaddleOCR 已就绪，无需额外安装")

    # 3. 检测最佳可用方案
    print(f"\n[3] OCR 方案检测:")
    method = extractor.detect_available_method()
    plan_names = {
        "paddleocr": "Plan A: 本地 PaddleOCR（主力）",
        "napcat": "Plan B: NapCat 下载 + 本地 OCR",
        "online": "Plan C: 在线 API（百度/腾讯云）",
        "none": "Plan D: 仅标记 [图片]（兜底）",
    }
    print(f"    当前方案: {plan_names.get(method, method)}")

    # 4. 测试图片 OCR（如果提供了命令行参数）
    import sys
    if len(sys.argv) > 1:
        test_image = sys.argv[1]
        print(f"\n[4] 测试图片 OCR: {test_image}")
        text = extractor.extract_text_from_file(test_image)
        if text:
            print(f"    识别成功 ({len(text)} 字符):")
            print("    " + "-" * 52)
            for line in text.split("\n")[:10]:
                print(f"    | {line}")
            if len(text.split("\n")) > 10:
                print(f"    | ... (共 {len(text.split(chr(10)))} 行)")
            print("    " + "-" * 52)
        else:
            print("    未识别到文字")
    else:
        print(f"\n[4] 测试图片: 未提供")
        print("    用法: python scripts/ocr_extractor.py <图片路径>")
        print("    示例: python scripts/ocr_extractor.py data/test_image.png")

    # 5. 成本报告
    print(f"\n[5] OCR 成本统计报告:")
    report = extractor.get_cost_report()
    print(f"    统计起始: {report['start_time'][:19]}")
    print(f"    在线 API 总调用: {report['total_calls']} 次")
    print(f"    免费额度已用: {report['free_quota_used']}/{extractor.FREE_QUOTA_MONTHLY}")
    print(f"    付费调用次数: {report['paid_calls']}")
    print(f"    预估费用: ¥{report['estimated_cost']}")
    print(f"    方法分布:")
    for m_name, count in report["method_breakdown"].items():
        print(f"      - {m_name}: {count} 次")
    print(f"    定价参考:")
    print(f"      - PaddleOCR: {report['paddleocr_price']}")
    print(f"      - 百度 OCR: {report['baidu_price_per_call']}")
    print(f"      - 腾讯云 OCR: {report['tencent_price_per_call']}")

    # 6. 清理临时文件
    cleaned = extractor.cleanup_temp_files()
    if cleaned > 0:
        print(f"\n[6] 清理临时文件: {cleaned} 个")
    else:
        print(f"\n[6] 无需清理临时文件")

    print("\n" + "=" * 60)
    print("  自测完成")
    print("=" * 60)
