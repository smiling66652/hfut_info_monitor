"""
Ollama 本地 AI 管理模块
功能：管理 Ollama 服务的生命周期、模型管理与推理接口
    与 ai_secretary 集成，提供智能摘要生成（支持 DeepSeek API 回退）

依赖：subprocess, json, urllib.request（标准库） + requests（项目已有）
"""

import json
import os
import subprocess
import time
import logging
import urllib.request
import urllib.error
from typing import Optional

# 尝试导入 requests（项目已有依赖）
try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# 尝试加载项目配置
try:
    from config_loader import load_config
    CONFIG = load_config()
except (ImportError, FileNotFoundError, json.JSONDecodeError):
    CONFIG = {}

logger = logging.getLogger('OllamaManager')


class OllamaManager:
    """Ollama 本地 AI 服务管理器，封装服务启停、模型管理和推理接口"""

    # 默认配置常量
    DEFAULT_BASE_URL = "http://localhost:11434"
    DEFAULT_MODEL = "qwen2.5:0.5b"  # 轻量模型，适合本地低配设备运行

    def __init__(self, base_url: str = None, default_model: str = None):
        """
        初始化 Ollama 管理器

        配置优先级：参数传入 > 环境变量 > config.json (ollama 节) > 内置默认值

        Args:
            base_url:      Ollama 服务地址
            default_model: 默认模型名
        """
        # 从 config.json 读取 ollama 配置节（如果有）
        ollama_config = CONFIG.get('ollama', {})

        self.base_url = (
            base_url or
            os.environ.get('OLLAMA_URL') or
            ollama_config.get('base_url') or
            self.DEFAULT_BASE_URL
        ).rstrip('/')

        self.default_model = (
            default_model or
            os.environ.get('OLLAMA_MODEL') or
            ollama_config.get('model') or
            self.DEFAULT_MODEL
        )

        self._process = None  # ollama serve 子进程引用

        logger.debug(f"OllamaManager 已初始化：base_url={self.base_url}, model={self.default_model}")

    # ═══════════════════════════════════════════════════════════════
    # 服务管理
    # ═══════════════════════════════════════════════════════════════

    def is_running(self) -> bool:
        """
        检查 Ollama 服务是否在运行（通过 /api/tags 探活）

        Returns:
            bool: True 表示服务可达
        """
        try:
            return self._http_get('/api/tags', timeout=3) is not None
        except Exception:
            return False

    def start(self) -> bool:
        """
        启动 Ollama 服务（ollama serve）

        处理策略：
        - 先检测是否已运行，避免重复启动
        - 子进程无窗口启动（Windows 兼容）
        - 最多等待 30 秒确认服务就绪

        Returns:
            bool: 服务是否成功启动（或已在运行）
        """
        try:
            if self.is_running():
                logger.info("Ollama 服务已在运行")
                return True

            logger.info("正在启动 Ollama 服务...")

            # Windows 下隐藏控制台窗口
            popen_kwargs = {
                'stdout': subprocess.DEVNULL,
                'stderr': subprocess.DEVNULL,
            }
            if os.name == 'nt':
                popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP

            self._process = subprocess.Popen(['ollama', 'serve'], **popen_kwargs)

            # 轮询等待服务就绪（最多 30 秒）
            for i in range(30):
                time.sleep(1)
                if self.is_running():
                    logger.info(f"Ollama 服务启动成功（耗时 {i + 1}s）")
                    return True

            logger.error("Ollama 服务启动超时（30s）")
            return False

        except FileNotFoundError:
            logger.error("未找到 ollama 命令，请先安装 Ollama：https://ollama.com/download")
            return False
        except Exception as e:
            logger.error(f"启动 Ollama 服务失败：{e}")
            return False

    def stop(self) -> bool:
        """
        停止 Ollama 服务

        通过以下途径尝试：
        1. 终止自身持有的子进程
        2. 若进程引用丢失，在 Windows 下使用 taskkill

        Returns:
            bool: 是否成功停止
        """
        try:
            if self._process:
                logger.info("正在停止 Ollama 服务（子进程）...")
                self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
                self._process = None
                logger.info("Ollama 服务已停止")
                return True

            # 子进程引用丢失，尝试系统级终止（仅 Windows）
            if os.name == 'nt':
                result = subprocess.run(
                    ['taskkill', '/F', '/IM', 'ollama.exe'],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if result.returncode == 0:
                    logger.info("已通过 taskkill 终止 ollama.exe")
                return True

            logger.info("无活动进程引用，无需停止")
            return True

        except Exception as e:
            logger.error(f"停止 Ollama 服务失败：{e}")
            return False

    def restart(self) -> bool:
        """
        重启 Ollama 服务

        Returns:
            bool: 重启后服务是否正常
        """
        logger.info("正在重启 Ollama 服务...")
        self.stop()
        time.sleep(2)  # 等待端口释放
        return self.start()

    def check_health(self) -> tuple:
        """
        健康检查（兼容 main.py 的 check_components 调用格式）

        Returns:
            (bool, str): (是否健康, 详情描述)
        """
        try:
            if self.is_running():
                models = self.list_models()
                count = len(models)
                if count > 0:
                    return (True, f"Ollama 服务正常，已安装 {count} 个模型")
                else:
                    return (True, "Ollama 服务正常，但未安装模型")
            else:
                return (False, "Ollama 服务未运行")
        except Exception as e:
            return (False, f"Ollama 健康检查异常：{e}")

    # ═══════════════════════════════════════════════════════════════
    # 模型管理
    # ═══════════════════════════════════════════════════════════════

    def list_models(self) -> list:
        """
        获取已安装的模型列表

        Returns:
            list[str]: 模型名称列表，失败时返回空列表
        """
        try:
            data = self._http_get('/api/tags')
            if data and 'models' in data:
                return [m.get('name', '') for m in data['models']]
            return []
        except Exception as e:
            logger.error(f"获取模型列表失败：{e}")
            return []

    def ensure_model(self, model_name: str = None) -> bool:
        """
        确保指定模型已下载，若缺失则通过 ollama pull 自动拉取

        Args:
            model_name: 模型名（含 tag），默认使用 self.default_model

        Returns:
            bool: 模型是否就绪
        """
        if model_name is None:
            model_name = self.default_model

        try:
            installed = self.list_models()

            # 精确匹配
            if model_name in installed:
                logger.debug(f"模型 {model_name} 已安装")
                return True

            # 部分匹配（例如 qwen2.5 匹配 qwen2.5:0.5b）
            base_name = model_name.split(':')[0] if ':' in model_name else model_name
            for m in installed:
                if m.startswith(base_name):
                    logger.debug(f"模型 {model_name} 已安装（匹配到 {m}）")
                    return True

            # 模型缺失，启动服务并拉取
            logger.info(f"模型 {model_name} 未安装，正在下载...")

            if not self.is_running():
                logger.warning("Ollama 服务未运行，尝试启动...")
                if not self.start():
                    logger.error("无法启动 Ollama 服务，模型拉取失败")
                    return False

            result = subprocess.run(
                ['ollama', 'pull', model_name],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                logger.info(f"模型 {model_name} 下载成功")
                return True
            else:
                logger.error(f"模型 {model_name} 下载失败：{result.stderr.strip()[:200]}")
                return False

        except FileNotFoundError:
            logger.error("未找到 ollama 命令")
            return False
        except Exception as e:
            logger.error(f"确保模型 {model_name} 失败：{e}")
            return False

    def get_default_model(self) -> str:
        """返回当前默认模型名称"""
        return self.default_model

    # ═══════════════════════════════════════════════════════════════
    # 推理接口
    # ═══════════════════════════════════════════════════════════════

    def infer(self, prompt: str, model: str = None,
              system_prompt: str = None) -> dict:
        """
        调用 /api/generate 进行文本补全推理

        Args:
            prompt:       用户提示词
            model:        模型名称（可选，默认使用 self.default_model）
            system_prompt: 系统级提示词（可选，将拼接到 prompt 之前）

        Returns:
            dict: 成功时 {'response': str, 'model': str, 'duration_ms': int}
                  失败时 {'error': str}
        """
        if model is None:
            model = self.default_model

        try:
            full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

            payload = {
                'model': model,
                'prompt': full_prompt,
                'stream': False,
            }

            data = self._http_post('/api/generate', payload, timeout=120)

            if data:
                # total_duration 单位是纳秒，转换为毫秒
                duration_ns = data.get('total_duration', 0)
                return {
                    'response': data.get('response', ''),
                    'model': data.get('model', model),
                    'duration_ms': round(duration_ns / 1_000_000, 1),
                }
            else:
                return {'error': 'Ollama 返回空响应'}

        except Exception as e:
            logger.error(f"Ollama infer 失败：{e}")
            return {'error': str(e)}

    def chat(self, messages: list, model: str = None) -> dict:
        """
        调用 /api/chat 进行多轮对话

        Args:
            messages: 消息列表，格式 [{'role': 'user', 'content': '...'}, ...]
            model:    模型名称

        Returns:
            dict: 成功时 {'response': str, 'model': str, 'duration_ms': int}
                  失败时 {'error': str}
        """
        if model is None:
            model = self.default_model

        try:
            payload = {
                'model': model,
                'messages': messages,
                'stream': False,
            }

            data = self._http_post('/api/chat', payload, timeout=120)

            if data:
                msg = data.get('message', {})
                duration_ns = data.get('total_duration', 0)
                return {
                    'response': msg.get('content', ''),
                    'model': data.get('model', model),
                    'duration_ms': round(duration_ns / 1_000_000, 1),
                }
            else:
                return {'error': 'Ollama 返回空响应'}

        except Exception as e:
            logger.error(f"Ollama chat 失败：{e}")
            return {'error': str(e)}

    # ═══════════════════════════════════════════════════════════════
    # ai_secretary 集成：信息摘要生成
    # ═══════════════════════════════════════════════════════════════

    def generate_summary(self, infos: list) -> str:
        """
        接收信息列表，调用 AI 生成中文摘要

        优先级：
        1. 本地 Ollama（快速、免费）
        2. DeepSeek API（需要 DEEPSEEK_API_KEY，作为回退）

        Args:
            infos: 信息列表，每条为 dict，包含 title/content/source/category 等字段

        Returns:
            str: 生成的中文摘要文本
        """
        if not infos:
            return "（无信息需要摘要）"

        prompt = self._build_summary_prompt(infos)

        # 优先使用本地 Ollama
        if self.is_running():
            try:
                result = self.infer(
                    prompt,
                    system_prompt="你是一个专业的校园信息摘要助手，请用简洁清晰的中文回复。"
                )
                response_text = result.get('response', '')
                if response_text and 'error' not in result:
                    logger.info(f"Ollama 摘要生成成功（{result.get('duration_ms', 0)}ms）")
                    return response_text
                else:
                    logger.warning(f"Ollama 摘要生成失败：{result.get('error', '未知')}，回退到 DeepSeek")
            except Exception as e:
                logger.warning(f"Ollama 摘要生成异常：{e}，回退到 DeepSeek")

        # 回退：使用 DeepSeek API
        return self._summary_by_deepseek(prompt)

    def _build_summary_prompt(self, infos: list) -> str:
        """将信息列表构建为 AI 摘要提示词"""
        lines = []
        for i, info in enumerate(infos, 1):
            title = info.get('title', '无标题')
            content = info.get('content', '')
            source = info.get('source', '未知来源')
            category = info.get('category', '未分类')
            time_str = info.get('time', '')

            lines.append(f"{i}. 【{category}】{title}")
            if content and content != title:
                # 截取前 200 字作为预览
                preview = content[:200].replace('\n', ' ')
                lines.append(f"   内容预览: {preview}")
            if time_str:
                lines.append(f"   时间: {time_str}")
            lines.append(f"   来源: {source}")
            lines.append("")

        info_block = '\n'.join(lines)

        return f"""请根据以下合工大相关校园信息，生成一份简洁的中文摘要报告：

{info_block}

要求：
1. 第一行给出总体概述（一句话概括本批次信息的主题分布）
2. 按类别分组汇总（通知公告、学术信息、活动比赛、招聘就业、普通信息等）
3. 突出标记最重要的 1-3 条信息（如果有的话）
4. 总字数控制在 500 字以内
5. 使用清晰的分段格式，便于快速阅读
"""

    def _summary_by_deepseek(self, prompt: str) -> str:
        """
        调用 DeepSeek API 生成摘要（回退方案）

        配置优先级：环境变量 DEEPSEEK_API_KEY > config.json llm.deepseek_api_key
        """
        try:
            # 读取 API Key 配置
            api_key = (
                os.environ.get('DEEPSEEK_API_KEY') or
                CONFIG.get('llm', {}).get('deepseek_api_key', '')
            )

            if not api_key:
                logger.warning("DeepSeek API Key 未配置（DEEPSEEK_API_KEY）")
                return "⚠️ 本地 AI 不可用，且 DeepSeek API Key 未配置，无法生成 AI 摘要"

            api_base = (
                os.environ.get('DEEPSEEK_API_BASE') or
                CONFIG.get('llm', {}).get('deepseek_api_base', 'https://api.deepseek.com')
            ).rstrip('/')

            logger.info("使用 DeepSeek API 生成摘要...")

            payload = {
                'model': 'deepseek-chat',
                'messages': [
                    {'role': 'system', 'content': '你是一个专业的校园信息摘要助手，请用简洁清晰的中文回复。'},
                    {'role': 'user', 'content': prompt},
                ],
                'max_tokens': 1000,
                'temperature': 0.3,
            }

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            }

            if _HAS_REQUESTS:
                resp = requests.post(
                    f'{api_base}/v1/chat/completions',
                    json=payload,
                    headers=headers,
                    timeout=60,
                )
                if resp.status_code == 200:
                    content = resp.json()['choices'][0]['message']['content']
                    logger.info("DeepSeek API 摘要生成成功")
                    return content
                else:
                    logger.error(f"DeepSeek API HTTP {resp.status_code}: {resp.text[:200]}")
                    return f"⚠️ DeepSeek API 调用失败（HTTP {resp.status_code}）"
            else:
                # urllib 回退
                req = urllib.request.Request(
                    f'{api_base}/v1/chat/completions',
                    data=json.dumps(payload).encode('utf-8'),
                    headers=headers,
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    return data['choices'][0]['message']['content']

        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace') if e.fp else ''
            logger.error(f"DeepSeek API HTTP {e.code}: {body[:200]}")
            return f"⚠️ DeepSeek API 请求失败（HTTP {e.code}）"
        except Exception as e:
            logger.error(f"DeepSeek API 调用异常：{e}")
            return f"⚠️ AI 摘要生成失败：{e}"

    # ═══════════════════════════════════════════════════════════════
    # 内部 HTTP 工具方法
    # ═══════════════════════════════════════════════════════════════

    def _http_get(self, path: str, timeout: int = 30) -> Optional[dict]:
        """GET 请求，返回 JSON 或 None"""
        url = f"{self.base_url}{path}"
        try:
            if _HAS_REQUESTS:
                resp = requests.get(url, timeout=timeout)
                return resp.json() if resp.status_code == 200 else None
            else:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
        except Exception:
            return None

    def _http_post(self, path: str, payload: dict, timeout: int = 120) -> Optional[dict]:
        """POST JSON 请求，返回 JSON 或 None"""
        url = f"{self.base_url}{path}"
        try:
            if _HAS_REQUESTS:
                resp = requests.post(url, json=payload, timeout=timeout)
                return resp.json() if resp.status_code == 200 else None
            else:
                data_bytes = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    url,
                    data=data_bytes,
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# 模块级便捷函数（兼容外部调用）
# ═══════════════════════════════════════════════════════════════

# 全局单例（懒加载）
_ollama_manager: Optional[OllamaManager] = None


def get_manager() -> OllamaManager:
    """获取 OllamaManager 单例（懒初始化，配置仅加载一次）"""
    global _ollama_manager
    if _ollama_manager is None:
        _ollama_manager = OllamaManager()
    return _ollama_manager


def check_health() -> tuple:
    """
    模块级健康检查（兼容 main.py check_components 的返回格式）

    Returns:
        (bool, str): (是否正常, 详情信息)
    """
    try:
        mgr = get_manager()
        return mgr.check_health()
    except Exception as e:
        return (False, f"Ollama 管理模块初始化失败：{e}")


def generate_summary(infos: list) -> str:
    """
    模块级便捷函数：接收信息列表，生成中文摘要

    Args:
        infos: 信息列表

    Returns:
        str: AI 生成的中文摘要
    """
    mgr = get_manager()
    return mgr.generate_summary(infos)


# ═══════════════════════════════════════════════════════════════
# 独立测试入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )

    print("=" * 60)
    print("OllamaManager 模块自测")
    print("=" * 60)

    mgr = OllamaManager()

    # 1. 基本配置
    print("\n[配置]")
    print(f"  服务地址: {mgr.base_url}")
    print(f"  默认模型: {mgr.get_default_model()}")

    # 2. 健康检查
    print("\n[健康检查]")
    ok, msg = check_health()
    print(f"  结果: {'PASS' if ok else 'FAIL'} - {msg}")

    # 3. 服务状态
    print("\n[服务状态]")
    running = mgr.is_running()
    print(f"  服务运行: {'是' if running else '否'}")

    # 4. 模型列表
    if running:
        print("\n[已安装模型]")
        models = mgr.list_models()
        for m in models:
            print(f"  - {m}")
        if not models:
            print("  (无已安装模型)")

    # 5. 简单推理测试
    if running and mgr.list_models():
        print("\n[推理测试]")
        result = mgr.infer("用一句话介绍合肥工业大学")
        if 'error' in result:
            print(f"  失败: {result['error']}")
        else:
            print(f"  响应: {result.get('response', '')[:80]}...")
            print(f"  模型: {result.get('model')}")
            print(f"  耗时: {result.get('duration_ms')}ms")

    # 6. 摘要生成测试
    print("\n[摘要生成测试]")
    test_infos = [
        {
            'title': '关于2026年保研工作的通知',
            'content': '请各位同学注意，2026年保研工作即将开始，请符合条件的同学尽快报名。截止日期为6月15日。',
            'source': '教务处通知',
            'category': '学术信息',
            'time': '2026-06-01',
        },
        {
            'title': '校园文化艺术节即将开幕',
            'content': '一年一度的校园文化艺术节将于本月20日在体育场开幕，欢迎同学们积极参加。',
            'source': '团委通知',
            'category': '活动比赛',
            'time': '2026-06-02',
        },
    ]
    summary = mgr.generate_summary(test_infos)
    print(f"  摘要结果:\n{summary[:200]}...")

    print("\n" + "=" * 60)
    print("自测完成")
