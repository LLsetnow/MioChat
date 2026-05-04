/**
 * 设置状态管理（纯内存，不持久化到浏览器）
 * 双分页：语音设置 + 模型与密钥
 */
import { ref, reactive } from 'vue'

// 语音设置（始终使用默认值）
const voice = ref('longhuhu_v3')
const instruction = ref('')
const micEnabled = ref(true)
const selectedDeviceId = ref('')
const ttsEnabled = ref(true)

// 模型与密钥（始终使用默认值）
const modelConfig = reactive({
  asr_model: 'fun-asr-realtime',
  asr_api_key: '',
  llm_model: 'deepseek-v4-flash',
  llm_api_key: '',
  llm_base_url: 'https://api.deepseek.com',
  tts_model: 'cosyvoice-v3-flash',
  tts_api_key: '',
})

// 始终视为新用户（无持久化设置）
const hasSavedSettings = ref(false)

export function useSettings() {
  function getUpdateConfigMsg() {
    const msg = { type: 'update_config' }
    if (voice.value) msg.voice = voice.value
    if (instruction.value) msg.instruction = instruction.value
    msg.asr_model = modelConfig.asr_model
    if (modelConfig.asr_api_key) msg.asr_api_key = modelConfig.asr_api_key
    msg.llm_model = modelConfig.llm_model
    if (modelConfig.llm_api_key) msg.llm_api_key = modelConfig.llm_api_key
    msg.llm_base_url = modelConfig.llm_base_url
    msg.tts_model = modelConfig.tts_model
    if (modelConfig.tts_api_key) msg.tts_api_key = modelConfig.tts_api_key
    msg.tts_enabled = ttsEnabled.value
    return msg
  }

  return {
    hasSavedSettings,
    voice,
    instruction,
    micEnabled,
    selectedDeviceId,
    ttsEnabled,
    modelConfig,
    getUpdateConfigMsg,
  }
}
