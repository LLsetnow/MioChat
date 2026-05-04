"""Real-time-voice 主服务：aiohttp HTTP + WebSocket + 静态文件"""

import asyncio
import json
import os

import sys
import time
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中（兼容 python src/server.py 直接运行）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from aiohttp import web

from src.logger import setup_logger, get_logger
from src.llm_client import get_context, clear_context, generate_diary, get_persona_tier
import httpx

# 加载 .env（本目录）
from dotenv import load_dotenv
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9902
DIARY_DIR = Path(__file__).resolve().parent / "diary"

# ── 状态常量 ────────────────────────────────────────────────────────

STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

# ── 消息工具 ────────────────────────────────────────────────────────


def _has_unclosed_paren(text: str) -> bool:
    """检查文本中是否有未闭合的括号（中英文圆括号 + 尖括号）"""
    paren_depth = 0
    angle_depth = 0
    for ch in text:
        if ch in '（(':
            paren_depth += 1
        elif ch in '）)':
            paren_depth -= 1
        elif ch == '<':
            angle_depth += 1
        elif ch == '>':
            angle_depth -= 1
    return paren_depth > 0 or angle_depth > 0


def _ends_with_break(text: str) -> bool:
    """检查文本是否以自然断点结尾（空格/标点/闭合括号/闭合尖括号）"""
    if not text:
        return True
    return text.rstrip()[-1:] in ' ，。！？、；：,.!?;:）)\n>'


def _mkmsg(**kwargs) -> str:
    return json.dumps(kwargs, ensure_ascii=False)


def _resolve_tts_model(voice: str, current_model: str) -> str:
    """从音色 ID 推断实际 TTS 模型（克隆音色编码了模型版本）"""
    for ver in ("v3.5-plus", "v3.5-flash", "v3-plus", "v3-flash", "v2"):
        prefix = f"cosyvoice-{ver}-"
        if voice.startswith(prefix):
            return f"cosyvoice-{ver}"
    return current_model


async def _send_json(ws, **kwargs):
    await ws.send_str(_mkmsg(**kwargs))


async def _send_audio(ws, pcm_bytes: bytes):
    """发送音频二进制帧：[0x02] + PCM float32"""
    await ws.send_bytes(b"\x02" + pcm_bytes)


# ── 主逻辑 ──────────────────────────────────────────────────────────


