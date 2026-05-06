# 日记功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add "End Conversation" button (LLM generates a first-person diary entry from Mio's perspective, clears context) and a diary browsing page.

**Architecture:** Client sends `end_conversation` WS message → server passes conversation context to LLM with diary prompt → saves result to `src/diary/YYYY-MM-DD-HH.md` → clears context. Diary panel fetches list via `GET /api/diaries`.

**Tech Stack:** Python aiohttp + httpx (backend), Vue 3 + Vite (frontend)

**Files to modify:**
- `src/llm_client.py` — add `generate_diary()`
- `src/server.py` — add `end_conversation` handler + diary HTTP API
- `frontend/src/components/ControlBar.vue` — add end-conversation button
- `frontend/src/App.vue` — add diary state/WS handlers/header button
- `frontend/src/components/DiaryPanel.vue` — new diary browser panel

---

### Task 1: Add `generate_diary()` to llm_client.py

**File:** `src/llm_client.py`

Add after `clear_context()` (line 72), before the `generate_llm_stream` section:

```python
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
```

- [ ] **Step 1: Add `generate_diary()` function**

  Insert the code block above into `src/llm_client.py` after line 72 (after `clear_context`).

- [ ] **Step 2: Verify import works**

  Run: `python -c "from src.llm_client import generate_diary; print('OK')"`
  Expected: `OK`

---

### Task 2: Add `end_conversation` handler + diary APIs to server.py

**File:** `src/server.py`

Changes:
1. Import `generate_diary` and `get_context`/`clear_context` from `llm_client`
2. Add `end_conversation` handler in `handle_message`
3. Add diary HTTP API endpoints
4. Create diary directory on server start

- [ ] **Step 1: Add diary directory constant at top of file**

  After `DEFAULT_PORT = 9902` (line 28), add:

  ```python
  DIARY_DIR = Path(__file__).resolve().parent / "diary"
  ```

- [ ] **Step 2: Update import in `handle_message` (line 242)**

  In the `_start_llm_tts_pipeline` method, the import at line 242 is inside the method. For `end_conversation`, add a module-level import reference.

  Add this after `from src.logger import setup_logger, get_logger` (line 18):

  ```python
  from src.llm_client import get_context, clear_context, generate_diary
  ```

- [ ] **Step 3: Add `end_conversation` route in `handle_message`**

  In `handle_message` (after line 153, the `update_config` handler), add:

  ```python
  elif msg_type == "end_conversation":
      await self._handle_end_conversation()
  ```

- [ ] **Step 4: Add `_handle_end_conversation` method**

  Add after `_handle_update_config` (after line 521), before the `# ── HTTP 路由 ──` section:

  ```python
  # ── 结束对话 → 写日记 ───────────────────────────────────────

  async def _handle_end_conversation(self):
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

      # 生成日记
      try:
          loop = asyncio.get_event_loop()
          diary_text = await loop.run_in_executor(
              None,
              generate_diary,
              context,
              self.llm_api_key,
              self.llm_base_url,
              self.llm_model,
          )
      except Exception as e:
          self.logger.error(f"[日记] 生成异常: {e}")
          await _send_json(self.ws, type="error", message=f"日记生成失败: {e}")
          return

      if not diary_text.strip():
          self.logger.warning("[日记] 日记内容为空，跳过保存")
          clear_context("default")
          await _send_json(self.ws, type="diary_saved", filename="")
          return

      # 保存到文件
      DIARY_DIR.mkdir(parents=True, exist_ok=True)
      now = time.localtime()
      filename = f"{now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d}-{now.tm_hour:02d}.md"
      filepath = DIARY_DIR / filename
      header = f"# {now.tm_year:04d}-{now.tm_mon:02d}-{now.tm_mday:02d} {now.tm_hour:02d}:00\n\n"
      filepath.write_text(header + diary_text, encoding="utf-8")
      self.logger.info(f"[日记] 已保存: {filepath}")

      # 清理上下文
      clear_context("default")
      self.affection = 0
      self.trust = 0
      await _send_json(self.ws, type="emotion", affection=0, trust=0)

      await _send_json(self.ws, type="diary_saved", filename=filename)
  ```

- [ ] **Step 5: Add diary HTTP API endpoints**

  Add after `api_character` (after line 555):

  ```python
  async def api_diaries(request):
      """GET /api/diaries — 返回日记文件列表（按时间倒序）"""
      DIARY_DIR.mkdir(parents=True, exist_ok=True)
      files = sorted(DIARY_DIR.glob("*.md"), reverse=True)
      entries = []
      for f in files:
          # "2026-05-05-14.md" → "2026-05-05 14:00"
          parts = f.stem.split("-")
          if len(parts) >= 4:
              time_str = f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}:00"
          else:
              time_str = f.stem
          entries.append({"name": f.name, "time": time_str})
      return web.json_response(entries)


  async def api_diary_detail(request):
      """GET /api/diaries/{name} — 返回日记文件内容"""
      name = request.match_info.get("name", "")
      # 安全校验：只允许 .md 文件，防止路径穿越
      if not name.endswith(".md") or "/" in name or "\\" in name:
          return web.Response(status=400, text="Invalid filename")
      filepath = DIARY_DIR / name
      if not filepath.exists():
          return web.Response(status=404, text="Not found")
      body = filepath.read_text(encoding="utf-8")
      return web.Response(body=body, content_type="text/plain; charset=utf-8")
  ```

