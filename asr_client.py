"""Fun-ASR Realtime 流式语音识别（DashScope WebSocket）"""

import json
import asyncio
import uuid
from logger import get_logger

logger = get_logger()


class ASRClient:
    """Fun-ASR Realtime WebSocket 客户端"""

    DASHSCOPE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

    def __init__(self, api_key: str, model: str = "fun-asr-realtime"):
        self.api_key = api_key
        self.model = model
        self._ws = None
        self._task_id = None
        self._on_partial = None
        self._on_final = None
        self._on_error = None
        self._finished = asyncio.Event()
        self._recv_task = None

    def set_callbacks(self, on_partial=None, on_final=None, on_error=None):
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error

    async def connect(self):
        """连接到 Fun-ASR WebSocket（新版 api-ws 端点）"""
        import websockets
        headers = {
            "Authorization": f"bearer {self.api_key}",
            "X-DashScope-DataInspection": "enable",
        }
        self._task_id = f"asr-{uuid.uuid4().hex[:8]}"
        logger.info(f"[ASR] 连接到 Fun-ASR: model={self.model} task_id={self._task_id}")
        self._ws = await websockets.connect(
            self.DASHSCOPE_WS_URL,
            additional_headers=headers,
            max_size=2**24,
        )

        task_msg = {
            "header": {
                "task_id": self._task_id,
                "action": "run-task",
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "enable_intermediate_result": True,
                    "enable_punctuation": True,
                    "enable_semantic_sentence_detection": True,
                },
                "input": {},
            },
        }
        await self._ws.send(json.dumps(task_msg))
        self._finished.clear()
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("[ASR] 已连接，等待识别结果")

    async def send_audio(self, pcm_bytes: bytes):
        """发送音频数据（16kHz, 16bit, mono PCM）"""
        if self._ws:
            try:
                await self._ws.send(pcm_bytes)
            except Exception as e:
                logger.error(f"[ASR] 发送音频失败: {e}")

    async def finish(self):
        """发送 finish-task 并等待服务端确认"""
        if self._ws:
            try:
                finish_msg = {
                    "header": {
                        "task_id": self._task_id,
                        "action": "finish-task",
                    },
                    "payload": {"input": {}},
                }
                await self._ws.send(json.dumps(finish_msg))
                logger.info("[ASR] 已发送 finish-task，等待 task-finished")
                # 等至多 5 秒让服务端返回 task-finished
                try:
                    await asyncio.wait_for(self._finished.wait(), timeout=5.0)
                    logger.info("[ASR] 收到 task-finished")
                except asyncio.TimeoutError:
                    logger.warning("[ASR] 等待 task-finished 超时")
            except Exception as e:
                logger.error(f"[ASR] 发送finish失败: {e}")

    async def close(self):
        """关闭连接"""
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _recv_loop(self):
        """接收 ASR 结果的后台循环"""
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    continue
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    continue

                header = msg.get("header", {})
                payload = msg.get("payload", {})
                event = header.get("event", "")

                if event == "task-failed":
                    err_code = header.get("error_code", "")
                    err_msg = header.get("status_text", "") or header.get("error_message", "") or "ASR task failed"
                    logger.error(f"[ASR] task-failed: {err_code} {err_msg}")
                    self._finished.set()
                    if self._on_error:
                        self._on_error(f"{err_code} {err_msg}")
                    break

                if event == "task-finished":
                    logger.info("[ASR] task-finished")
                    self._finished.set()
                    break

                # 新版 API 结果在 payload.output 中
                output = payload.get("output", {})
                sentence = output.get("sentence", {})

                if not sentence:
                    # 兼容旧格式
                    result = payload.get("result", "")
                    is_final = payload.get("is_final", False)
                    if result:
                        if is_final:
                            logger.info(f"[ASR] final: {result}")
                            if self._on_final:
                                self._on_final(result)
                        else:
                            if self._on_partial:
                                self._on_partial(result)
                    continue

                text = sentence.get("text", "")
                is_final = sentence.get("end_time", None) is not None or output.get("is_final", False)

                if text:
                    if is_final:
                        logger.info(f"[ASR] final: {text}")
                        if self._on_final:
                            self._on_final(text)
                    else:
                        if self._on_partial:
                            self._on_partial(text)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[ASR] 接收循环异常: {e}")
            self._finished.set()
            if self._on_error:
                self._on_error(str(e))