class VoiceChatSession:
    """管理单次 WebSocket 连接的完整会话状态"""

    def __init__(self, ws, logger):
        self.ws = ws
        self.logger = logger
        self.state = STATE_IDLE

        # 配置
        self.asr_model = os.environ.get("ASR_MODEL", "fun-asr-realtime")
        self.asr_api_key = os.environ.get("ASR_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
        self.llm_model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
        self.llm_api_key = os.environ.get("LLM_API_KEY", "")
        self.llm_base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        self.tts_model = os.environ.get("QWEN_TTS_MODEL", "cosyvoice-v3-flash")
        self.tts_api_key = os.environ.get("QWEN_TTS_API_KEY", "")
        self.tts_voice = os.environ.get("VOICE_ID1", "").split("#")[0].strip() or "longhuhu_v3"
        self.tts_instruction = ""
        self.tts_enabled = True

        # 打断控制
        self._cancel_event = asyncio.Event()
        self._current_asr = None
        self._current_llm_task = None
        self._tts_queue = asyncio.Queue()

        # 日记锁
        self._diary_lock = asyncio.Lock()

        # 性能计时
        self._pipe_start = 0.0       # 管道开始时间
        self._llm_first_token = 0.0  # LLM 首token时间
        self._tts_first_audio = 0.0  # TTS 首包音频时间
        self._tts_pcm_samples = 0    # TTS 累计 PCM 采样数
        self._tts_sample_rate = 24000
        self._tts_chunk_texts = []

        # 多维情感状态
        self.emotions = {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0, "love": 0.0, "surprise": 0.0, "trust": 0.0}
        self._pending_emotions = {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0, "love": 0.0, "surprise": 0.0, "trust": 0.0}

        # 亲密度 & 人设等级
        self.intimacy = 0.0
        self.persona_tier = 1
        self._pending_intimacy = 0.0

    async def handle_message(self, raw):
        """消息路由"""
        if isinstance(raw, bytes):
            if raw[0:1] == b"\x01":
                if self._current_asr:
                    await self._current_asr.send_audio(raw[1:])
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type", "")
        self.logger.debug(f"[WS] 收到: {msg_type}")

        if msg_type == "start_voice":
            await self._handle_start_voice(msg)
        elif msg_type == "stop_voice":
            await self._handle_stop_voice()
        elif msg_type == "text_input":
            await self._handle_text_input(msg.get("text", ""))
        elif msg_type == "interrupt":
            await self._handle_interrupt()
        elif msg_type == "update_config":
            await self._handle_update_config(msg)
        elif msg_type == "end_conversation":
            await self._handle_end_conversation()

    # ── 语音输入 ──────────────────────────────────────────────────

    async def _handle_start_voice(self, msg):
        if self.state == STATE_THINKING or self.state == STATE_SPEAKING:
            await self._handle_interrupt()

        # 清理可能残留的旧 ASR 连接
        if self._current_asr:
            try:
                await self._current_asr.close()
            except Exception:
                pass
            self._current_asr = None

        from src.asr_client import ASRClient

        self.state = STATE_LISTENING
        await _send_json(self.ws, type="status", state=STATE_LISTENING)

        asr = ASRClient(self.asr_api_key, self.asr_model)
        self._current_asr = asr

        asr.set_callbacks(
            on_partial=lambda t: asyncio.create_task(
                _send_json(self.ws, type="asr_partial", text=t)
            ),
            on_final=lambda t: asyncio.create_task(self._on_asr_final(t)),
            on_error=lambda e: asyncio.create_task(
                _send_json(self.ws, type="error", message=str(e))
            ),
        )

        try:
            await asr.connect()
        except Exception as e:
            self.logger.error(f"[ASR] 连接失败: {e}")
            await _send_json(self.ws, type="error", message=f"ASR连接失败: {e}")
            self.state = STATE_IDLE

    async def _on_asr_final(self, text: str):
        self.logger.info(f"[会话] ASR final: {text} (state={self.state})")
        await _send_json(self.ws, type="asr_final", text=text)

        # 如果当前正在思考/说话，打断旧管线
        if self.state == STATE_THINKING or self.state == STATE_SPEAKING:
            self.logger.info("[会话] ASR 打断当前回复")
            self._cancel_event.set()

        await self._start_llm_tts_pipeline(text)

    async def _handle_stop_voice(self):
        if self._current_asr:
            await self._current_asr.finish()
            await self._current_asr.close()
            self._current_asr = None
        if self.state == STATE_LISTENING:
            self.state = STATE_IDLE
            await _send_json(self.ws, type="status", state=STATE_IDLE)

    # ── 文字输入 ──────────────────────────────────────────────────

    async def _handle_text_input(self, text: str):
        if not text.strip():
            return
        if self.state == STATE_THINKING or self.state == STATE_SPEAKING:
            await self._handle_interrupt()
        self.logger.info(f"[会话] 文字输入: {text}")
        await self._start_llm_tts_pipeline(text)

    # ── LLM + TTS 流水线 ──────────────────────────────────────────

    async def _start_llm_tts_pipeline(self, user_text: str):
        self.state = STATE_THINKING
        self._cancel_event.clear()
        await _send_json(self.ws, type="status", state=STATE_THINKING)

        # 重置计时
        self._pipe_start = time.time()
        self._llm_first_token = 0.0
        self._tts_first_audio = 0.0
        self._tts_pcm_samples = 0
        self._tts_sample_rate = 24000
        self._tts_chunk_texts = []
        self._pending_emotions = {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0, "love": 0.0, "surprise": 0.0, "trust": 0.0}
        self._pending_intimacy = 0.0

        try:
            from src.llm_client import generate_llm_stream, should_trigger_tts, extract_tts_chunks, extract_emotion_tags, analyze_sentiment

            text_buffer = ""
            tts_queue = asyncio.Queue()

            # LLM 流式生成 → 分块入队
            async def _llm_producer():
                nonlocal text_buffer
                try:
                    async for token, full_text, is_done in generate_llm_stream(
                        user_text=user_text,
                        session_id="default",
                        api_key=self.llm_api_key,
                        base_url=self.llm_base_url,
                        model=self.llm_model,
                        tier=self.persona_tier,
                    ):
                        if self._cancel_event.is_set():
                            return

                        if token:
                            if not self._llm_first_token:
                                self._llm_first_token = time.time()
                            text_buffer += token

                            # 检查是否有自然断点 → 分块
                            if should_trigger_tts(text_buffer) and not _has_unclosed_paren(text_buffer):
                                # 先提取情绪标签（从 text_buffer 中去掉）
                                text_buffer, emotions = extract_emotion_tags(text_buffer)
                                for k, v in emotions.items():
                                    if v != 0:
                                        self._pending_emotions[k] += v
                                self._pending_intimacy += emotions.get("love", 0) + emotions.get("trust", 0)
                                has_nonzero = any(v != 0 for v in emotions.values())
                                if has_nonzero:
                                    active = {k: v for k, v in emotions.items() if v != 0}
                                    self.logger.info(f"[会话] 提取标签: {active}")
                                # 发送过滤掉标签后的纯文本到前端
                                if text_buffer:
                                    await _send_json(self.ws, type="llm_delta", text=text_buffer)
                                chunks = extract_tts_chunks(text_buffer)
                                if _ends_with_break(text_buffer):
                                    # 所有块完整，全部入队
                                    for ch in chunks:
                                        self._tts_chunk_texts.append(ch)
                                        await tts_queue.put(ch)
                                    text_buffer = ""
                                elif len(chunks) > 1:
                                    # 最后一块可能不完整，保留在 buffer
                                    for ch in chunks[:-1]:
                                        self._tts_chunk_texts.append(ch)
                                        await tts_queue.put(ch)
                                    text_buffer = chunks[-1]
                                elif len(chunks) == 1 and len(chunks[0]) <= 20:
                                    # 单块但足够短，直接入队
                                    self._tts_chunk_texts.append(chunks[0])
                                    await tts_queue.put(chunks[0])
                                    text_buffer = ""
                                else:
                                    # 兜底：剩余内容全部入队
                                    for ch in chunks:
                                        self._tts_chunk_texts.append(ch)
                                        await tts_queue.put(ch)
                                    text_buffer = ""

                        if is_done and text_buffer.strip():
                            text_buffer, emotions = extract_emotion_tags(text_buffer)
                            for k, v in emotions.items():
                                if v != 0:
                                    self._pending_emotions[k] += v
                            self._pending_intimacy += emotions.get("love", 0) + emotions.get("trust", 0)
                            has_nonzero = any(v != 0 for v in emotions.values())
                            if has_nonzero:
                                active = {k: v for k, v in emotions.items() if v != 0}
                                self.logger.info(f"[会话] 提取标签(is_done): {active}")
                            # 发送剩余纯文本（标签已被去掉）
                            if text_buffer.strip():
                                await _send_json(self.ws, type="llm_delta", text=text_buffer)
                            chunks = extract_tts_chunks(text_buffer)
                            for ch in chunks:
                                self._tts_chunk_texts.append(ch)
                                await tts_queue.put(ch)
                            text_buffer = ""

                        if is_done:
                            # 如果 LLM 没有输出显式标签，使用情感分析兜底
                            all_zero = all(v == 0 for v in self._pending_emotions.values())
                            if all_zero:
                                emotions = analyze_sentiment(full_text)
                                has_nonzero = any(v != 0 for v in emotions.values())
                                if has_nonzero:
                                    for k, v in emotions.items():
                                        self._pending_emotions[k] += v
                                    self._pending_intimacy += emotions.get("love", 0) + emotions.get("trust", 0)
                                    active = {k: v for k, v in emotions.items() if v != 0}
                                    self.logger.info(f"[会话] 情感分析兜底: {active}")

                            self.logger.info(f"[LLM] 完整回复: {full_text}")
                            return
                finally:
                    await tts_queue.put(None)  # 哨兵：LLM 结束

            # 顺序 TTS 合成 → 出队
            async def _tts_consumer():
                while True:
                    if self._cancel_event.is_set():
                        return
                    tts_text = await tts_queue.get()
                    if tts_text is None:
                        break
                    if self.tts_enabled:
                        await self._tts_synthesize(tts_text)

            # 并行运行：LLM 生产 + TTS 顺序消费
            llm_task = asyncio.create_task(_llm_producer())
            tts_task = asyncio.create_task(_tts_consumer())
            try:
                await asyncio.wait_for(
                    asyncio.gather(llm_task, tts_task, return_exceptions=True),
                    timeout=30
                )
            except asyncio.TimeoutError:
                self.logger.error("[会话] 管道超时(30s)，强制取消")
                self._cancel_event.set()
                for t in (llm_task, tts_task):
                    t.cancel()
                await asyncio.gather(llm_task, tts_task, return_exceptions=True)

            await _send_json(self.ws, type="llm_done")

            # 应用多维情感变化
            for k in self._pending_emotions:
                self.emotions[k] += self._pending_emotions[k]
            has_nonzero = any(v != 0 for v in self._pending_emotions.values())
            if has_nonzero:
                active = {k: f"{self._pending_emotions[k]:+.1f}" for k, v in self._pending_emotions.items() if v != 0}
                self.logger.info(f"[会话] 情感变化: {active} | 当前: {self.emotions}")
                await _send_json(self.ws, type="emotion", **self.emotions)

            # 亲密度 & 人设切换
            self.intimacy += self._pending_intimacy
            new_tier = get_persona_tier(self.intimacy)
            if new_tier != self.persona_tier:
                self.persona_tier = new_tier
                self.logger.info(f"[角色] 人设切换至 Tier {new_tier} (亲密度: {self.intimacy:.1f})")
                await _send_json(self.ws, type="persona_tier", tier=new_tier, intimacy=round(self.intimacy, 1))

            # ── TTS 分块日志 ──
            total_chunks = len(self._tts_chunk_texts)
            for i, ch in enumerate(self._tts_chunk_texts, 1):
                self.logger.info(f"[TTS] [{i}/{total_chunks}] {ch}")

            # ── 性能统计 ──
            pipe_end = time.time()
            total_time = pipe_end - self._pipe_start
            llm_latency = (self._llm_first_token - self._pipe_start) if self._llm_first_token else 0
            tts_latency = (self._tts_first_audio - self._llm_first_token) if (self._tts_first_audio and self._llm_first_token) else 0
            rts_latency = (self._tts_first_audio - self._pipe_start) if self._tts_first_audio else 0
            audio_duration = self._tts_pcm_samples / self._tts_sample_rate if self._tts_sample_rate else 0
            rtf = total_time / audio_duration if audio_duration > 0 else 0
            self.logger.info(
                f"语音完成! {total_time:.1f}s | "
                f"RTS首包{rts_latency:.1f}s, LLM首响应{llm_latency:.1f}s, TTS首包{tts_latency:.1f}s | "
                f"音频{audio_duration:.1f}s, RTF {rtf:.2f}"
            )

        except Exception as e:
            self.logger.error(f"[会话] LLM/TTS 管道异常: {e}")
            await _send_json(self.ws, type="error", message=str(e))
        finally:
            if self.state not in (STATE_IDLE, STATE_LISTENING):
                if self._current_asr:
                    self.state = STATE_LISTENING
                else:
                    self.state = STATE_IDLE
                await _send_json(self.ws, type="status", state=self.state)

    async def _tts_synthesize(self, text: str):
        """执行单次 TTS 合成并发送音频（在线程池中运行同步 requests）"""
        from src.tts_client import generate_tts_stream

        if self._cancel_event.is_set():
            return

        self.state = STATE_SPEAKING
        await _send_json(self.ws, type="status", state=STATE_SPEAKING)

        tts_start = time.time()

        try:
            loop = asyncio.get_event_loop()
            sent_start = False
            pcm_samples_this_segment = 0

            # 从音色 ID 自动推断模型（克隆音色可能自带版本信息）
            actual_model = _resolve_tts_model(self.tts_voice, self.tts_model)
            if actual_model != self.tts_model:
                self.logger.debug(f"[TTS] 自动切换模型: {self.tts_model} → {actual_model}（音色 {self.tts_voice}）")
            # 在线程池中运行同步 TTS，逐块取回结果
            gen = generate_tts_stream(
                text=text,
                voice=self.tts_voice,
                model=actual_model,
                api_key=self.tts_api_key,
                instruction=self.tts_instruction,
            )

            # 包装函数：将 StopIteration 转为 None，避免 Future 异常
            _SENTINEL = object()

            def _next_item(g):
                try:
                    return next(g)
                except StopIteration:
                    return _SENTINEL

            while True:
                if self._cancel_event.is_set():
                    return

                result = await loop.run_in_executor(None, _next_item, gen)
                if result is _SENTINEL:
                    break

                try:
                    pcm_bytes, sr, is_final = result
                except Exception as e:
                    if not self._cancel_event.is_set():
                        self.logger.error(f"[TTS] 合成失败: {e}")
                    return

                if self._cancel_event.is_set():
                    return

                if pcm_bytes and not is_final:
                    # 累计 PCM 采样数（float32 = 4 bytes/sample）
                    pcm_samples_this_segment += len(pcm_bytes) // 4
                    self._tts_pcm_samples += len(pcm_bytes) // 4
                    self._tts_sample_rate = sr

                    if not sent_start:
                        await _send_json(self.ws, type="tts_start", sample_rate=sr)
                        sent_start = True
                        if not self._tts_first_audio:
                            self._tts_first_audio = time.time()

                    await _send_audio(self.ws, pcm_bytes)

            # 本段 TTS 结束，通知前端
            await _send_json(self.ws, type="tts_end")
            audio_dur = pcm_samples_this_segment / self._tts_sample_rate if self._tts_sample_rate else 0
            self.logger.debug(f"[TTS] 块合成完成: {text} | 音频{audio_dur:.1f}s")

        except Exception as e:
            if not self._cancel_event.is_set():
                self.logger.error(f"[TTS] 合成失败: {e}")

    # ── 打断 ──────────────────────────────────────────────────────

    async def _handle_interrupt(self):
        self.logger.info("[会话] 收到打断请求")
        self._cancel_event.set()

        if self._current_asr:
            try:
                await self._current_asr.close()
            except Exception:
                pass
            self._current_asr = None

        self.state = STATE_IDLE
        await _send_json(self.ws, type="status", state=STATE_IDLE)

    # ── 配置更新 ──────────────────────────────────────────────────

    async def _handle_update_config(self, msg):
        if "voice" in msg:
            self.tts_voice = msg["voice"]
            # 根据音色自动同步模型
            auto_model = _resolve_tts_model(self.tts_voice, self.tts_model)
            if auto_model != self.tts_model:
                self.tts_model = auto_model
                self.logger.info(f"[会话] 音色变更，模型自动同步为: {self.tts_model}")
        if "instruction" in msg:
            self.tts_instruction = msg.get("instruction", "")
        if "asr_model" in msg:
            self.asr_model = msg["asr_model"]
        if "asr_api_key" in msg and msg["asr_api_key"]:
            self.asr_api_key = msg["asr_api_key"]
        if "llm_model" in msg:
            self.llm_model = msg["llm_model"]
        if "llm_api_key" in msg and msg["llm_api_key"]:
            self.llm_api_key = msg["llm_api_key"]
        if "llm_base_url" in msg:
            self.llm_base_url = msg["llm_base_url"]
        if "tts_model" in msg:
            self.tts_model = msg["tts_model"]
        if "tts_api_key" in msg and msg["tts_api_key"]:
            self.tts_api_key = msg["tts_api_key"]
        if "tts_enabled" in msg:
            self.tts_enabled = msg["tts_enabled"]
        self.logger.info(f"[会话] 配置已更新: voice={self.tts_voice}, tts_model={self.tts_model}, tts_enabled={self.tts_enabled}")

    # ── 时间 API ───────────────────────────────────────────────

    async def _fetch_real_time(self) -> datetime | None:
        """调用外部 API 获取真实时间，失败则返回 None"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("https://worldtimeapi.org/api/timezone/Asia/Shanghai")
                if resp.status_code == 200:
                    data = resp.json()
                    dt_str = data.get("datetime", "")
                    if dt_str:
                        dt = datetime.fromisoformat(dt_str)
                        self.logger.info(f"[日记] 时间API返回: {dt}")
                        return dt
        except Exception as e:
            self.logger.warning(f"[日记] 时间API失败，使用本地时间: {e}")
        return None

    # ── 结束对话 → 写日记 ───────────────────────────────────────

    async def _handle_end_conversation(self):
        async with self._diary_lock:
            self.logger.info("[会话] 结束对话请求")
            if self.state != STATE_IDLE:
                self.logger.info("[会话] 当前非 IDLE 状态，忽略结束请求")
                return

            context = get_context("default")
            if not context:
                self.logger.info("[会话] 无对话上下文，直接清理")
                clear_context("default")
                await _send_json(self.ws, type="diary_saved", filename="")
                return

            # 获取真实时间
            real_dt = await self._fetch_real_time()
            now = real_dt if real_dt else datetime.now()

            # 生成日记
            try:
                loop = asyncio.get_event_loop()
                diary_text = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        generate_diary,
                        context,
                        self.llm_api_key,
                        self.llm_base_url,
                        self.llm_model,
                        now.month,
                        now.day,
                        self.persona_tier,
                    ),
                    timeout=35,
                )
            except Exception as e:
                self.logger.error(f"[日记] 生成异常: {e}")
                await _send_json(self.ws, type="error", message=f"日记生成失败: {e}")
                return

            if not diary_text.strip():
                self.logger.warning("[日记] 日记内容为空，跳过保存")
                clear_context("default")
                self.emotions = {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0, "love": 0.0, "surprise": 0.0, "trust": 0.0}
                self.intimacy = 0.0
                self.persona_tier = 1
                await _send_json(self.ws, type="emotion", **self.emotions)
                await _send_json(self.ws, type="diary_saved", filename="")
                return

            # 文件名精确到秒，避免覆盖
            filename = now.strftime("%Y-%m-%d-%H-%M-%S.md")
            filepath = DIARY_DIR / filename
            tmp = filepath.with_suffix(".tmp")
            tmp.write_text(diary_text, encoding="utf-8")
            tmp.rename(filepath)
            self.logger.info(f"[日记] 已保存: {filepath}")

            # 清理上下文
            clear_context("default")
            self.emotions = {"joy": 0.0, "sadness": 0.0, "anger": 0.0, "fear": 0.0, "love": 0.0, "surprise": 0.0, "trust": 0.0}
            self.intimacy = 0.0
            self.persona_tier = 1
            await _send_json(self.ws, type="emotion", **self.emotions)

            await _send_json(self.ws, type="diary_saved", filename=filename)


# ── HTTP 路由 ───────────────────────────────────────────────────────


_MIME_MAP = {
    ".html": ("text/html", "utf-8"),
    ".js": ("application/javascript", None),
    ".css": ("text/css", None),
    ".json": ("application/json", None),
    ".svg": ("image/svg+xml", None),
    ".png": ("image/png", None),
    ".ico": ("image/x-icon", None),
    ".woff2": ("font/woff2", None),
}


def _guess_mime(path: Path):
    return _MIME_MAP.get(path.suffix.lower(), ("application/octet-stream", None))


async def api_voices(request):
    """GET /api/voices — 返回可用音色列表（支持 ?model= 过滤）"""
    from src.tts_client import list_voices
    api_key = os.environ.get("QWEN_TTS_API_KEY", "")
    model = request.query.get("model", "")
    voices = list_voices(api_key, model=model)
    return web.json_response(voices)


async def api_character(request):
    """GET /api/character — 返回角色信息（姓名、头像路径）"""
    from src.llm_client import get_character_info
    return web.json_response(get_character_info())


async def api_diaries(request):
    """GET /api/diaries — 返回日记文件列表（按时间倒序）"""
    DIARY_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(DIARY_DIR.glob("*.md"), reverse=True)
    entries = []
    for f in files:
        parts = f.stem.split("-")
        if len(parts) >= 5:
            time_str = f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}:{parts[4]}"
        elif len(parts) == 4:
            time_str = f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}:00"
        else:
            time_str = f.stem
        entries.append({"name": f.name, "time": time_str})
    return web.json_response(entries)


async def api_diary_detail(request):
    """GET /api/diaries/{name} — 返回日记文件内容"""
    name = request.match_info.get("name", "")
    if not name.endswith(".md") or "/" in name or "\\" in name:
        return web.Response(status=400, text="Invalid filename")
    filepath = DIARY_DIR / name
    if not filepath.exists():
        return web.Response(status=404, text="Not found")
    body = filepath.read_text(encoding="utf-8")
    return web.Response(body=body, content_type="text/plain", charset="utf-8")


async def static_files(request):
    """静态文件服务 + SPA fallback"""
    try:
        static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
        if not static_dir.exists():
            static_dir = Path(__file__).resolve().parent.parent / "frontend"

        path = request.match_info.get("path", "") or "index.html"

        file_path = static_dir / path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            mime, cs = _guess_mime(file_path)
            body = file_path.read_bytes()
            return web.Response(body=body, content_type=mime, charset=cs)

        # SPA fallback
        index_path = static_dir / "index.html"
        if index_path.exists():
            body = index_path.read_bytes()
            return web.Response(body=body, content_type="text/html", charset="utf-8")

        return web.Response(status=404, text="Not Found")
    except Exception as e:
        return web.Response(status=500, text=f"static error: {e}")


# ── WebSocket 处理 ──────────────────────────────────────────────────


async def ws_voice_chat(request):
    """WebSocket /ws/voice-chat"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logger = get_logger()
    session = VoiceChatSession(ws, logger)
    client_addr = request.remote
    logger.info(f"[连接] 新客户端: {client_addr}")

    await _send_json(ws, type="status", state=STATE_IDLE)

    # 发送服务端初始配置给前端
    await _send_json(ws, type="config", voice=session.tts_voice,
                     instruction=session.tts_instruction,
                     asr_model=session.asr_model,
                     llm_model=session.llm_model,
                     llm_base_url=session.llm_base_url,
                     tts_model=session.tts_model)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                await session.handle_message(msg.data)
            elif msg.type == web.WSMsgType.BINARY:
                await session.handle_message(msg.data)
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"[WS] 错误: {ws.exception()}")
                break
    except Exception as e:
        logger.error(f"[连接] 异常: {e}")
    finally:
        session._cancel_event.set()
        if session._current_asr:
            try:
                await session._current_asr.close()
            except Exception:
                pass
        logger.info(f"[连接] 客户端断开: {client_addr}")

    return ws