- [ ] **Step 6: Register diary routes in `main()`**

  In `main()` (after line 666, `app.router.add_get("/api/character", api_character)`), add:

  ```python
  app.router.add_get("/api/diaries", api_diaries)
  app.router.add_get("/api/diaries/{name}", api_diary_detail)
  ```

- [ ] **Step 7: Verify server starts**

  Run: `python -c "from src.server import main; print('OK')"`
  Expected: `OK`

---

### Task 3: Add end-conversation button to ControlBar.vue

**File:** `frontend/src/components/ControlBar.vue`

- [ ] **Step 1: Add emit to defineEmits**

  Change line 60 from:
  ```js
  const emit = defineEmits(['send-text', 'start-voice', 'stop-voice', 'open-settings'])
  ```
  to:
  ```js
  const emit = defineEmits(['send-text', 'start-voice', 'stop-voice', 'open-settings', 'end-conversation'])
  ```

- [ ] **Step 2: Add button template after settings button**

  After the settings button (after line 43 `</button>`), add:

  ```html
  <button class="btn diary-end-btn" :disabled="state !== 'idle'" @click="$emit('end-conversation')" title="结束对话">
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2z" />
    </svg>
  </button>
  ```

- [ ] **Step 3: Add style for the button**

  After `.settings-btn` style block (after line 190), add:

  ```css
  .diary-end-btn {
    background: rgba(180, 120, 255, 0.1);
    border-color: rgba(180, 120, 255, 0.2);
    color: #b078ff;
  }
  ```

---

### Task 4: Add diary state/events/header button to App.vue

**File:** `frontend/src/App.vue`

- [ ] **Step 1: Import DiaryPanel**

  Add after the SettingsPanel import (line 89):
  ```js
  import DiaryPanel from './components/DiaryPanel.vue'
  ```

- [ ] **Step 2: Add diary state refs**

  After `const voices = ref([])` (line 110), add:
  ```js
  const diaryVisible = ref(false)
  ```

- [ ] **Step 3: Add diary WS event handler**

  After the `ws.on('error', ...)` block (after line 208), add:
  ```js
  ws.on('diary_saved', (msg) => {
    if (msg.filename) {
      console.log(`[日记] 已保存: ${msg.filename}`)
      // 简单的 toast 提示 — 通过添加一条系统消息
      messages.value.push({
        role: 'system',
        text: `📖 日记已保存 (${msg.filename})`,
        msgId: ++_msgIdCounter,
      })
    }
  })
  ```

- [ ] **Step 4: Add diary icon button in header**

  In the header template, after the settings button (after line 20 `</button>`), add:
  ```html
  <button class="icon-btn" @click="diaryVisible = true" title="日记">
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2z" />
    </svg>
  </button>
  ```

- [ ] **Step 5: Add end-conversation handler**

  After `handleModelConfigUpdate` (after line 317), add:
  ```js
  function handleEndConversation() {
    ws.send({ type: 'end_conversation' })
    // 在对话气泡中显示提示
    messages.value.push({
      role: 'system',
      text: '📝 正在结束对话并写日记...',
      msgId: ++_msgIdCounter,
    })
  }
  ```

- [ ] **Step 6: Wire up ControlBar event**

  Change ControlBar in template to listen for `end-conversation`:
  Find the ControlBar component section (around line 48-59) and add `@end-conversation="handleEndConversation"`:

  ```html
  <ControlBar
    ...
    @open-settings="openSettings"
    @end-conversation="handleEndConversation"
  />
  ```

- [ ] **Step 7: Add DiaryPanel component**

  Add before the closing `</div>` of the root div (after the SettingsPanel at line 79), add:

  ```html
  <DiaryPanel
    :visible="diaryVisible"
    @close="diaryVisible = false"
  />
  ```

---

### Task 5: Create DiaryPanel.vue

**File:** `frontend/src/components/DiaryPanel.vue` (new)

