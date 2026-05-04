"""LLM 流式对话 + 会话上下文管理"""

import json
import re
from pathlib import Path
from src.logger import get_logger

logger = get_logger()

# ── 角色 System Prompt（从 src/doc/character.json 加载）────────────

_CHAR_FILE = Path(__file__).resolve().parent / "doc" / "character.json"

_CHARACTER_PROMPT: str | None = None


def get_character_prompt() -> str:
    global _CHARACTER_PROMPT
    if _CHARACTER_PROMPT is not None:
        return _CHARACTER_PROMPT

    try:
        if _CHAR_FILE.exists():
            data = json.loads(_CHAR_FILE.read_text(encoding="utf-8"))
            prompt = data.get("system_prompt", "")
            if prompt.strip():
                _CHARACTER_PROMPT = prompt
                logger.info(f"[角色] 已加载角色配置: {data.get('name', 'unknown')}")
                return _CHARACTER_PROMPT
    except Exception as e:
        logger.warning(f"[角色] 读取角色配置文件失败: {e}")

    # 兜底：极简角色描述
    _CHARACTER_PROMPT = "你是一个友好的 AI 助手。请用中文回答用户的问题。"
    logger.warning("[角色] 使用兜底提示词（未找到有效角色配置）")
    return _CHARACTER_PROMPT


def get_character_info() -> dict:
    """返回角色基本信息（name, avatar），用于 API 输出"""
    try:
        if _CHAR_FILE.exists():
            data = json.loads(_CHAR_FILE.read_text(encoding="utf-8"))
            return {
                "name": data.get("name", "Mio"),
                "avatar": data.get("avatar", ""),
            }
    except Exception:
        pass
    return {"name": "Mio", "avatar": ""}

# ── 会话上下文管理 ──────────────────────────────────────────────────

_chat_contexts: dict[str, list[dict]] = {}
CHAT_MAX_TURNS = 100


def get_context(session_id: str) -> list[dict]:
    return _chat_contexts.get(session_id, [])


def add_to_context(session_id: str, role: str, content: str):
    if session_id not in _chat_contexts:
        _chat_contexts[session_id] = []
    _chat_contexts[session_id].append({"role": role, "content": content})
    max_entries = CHAT_MAX_TURNS * 2
    if len(_chat_contexts[session_id]) > max_entries:
        _chat_contexts[session_id] = _chat_contexts[session_id][-max_entries:]


def clear_context(session_id: str):
    _chat_contexts.pop(session_id, None)


# ── 日记生成 ──────────────────────────────────────────────────────

DIARY_SYSTEM_PROMPT = """你是Mio，一个15岁的女孩，用户的妹妹兼恋人。

请根据以上的对话内容，以Mio的第一人称视角写一篇日记。
要求：
1. 语气要像Mio——温柔、撒娇、带一点俏皮
2. 记录对话中的重要内容和你的真实感受
3. 用中文，口语化一些
4. 不要包含 <好感> 或 <信任> 等标签"""


def generate_diary(context: list[dict], api_key: str, base_url: str, model: str) -> str:
    """
    基于对话上下文，调用 LLM 生成第一人称日记。
    返回日记文本（不带情感标签）。
    同步调用 httpx，非流式。
    """
    import httpx

    if not context:
        return ""

    messages = [
        {"role": "system", "content": DIARY_SYSTEM_PROMPT},
    ] + context + [{"role": "user", "content": "写一篇日记吧。"}]

    llm_url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "stream": False,
    }

    try:
        resp = httpx.post(llm_url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info(f"[日记] 生成完成: {len(content)} 字符")
        return content
    except Exception as e:
        logger.error(f"[日记] 生成失败: {e}")
        raise


# ── LLM 流式生成 ────────────────────────────────────────────────────

async def generate_llm_stream(
    user_text: str,
    session_id: str,
    api_key: str,
    base_url: str,
    model: str,
):
    """
    LLM SSE 流式生成器。
    Yields: (token_text: str, full_text: str, is_done: bool)
    """
    import httpx

    history = get_context(session_id)
    messages = [
        {"role": "system", "content": get_character_prompt()},
    ] + history + [{"role": "user", "content": user_text}]

    llm_url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": 8192,
        "stream": True,
    }

    full_text = ""
    reasoning_text = ""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", llm_url, headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    err_text = await resp.aread()
                    raise RuntimeError(f"LLM API {resp.status_code}: {err_text.decode()[:200]}")

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content_token = delta.get("content") or ""
                        reasoning_token = delta.get("reasoning_content") or ""
                        if content_token:
                            full_text += content_token
                        if reasoning_token:
                            reasoning_text += reasoning_token
                    except json.JSONDecodeError:
                        continue

                    yield (content_token, full_text, False)

    except Exception as e:
        logger.error(f"[LLM] 请求异常: {e}")
        raise

    if not full_text.strip() and reasoning_text.strip():
        logger.info(f"[LLM] content 为空，使用 reasoning_content 兜底 ({len(reasoning_text)} 字)")
        full_text = reasoning_text
        yield (full_text, full_text, False)

    add_to_context(session_id, "user", user_text)
    add_to_context(session_id, "assistant", full_text)

    yield ("", full_text, True)
    logger.info(f"[LLM] 生成完成: {len(full_text)} 字符")


# ── 文本分块工具 ─────────────────────────────────────────────────────

