# MioChat — 实时语音 AI 聊天

MioChat 是一个实时语音对话 AI 应用，支持语音识别（ASR）、大语言模型（LLM）对话和语音合成（TTS），并带有角色养成系统（好感度 / 信任度）。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                   前端 (Vue 3 + Vite)                │
│  CharacterPanel  ChatBubbles  ControlBar  Settings   │
└──────────────┬──────────────────────────────────────┘
               │ WebSocket (aiohttp)
┌──────────────▼──────────────────────────────────────┐
│              后端 (Python aiohttp)                    │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ ASRClient │  │ LLM      │  │ TTS (CosyVoice)    │  │
│  │ Fun-ASR   │  │ DeepSeek │  │ 流式音频合成       │  │
│  └──────────┘  │ 智谱AI等  │  └───────────────────┘  │
│                └──────────┘                          │
└──────────────────────────────────────────────────────┘
```

## 功能

- **语音对话** — 实时语音识别 + LLM 响应 + 语音合成，端到端延迟低
- **文字对话** — 支持键盘文本输入
- **角色养成** — 好感度 / 信任度系统，随对话动态变化
- **多角色音色** — 支持 CosyVoice V2/V3 多套音色切换
- **打断机制** — 用户可随时打断 AI 说话
- **多模型支持** — ASR、LLM、TTS 可分别配置不同的 API 提供商
- **前端粒子背景** — 动态粒子特效，状态联动光效

## 快速开始

### 前置要求

- Python 3.10+
- Node.js 18+
- 各 API 密钥（见下方配置）

### 后端

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 API Key

# 启动服务
python src/server.py
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

打开浏览器访问 `http://localhost:5173` 即可。

## 配置说明

编辑 `.env` 文件配置 API 密钥和服务：

| 变量 | 说明 | 必填 |
|---|---|---|
| `ZHIPU_API_KEY` | 智谱 AI API Key（用于 ASR） | 是 |
| `ASR_MODEL` | ASR 模型名 | 否 |
| `LLM_API_KEY` | LLM API Key | 是 |
| `LLM_BASE_URL` | LLM API 地址 | 是 |
| `LLM_MODEL` | LLM 模型名 | 否 |
| `QWEN_TTS_API_KEY` | CosyVoice TTS API Key | 是 |
| `QWEN_TTS_MODEL` | TTS 模型名 | 否 |
| `VOICE_ID1` | 默认音色 | 否 |

## 项目结构

```
MioChat/
├── src/               # 后端 Python 源码
│   ├── server.py      # WebSocket + HTTP 主服务
│   ├── asr_client.py  # 语音识别客户端 (DashScope Fun-ASR)
│   ├── llm_client.py  # LLM 对话客户端
│   ├── tts_client.py  # 语音合成客户端 (CosyVoice)
│   └── logger.py      # 日志工具
├── requirements.txt   # Python 依赖
├── .env.example       # 环境变量模板
└── frontend/          # Vue 3 前端
    ├── index.html
    ├── vite.config.js
    ├── package.json
    └── src/
        ├── App.vue                    # 主页面
        ├── main.js
        ├── components/
        │   ├── CharacterPanel.vue     # 角色立绘面板
        │   ├── ChatBubbles.vue        # 对话气泡
        │   ├── ControlBar.vue         # 底部控制栏
        │   ├── SettingsPanel.vue      # 设置面板
        │   ├── ParticleBg.vue         # 粒子背景
        │   └── VoiceWaveform.vue      # 语音波形动画
        └── composables/
            ├── useWebSocket.js        # WebSocket 连接
            ├── useMicrophone.js       # 麦克风管理
            ├── useAudioPlayer.js      # 音频播放
            └── useSettings.js         # 设置管理
```

## 技术栈

- **后端**: Python, aiohttp, WebSocket
- **前端**: Vue 3, Vite
- **ASR**: DashScope Fun-ASR (阿里云)
- **LLM**: DeepSeek / 智谱 GLM 等
- **TTS**: CosyVoice (阿里云)

## 许可

MIT
