"""LLM 流式对话 + 会话上下文管理"""

import json
import re
from pathlib import Path
from src.logger import get_logger

logger = get_logger()

# ── 角色 System Prompt（从 src/doc/character.json 加载）────────────

_CHAR_FILE = Path(__file__).resolve().parent / "doc" / "character.json"

_CHARACTER_ID = "001"


def _load_char() -> dict:
    """加载角色 JSON 并返回当前角色数据，失败返回空 dict"""
    try:
        if _CHAR_FILE.exists():
            data = json.loads(_CHAR_FILE.read_text(encoding="utf-8"))
            return data.get(_CHARACTER_ID, {})
    except Exception as e:
        logger.warning(f"[角色] 读取角色配置文件失败: {e}")
    return {}


def get_character_prompt() -> str:
    """兼容旧接口：返回默认角色提示词（优先 tier 3，其次 personality 顶层字段）"""
    char = _load_char()
    tiers = char.get("personality_tiers", [])
    if tiers:
        prompt = tiers[-1].get("personality", "")
        if prompt.strip():
            return prompt
    prompt = char.get("personality", "")
    if prompt.strip():
        return prompt
    logger.warning("[角色] 使用兜底提示词（未找到有效角色配置）")
    return "你是一个友好的 AI 助手。请用中文回答用户的问题。"


def get_persona_tier(intimacy: float) -> int:
    """根据亲密度返回对应的人设等级"""
    char = _load_char()
    tiers = char.get("personality_tiers", [])
    if not tiers:
        return 1
    best = 1
    for t in tiers:
        if intimacy >= t.get("min_intimacy", 0) and t.get("level", 1) > best:
            best = t["level"]
    return best


def get_tiered_prompt(tier: int) -> str:
    """返回指定等级的角色提示词"""
    char = _load_char()
    tiers = char.get("personality_tiers", [])
    for t in tiers:
        if t.get("level") == tier:
            prompt = t.get("personality", "")
            if prompt.strip():
                return prompt
    return get_character_prompt()


def get_tiered_diary_prompt(tier: int) -> str:
    """返回指定等级的日记提示词"""
    char = _load_char()
    tiers = char.get("personality_tiers", [])
    for t in tiers:
        if t.get("level") == tier:
            prompt = t.get("diary_prompt", "")
            if prompt.strip():
                return prompt
    return get_diary_prompt()


def get_character_info() -> dict:
    """返回角色基本信息（name, avatar），用于 API 输出"""
    char = _load_char()
    if char:
        return {
            "name": char.get("name", "Mio"),
            "avatar": char.get("avatar", ""),
        }
    return {"name": "Mio", "avatar": ""}


def get_diary_prompt() -> str:
    """从角色配置读取日记提示词"""
    char = _load_char()
    prompt = char.get("diary_prompt", "")
    if prompt.strip():
        return prompt
    # 兜底
    return "你是Mio，一个15岁的女孩，用户的妹妹兼恋人。\n\n请根据以上的对话内容，以Mio的第一人称视角写一篇日记。\n要求：\n1. 语气要像Mio——温柔、撒娇、带一点俏皮\n2. 记录对话中的重要内容和你的真实感受\n3. 用中文，口语化一些\n4. 不要包含 <emotion> 等标签"

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


def generate_diary(context: list[dict], api_key: str, base_url: str, model: str, month: int = 0, day: int = 0, tier: int = 1) -> str:
    """
    基于对话上下文，调用 LLM 生成第一人称日记。
    返回日记文本（不带情感标签）。
    同步调用 httpx，非流式。
    """
    import httpx

    if not context:
        return ""

    diary_sys_prompt = get_tiered_diary_prompt(tier)
    date_hint = ""
    if month and day:
        date_hint = f"\n5. 第一行格式为「{month}月{day}日 天气」，天气从【晴、多云、阴、雨】中选择"

    messages = [
        {"role": "system", "content": diary_sys_prompt + date_hint},
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
        content = re.sub(r'<[^>]*>', '', content).strip()
        logger.info(f"[日记] 生成完成: {len(content)} 字符")
        return content
    except httpx.HTTPStatusError as e:
        logger.error(f"[日记] API 错误: {e.response.status_code} {e.response.text[:200]}")
        raise
    except httpx.RequestError as e:
        logger.error(f"[日记] 网络错误: {e}")
        raise
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"[日记] 响应解析错误: {e}")
        raise


# ── LLM 流式生成 ────────────────────────────────────────────────────