# ── 启动服务 ───────────────────────────────────────────────────────


async def health(request):
    """健康检查端点（Railway 用）"""
    return web.json_response({"status": "ok"})


def main(host=DEFAULT_HOST, port=DEFAULT_PORT):
    logger = setup_logger("voice-chat")
    logger.info("=== Real-time-voice 服务启动 ===")
    logger.info(f"HTTP + WS 地址: http://{host}:{port}")
    logger.info("API: GET /api/voices")

    asr_key = os.environ.get("ASR_API_KEY") or os.environ.get("IMAGE_API_KEY", "")
    llm_key = os.environ.get("LLM_API_KEY", "")
    tts_key = os.environ.get("QWEN_TTS_API_KEY") or os.environ.get("IMAGE_API_KEY", "")

    if not asr_key:
        logger.warning("[配置] ASR_API_KEY 未设置，语音识别不可用")
    if not llm_key:
        logger.warning("[配置] LLM_API_KEY 未设置，对话不可用")
    if not tts_key:
        logger.warning("[配置] QWEN_TTS_API_KEY 未设置，语音合成不可用")

    logger.info(f"[配置] ASR: fun-asr-realtime")
    logger.info(f"[配置] LLM: {os.environ.get('LLM_MODEL', 'deepseek-v4-flash')} @ {os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')}")
    logger.info(f"[配置] TTS: {os.environ.get('QWEN_TTS_MODEL', 'cosyvoice-v3.5-flash')}")

    app = web.Application()

    # 健康检查
    app.router.add_get("/health", health)

    # API 路由
    app.router.add_get("/api/voices", api_voices)
    app.router.add_get("/api/character", api_character)
    app.router.add_get("/api/diaries", api_diaries)
    app.router.add_get("/api/diaries/{name}", api_diary_detail)

    # WebSocket 路由
    app.router.add_get("/ws/voice-chat", ws_voice_chat)

    # 图片目录（/img/ → 项目根 img/）
    img_dir = Path(__file__).resolve().parent.parent / "img"
    if img_dir.exists():
        app.router.add_static("/img", str(img_dir))

    # 静态文件（SPA fallback）
    app.router.add_get("/{path:.*}", static_files)

    logger.info(f"服务就绪 → ws://{host}:{port}/ws/voice-chat")
    logger.info("按 Ctrl+C 停止服务")

    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Real-time-voice 服务")
    parser.add_argument("--host", default=DEFAULT_HOST, help="绑定地址")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)), help="绑定端口")
    args = parser.parse_args()
    main(args.host, args.port)