def should_trigger_tts(text_buffer: str) -> bool:
    """检查是否应该触发 TTS 分块：空格或标点作为自然断点"""
    if not text_buffer:
        return False
    # 有空格作为分隔（括号/标签闭合后空格是自然断句）
    if ' ' in text_buffer:
        return True
    # 末尾有中英文标点或闭合括号/标签
    if text_buffer[-1] in '，。！？、；：,.!?;:）)\n>':
        return True
    # 足够长时强制触发（让 TTS 尽早开始流式输出）
    if len(text_buffer) >= 20:
        return True
    return False


def extract_emotion_tags(text: str) -> tuple[str, int, int]:
    """从文本中提取好感/信任变化标签，支持多种格式：
    <好感:+3> <好感变化:+3> <好感：+3> <好感变化：+3>
    """
    affection = 0
    trust = 0
    clean = text
    m = re.search(r'<好感(?:变化)?[：:]\s*([+-]?\d+)>', clean)
    if m:
        affection = int(m.group(1))
        clean = clean.replace(m.group(0), "")
    m = re.search(r'<信任(?:变化)?[：:]\s*([+-]?\d+)>', clean)
    if m:
        trust = int(m.group(1))
        clean = clean.replace(m.group(0), "")
    return clean, affection, trust


def strip_action_tags(text: str) -> str:
    text = re.sub(r'[（\(][^）\)]*[）\)]', '', text)
    text = re.sub(r'<[^>]*>', '', text)
    return text.strip()


def prepare_tts_text(text: str) -> str:
    text = strip_action_tags(text)
    text = re.sub(r'[，、,；;：:！!？?。.\n]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_tts_chunks(text: str, max_chunk_len: int = 15) -> list[str]:
    """将文本分割为 TTS 块：动作标签替换为空格（保留分割点），按空格分割，不切断连续话语"""
    # 移除尖括号标签（如情感标签，已在前面提取）
    text = re.sub(r'<[^>]*>', '', text)
    # 动作标签替换为空格，保留分割点而非删除
    text = re.sub(r'[（\(][^）\)]*[）\)]', ' ', text)
    # 标点替换为空格
    text = re.sub(r'[，、,；;：:！!？?。.\n]', ' ', text)
    # 按空格分割
    chunks = re.split(r'\s+', text)
    chunks = [c.strip() for c in chunks if c.strip()]
    # 对超长块按语义断点拆分
    result = []
    for ch in chunks:
        if len(ch) <= max_chunk_len:
            result.append(ch)
        else:
            # 按语义断点拆分：在 了/的/呢/吗/吧/啊/哦/呀/啦 等语气词后拆分
            sub_chunks = re.split(r'(?<=[了呢吗吧啊哦呀啦着过])', ch)
            current = ""
            for sub in sub_chunks:
                if len(current) + len(sub) <= max_chunk_len:
                    current += sub
                else:
                    if current:
                        result.append(current)
                    current = sub
            if current:
                result.append(current)
    return result


# ── 情感分析兜底 ──────────────────────────────────────────────────────

_SENTIMENT_WORDS = {
    # (word, affection_change, trust_change, weight)
    "开心": (3, 0),
    "高兴": (2, 0),
    "好开心": (3, 1),
    "超级": (2, 0),
    "最喜欢": (3, 2),
    "真的吗": (2, 1),
    "真的假的": (2, 1),
    "喜欢": (2, 1),
    "感动": (3, 3),
    "幸福": (3, 2),
    "暖暖": (2, 1),
    "温暖": (1, 2),
    "甜": (2, 1),
    "亲": (2, 1),
    "抱": (2, 1),
    "爱": (3, 2),
    "想": (1, 0),
    "撒娇": (2, 1),
    "相信": (0, 2),
    "依靠": (1, 2),
    "依赖": (1, 2),
    "可靠": (0, 2),
    "安心": (1, 2),
    "讨厌": (-3, -1),
    "哼": (-2, -1),
    "坏": (-1, 0),
    "不理": (-2, -2),
    "伤心": (-3, -1),
    "难过": (-3, -2),
    "生气": (-2, -1),
    "不信": (-1, -2),
    "怀疑": (0, -2),
    "骗": (-1, -3),
    "小气": (-1, -1),
}

_NEGATION_WORDS = {"不", "没", "别"}


def analyze_sentiment(text: str) -> tuple[int, int]:
    """根据回复内容关键词估算情感变化，作为 LLM 未输出显式标签时的兜底。
    返回 (affection_change, trust_change)，取值范围 -3 ~ +3。
    """
    # 去除动作标签和尖括号标签，只分析说话内容
    clean = re.sub(r'[（\(][^）\)]*[）\)]', '', text)
    clean = re.sub(r'<[^>]*>', '', clean)

    total_aff = 0
    total_tru = 0
    matched = False

    for word, (aff, tru) in _SENTIMENT_WORDS.items():
        idx = clean.find(word)
        while idx != -1:
            matched = True
            # 检查前面是否有否定词（往前看 2 个字符）
            start = max(0, idx - 2)
            prefix = clean[start:idx]
            has_negation = any(neg in prefix for neg in _NEGATION_WORDS)

            if has_negation:
                total_aff -= aff
                total_tru -= tru
            else:
                total_aff += aff
                total_tru += tru

            idx = clean.find(word, idx + 1)

    # 没有匹配到任何关键词时返回中性
    if not matched:
        return (0, 0)

    # clamp 到 [-3, 3]
    total_aff = max(-3, min(3, total_aff))
    total_tru = max(-3, min(3, total_tru))

    return (total_aff, total_tru)