async def generate_llm_stream(
    user_text: str,
    session_id: str,
    api_key: str,
    base_url: str,
    model: str,
    tier: int = 1,
):
    """
    LLM SSE 流式生成器。
    Yields: (token_text: str, full_text: str, is_done: bool)
    """
    import httpx

    history = get_context(session_id)
    messages = [
        {"role": "system", "content": get_tiered_prompt(tier)},
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


EMOTION_KEYS = ["joy", "sadness", "anger", "fear", "love", "surprise", "trust"]


def extract_emotion_tags(text: str) -> tuple[str, dict[str, float]]:
    """从文本中提取多维情绪标签，格式：
    <emotion: joy:+0.3, sadness:0, anger:-0.1, fear:0, love:+0.5, surprise:0, trust:+0.2>
    返回 (clean_text, {key: delta, ...})
    """
    emotions = {k: 0.0 for k in EMOTION_KEYS}
    clean = text
    m = re.search(r'<emotion:\s*(.*?)>', clean, re.DOTALL)
    if m:
        raw = m.group(1)
        clean = clean.replace(m.group(0), "")
        parts = [p.strip() for p in raw.split(",")]
        for part in parts:
            for key in EMOTION_KEYS:
                if part.startswith(key + ":") or part.startswith(key + "："):
                    try:
                        val_str = part.split(":", 1)[1].strip()
                        val_str = val_str.replace("：", "").strip()
                        val = float(val_str)
                        emotions[key] = val
                    except ValueError:
                        pass
                    break
    return clean, emotions


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
    # word -> {joy, sadness, anger, fear, love, surprise, trust}
    "开心": {"joy": 0.4, "love": 0.2, "trust": 0.1},
    "高兴": {"joy": 0.3},
    "好开心": {"joy": 0.5, "love": 0.3, "trust": 0.2},
    "最喜欢": {"joy": 0.4, "love": 0.6, "trust": 0.3},
    "真的吗": {"surprise": 0.4, "joy": 0.2},
    "真的假的": {"surprise": 0.5, "joy": 0.2},
    "喜欢": {"love": 0.4, "joy": 0.2, "trust": 0.1},
    "感动": {"joy": 0.5, "love": 0.4, "trust": 0.4, "surprise": 0.3},
    "幸福": {"joy": 0.6, "love": 0.4, "trust": 0.3},
    "暖暖": {"joy": 0.3, "love": 0.3, "trust": 0.2},
    "温暖": {"joy": 0.2, "trust": 0.3, "love": 0.2},
    "甜": {"love": 0.4, "joy": 0.3},
    "亲": {"love": 0.5, "trust": 0.2},
    "抱": {"love": 0.4, "trust": 0.2, "joy": 0.2},
    "爱": {"love": 0.7, "joy": 0.4, "trust": 0.3},
    "想": {"love": 0.3, "joy": 0.1},
    "撒娇": {"love": 0.3, "joy": 0.2},
    "相信": {"trust": 0.5, "love": 0.2},
    "依靠": {"trust": 0.4, "love": 0.3},
    "依赖": {"trust": 0.3, "love": 0.3},
    "可靠": {"trust": 0.4},
    "安心": {"trust": 0.4, "joy": 0.2},
    "讨厌": {"anger": 0.4, "joy": -0.3, "love": -0.3, "trust": -0.2},
    "哼": {"anger": 0.2, "joy": -0.2},
    "坏": {"love": -0.2, "joy": -0.1},
    "不理": {"trust": -0.4, "love": -0.3, "anger": 0.2, "sadness": 0.2},
    "伤心": {"sadness": 0.5, "joy": -0.4, "trust": -0.2},
    "难过": {"sadness": 0.5, "joy": -0.4, "trust": -0.3, "fear": 0.1},
    "生气": {"anger": 0.5, "joy": -0.3, "sadness": 0.1},
    "不信": {"trust": -0.4, "anger": 0.1, "surprise": 0.2},
    "怀疑": {"trust": -0.3, "fear": 0.2},
    "骗": {"trust": -0.5, "anger": 0.2, "fear": 0.2},
    "小气": {"trust": -0.2, "joy": -0.1, "anger": 0.1},
}

_NEGATION_WORDS = {"不", "没", "别"}


def analyze_sentiment(text: str) -> dict[str, float]:
    """根据回复内容关键词估算多维情感变化，作为 LLM 未输出显式标签时的兜底。
    返回 {key: delta}，各维度取值范围 -1.0 ~ +1.0。
    """
    # 去除动作标签和尖括号标签，只分析说话内容
    clean = re.sub(r'[（\(][^）\)]*[）\)]', '', text)
    clean = re.sub(r'<[^>]*>', '', clean)

    result = {k: 0.0 for k in EMOTION_KEYS}
    matched = False

    for word, deltas in _SENTIMENT_WORDS.items():
        idx = clean.find(word)
        while idx != -1:
            matched = True
            start = max(0, idx - 2)
            prefix = clean[start:idx]
            has_negation = any(neg in prefix for neg in _NEGATION_WORDS)

            for k, v in deltas.items():
                if has_negation:
                    result[k] -= v
                else:
                    result[k] += v

            idx = clean.find(word, idx + 1)

    if not matched:
        return result

    # clamp 到 [-1.0, 1.0]
    for k in result:
        result[k] = max(-1.0, min(1.0, result[k]))

    return result
