# 日记功能设计文档

## 概述

为 MioChat 增加两个功能：
1. **结束对话按钮** — 结束本次对话，LLM 以第一人称（人设）视角写日记，清空上下文
2. **日记页面** — 在浏览器中浏览历史日记

## 后端设计

### 1. 新增 WS 消息类型: `end_conversation`

**客户端 → 服务端:**
```json
{"type": "end_conversation"}
```

**服务端处理流程 (server.py):**
1. 获取当前对话上下文 `llm_client.get_context("default")`
2. 若上下文为空（无消息），直接清理并返回
3. 调用 `llm_client.generate_diary(context)` 生成日记
4. 将日记保存到 `src/diary/YYYY-MM-DD-HH.md`
5. 调用 `llm_client.clear_context("default")` 清空上下文
6. 重置 `affection`/`trust` 为初始值 0
7. 回复客户端: `{"type": "diary_saved", "filename": "2026-05-05-14.md"}`

**约束:**
- 仅在 state 为 IDLE 时处理（对话已结束）
- LLM 调用使用非流式请求（一次性返回全文）
- 为防止打断，设 30 秒超时

### 2. 新增 LLM 函数: `generate_diary()`

文件: `llm_client.py`

```python
def generate_diary(context: list[dict]) -> str:
    """
    基于对话上下文，调用 LLM 生成第一人称日记。
    返回日记文本（不带情感标签）。
    """
```

**System prompt:**
```
请根据以上对话内容，以 Mio 的第一人称视角写一篇日记。
要求：
1. 语气符合 Mio 的人设（温柔撒娇的妹妹）
2. 记录对话中的重要内容和你的感受
3. 不要包含 <好感> <信任> 等标签
4. 用中文
```

**调用方式:** 非流式 `httpx.POST`，`stream=False`，`max_tokens=4096`

**结构化消息:**
```
[system_prompt] + history + [{"role": "user", "content": "请写一篇日记吧。"}]
```

### 3. 新增 HTTP API

| 端点 | 方法 | 说明 |
|---|---|---|
| `GET /api/diaries` | HTTP GET | 返回日记文件列表 `[{name, time}]`，按时间倒序。`name` 为文件名，`time` 从文件名解析显示 |
| `GET /api/diaries/{name}` | HTTP GET | 返回日记文件内容 (text/plain) |

**`GET /api/diaries` 响应格式:**
```json
[
  {"name": "2026-05-05-14.md", "time": "2026-05-05 14:00"},
  {"name": "2026-05-04-22.md", "time": "2026-05-04 22:00"}
]
```

### 4. 存储结构

日记文件保存在 `src/diary/` 目录下：
```
src/diary/
├── 2026-05-05-14.md
├── 2026-05-05-16.md
└── 2026-05-04-22.md
```

文件格式: Markdown 纯文本，第一行为标题 `# 2026-05-05 14:00`

## 前端设计

### 1. ControlBar.vue

在设置按钮旁边新增「结束对话」按钮：
- 图标: 书/日记图标 (📖 或 SVG)
- `disabled` 条件: `state !== 'idle'`
- 点击: emit `end-conversation`
- 需要新增 prop: `state`（已有）

### 2. App.vue

**新增状态:**
- `diaryVisible: ref(false)` — 日记面板可见性
- `diarySaved: ref(false)` — 日记已保存标记（用于 toast 提示）

**新增 WS 事件处理:**
- `ws.on('diary_saved', ...)` — 显示 toast 提示

**Header 新增日记图标按钮:**
- 设置齿轮旁边
- 点击: `diaryVisible = true`

**控制流:**
- `handleEndConversation()`: 发送 `{type: "end_conversation"}`，禁用按钮

### 3. DiaryPanel.vue（新组件）

**复用模式:** SettingsPanel 的 Teleport + slide-in 动画

```
<Teleport to="body">
  <transition name="slide">
    <div class="diary-overlay" @click.self="close">
      <div class="diary-panel">
        <div class="panel-header">
          <h3>Mio 的日记</h3>
          <button class="close-btn" @click="close">✕</button>
        </div>
        <div class="panel-body">
          <!-- 列表视图 -->
          <div v-if="!selectedDiary" class="diary-list">
            <div v-for="entry in entries" class="diary-item" @click="selectDiary(entry)">
              <div class="diary-time">{{ entry.time }}</div>
            </div>
            <div v-if="entries.length === 0" class="empty">暂无日记</div>
          </div>
          <!-- 详情视图 -->
          <div v-else class="diary-detail">
            <button class="back-btn" @click="selectedDiary = null">← 返回</button>
            <div class="diary-content">{{ selectedDiary.content }}</div>
          </div>
        </div>
      </div>
    </div>
  </transition>
</Teleport>
```

**Props:**
- `visible: Boolean`

**Emits:**
- `close`

**内部逻辑:**
- `onMounted` / `watch(visible)` → fetch `GET /api/diaries` 加载列表
- 点击条目 → fetch `GET /api/diaries/{name}` 加载内容
- 列表条目显示时间 + 第一行摘要

## 需要修改的文件清单

| 文件 | 改动类型 |
|---|---|
| `src/server.py` | 修改 — 新增 WS handler + HTTP API |
| `src/llm_client.py` | 修改 — 新增 `generate_diary()` |
| `frontend/src/components/ControlBar.vue` | 修改 — 新增结束对话按钮 |
| `frontend/src/App.vue` | 修改 — 新增 WS handler + 日记入口 |
| `frontend/src/components/DiaryPanel.vue` | 新建 — 日记浏览面板 |