- [ ] **Step 1: Create the component**

  ```vue
  <template>
    <Teleport to="body">
      <transition name="slide">
        <div v-if="visible" class="diary-overlay" @click.self="$emit('close')">
          <div class="diary-panel">
            <div class="panel-header">
              <h3>Mio 的日记</h3>
              <button class="close-btn" @click="$emit('close')">&times;</button>
            </div>
            <div class="panel-body">
              <!-- 列表视图 -->
              <div v-if="!selectedEntry" class="diary-list">
                <div
                  v-for="entry in entries"
                  :key="entry.name"
                  class="diary-item"
                  @click="selectEntry(entry)"
                >
                  <div class="diary-time">{{ entry.time }}</div>
                </div>
                <div v-if="entries.length === 0 && !loading" class="empty">
                  暂无日记
                </div>
                <div v-if="loading" class="empty">加载中...</div>
              </div>
              <!-- 详情视图 -->
              <div v-else class="diary-detail">
                <button class="back-btn" @click="selectedEntry = null">← 返回列表</button>
                <div class="diary-content" v-html="renderedContent" />
              </div>
            </div>
          </div>
        </div>
      </transition>
    </Teleport>
  </template>

  <script setup>
  import { ref, watch, computed } from 'vue'

  const props = defineProps({
    visible: { type: Boolean, default: false },
  })

  const emit = defineEmits(['close'])

  const entries = ref([])
  const selectedEntry = ref(null)
  const selectedContent = ref('')
  const loading = ref(false)

  // 简单的 Markdown 渲染（只处理标题和换行）
  const renderedContent = computed(() => {
    if (!selectedContent.value) return ''
    return selectedContent.value
      .replace(/^### (.*$)/gm, '<h4>$1</h4>')
      .replace(/^## (.*$)/gm, '<h3>$1</h3>')
      .replace(/^# (.*$)/gm, '<h2>$1</h2>')
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br>')
  })

  async function loadList() {
    loading.value = true
    try {
      const resp = await fetch('/api/diaries')
      if (resp.ok) {
        entries.value = await resp.json()
      }
    } catch (e) {
      console.error('加载日记列表失败', e)
    } finally {
      loading.value = false
    }
  }

  async function selectEntry(entry) {
    selectedEntry.value = entry
    selectedContent.value = ''
    try {
      const resp = await fetch(`/api/diaries/${entry.name}`)
      if (resp.ok) {
        selectedContent.value = await resp.text()
      }
    } catch (e) {
      console.error('加载日记内容失败', e)
      selectedContent.value = '加载失败'
    }
  }

  watch(() => props.visible, (val) => {
    if (val) {
      loadList()
    } else {
      selectedEntry.value = null
      selectedContent.value = ''
    }
  })
  </script>

  <style scoped>
  .diary-overlay {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    justify-content: flex-end;
  }

  .diary-panel {
    width: 400px;
    max-width: 90vw;
    height: 100%;
    background: rgba(20, 15, 40, 0.94);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-left: 1px solid rgba(255, 255, 255, 0.1);
    display: flex;
    flex-direction: column;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }

  .panel-header h3 {
    color: rgba(255, 255, 255, 0.9);
    font-size: 16px;
    font-weight: 500;
  }

  .close-btn {
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.06);
    color: rgba(255, 255, 255, 0.6);
    font-size: 20px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
  }

  .close-btn:hover {
    background: rgba(255, 255, 255, 0.12);
    color: white;
  }

  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px 0;
  }

  .diary-list {
    display: flex;
    flex-direction: column;
  }

  .diary-item {
    padding: 12px 20px;
    cursor: pointer;
    transition: background 0.15s;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  }

  .diary-item:hover {
    background: rgba(255, 255, 255, 0.06);
  }

  .diary-time {
    color: rgba(255, 180, 220, 0.8);
    font-size: 14px;
    font-weight: 500;
  }

  .empty {
    padding: 40px 20px;
    text-align: center;
    color: rgba(255, 255, 255, 0.3);
    font-size: 14px;
  }

  .diary-detail {
    padding: 0 20px 20px;
  }

  .back-btn {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    color: rgba(255, 255, 255, 0.6);
    padding: 6px 14px;
    font-size: 13px;
    cursor: pointer;
    margin-bottom: 16px;
    transition: all 0.2s;
  }

  .back-btn:hover {
    background: rgba(255, 255, 255, 0.08);
    color: white;
  }

  .diary-content {
    color: rgba(255, 255, 255, 0.85);
    font-size: 14px;
    line-height: 1.8;
    white-space: pre-wrap;
  }

  .diary-content :deep(h2) {
    color: rgba(255, 180, 220, 0.9);
    font-size: 18px;
    font-weight: 500;
    margin: 0 0 12px;
  }

  .diary-content :deep(p) {
    margin: 0 0 12px;
  }

  /* 复用 SettingsPanel 的 slide 过渡 */
  .slide-enter-active,
  .slide-leave-active {
    transition: transform 0.3s ease;
  }

  .slide-enter-from,
  .slide-leave-to {
    transform: translateX(100%);
  }

  .slide-enter-to,
  .slide-leave-from {
    transform: translateX(0);
  }
  </style>
  ```
