<template>
  <div class="character-panel">
    <div class="character-avatar" :class="stateClass">
      <div class="avatar-glow" />
      <img :src="avatarUrl" :alt="charName" class="avatar-img" />
    </div>

    <div class="emotions-section">
      <div
        v-for="e in emotionList"
        :key="e.key"
        class="emotion-bar"
      >
        <div class="bar-label">{{ e.label }}</div>
        <div class="bar-track">
          <div
            class="bar-fill"
            :class="[e.key, { pulse: e.pulse }]"
            :style="{ width: e.percent + '%' }"
          />
        </div>
        <div class="bar-value">{{ e.value }}</div>
      </div>
    </div>

    <div class="tier-display">
      <span class="tier-label">{{ tierLabel }}</span>
      <span class="intimacy-text">亲密度 {{ intimacy.toFixed(1) }}</span>
    </div>

    <div class="status-indicator">
      <span class="status-dot" :class="stateClass" />
      <span class="status-text">{{ stateText }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'

const props = defineProps({
  state: { type: String, default: 'idle' },
  emotions: { type: Object, default: () => ({ joy: 0, sadness: 0, anger: 0, fear: 0, love: 0, surprise: 0, trust: 0 }) },
  personaTier: { type: Number, default: 1 },
  intimacy: { type: Number, default: 0 },
  charName: { type: String, default: 'Mio' },
  avatarUrl: { type: String, default: '/img/gpt_img_20260430_210021.png' },
})

const TIER_LABELS = { 1: '陌生', 2: '友好', 3: '亲密' }
const tierLabel = computed(() => TIER_LABELS[props.personaTier] || `Lv.${props.personaTier}`)

const EMOTION_META = [
  { key: 'joy', label: '快乐', color: '#ffd93d' },
  { key: 'sadness', label: '悲伤', color: '#6c5ce7' },
  { key: 'anger', label: '愤怒', color: '#ff6b6b' },
  { key: 'fear', label: '恐惧', color: '#a29bfe' },
  { key: 'love', label: '爱意', color: '#ff7eb3' },
  { key: 'surprise', label: '惊喜', color: '#fdcb6e' },
  { key: 'trust', label: '信任', color: '#74b9ff' },
]

const stateClass = computed(() => props.state)

const stateText = computed(() => {
  const map = { idle: '待机中', listening: '聆听中...', thinking: '思考中...', speaking: '说话中...' }
  return map[props.state] || '待机中'
})

const pulses = reactive({})
EMOTION_META.forEach(e => { pulses[e.key] = false })

const emotionList = computed(() =>
  EMOTION_META.map(e => {
    const raw = props.emotions[e.key] ?? 0
    // 映射 [-50, 50] 范围到 [0, 100]%，当前显示绝对值，偏移 +50
    const percent = Math.min(100, Math.max(0, (raw + 50) * 1))
    const value = raw > 0 ? `+${raw}` : `${raw}`
    return { ...e, percent, value, pulse: pulses[e.key] }
  })
)

EMOTION_META.forEach(e => {
  watch(() => props.emotions[e.key], (val, old) => {
    if (val !== old && old !== undefined) {
      pulses[e.key] = true
      setTimeout(() => { pulses[e.key] = false }, 800)
    }
  })
})
</script>

<style scoped>
.character-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 16px;
}

.character-avatar {
  position: relative;
  width: 180px;
  height: 260px;
  border-radius: 16px;
  overflow: hidden;
  transition: transform 0.3s ease;
}

.character-avatar.speaking {
  transform: scale(1.03);
}

.avatar-glow {
  position: absolute;
  inset: -20px;
  border-radius: 50%;
  z-index: 0;
  transition: all 0.5s ease;
}

.character-avatar.idle .avatar-glow {
  box-shadow: 0 0 30px rgba(100, 200, 100, 0.3);
}
.character-avatar.listening .avatar-glow {
  box-shadow: 0 0 40px rgba(80, 150, 255, 0.5);
  animation: pulse 1.5s ease-in-out infinite;
}
.character-avatar.thinking .avatar-glow {
  box-shadow: 0 0 40px rgba(255, 180, 60, 0.5);
  animation: pulse 1.2s ease-in-out infinite;
}
.character-avatar.speaking .avatar-glow {
  box-shadow: 0 0 50px rgba(255, 120, 180, 0.6);
  animation: pulse 1s ease-in-out infinite;
}

.avatar-img {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.tier-display {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 0;
}

.tier-label {
  font-size: 13px;
  font-weight: 600;
  color: #ffd93d;
  text-shadow: 0 0 8px rgba(255, 217, 61, 0.3);
}

.intimacy-text {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
  font-variant-numeric: tabular-nums;
}

.emotions-section {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.emotion-bar {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
}

.bar-label {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.7);
  white-space: nowrap;
  width: 36px;
  text-align: right;
}

.bar-track {
  flex: 1;
  height: 5px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 3px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.5s ease;
}

.bar-fill.joy { background: #ffd93d; }
.bar-fill.sadness { background: #6c5ce7; }
.bar-fill.anger { background: #ff6b6b; }
.bar-fill.fear { background: #a29bfe; }
.bar-fill.love { background: #ff7eb3; }
.bar-fill.surprise { background: #fdcb6e; }
.bar-fill.trust { background: #74b9ff; }

.bar-fill.pulse {
  animation: barPulse 0.8s ease;
}

@keyframes barPulse {
  0%, 100% { filter: brightness(1); }
  50% { filter: brightness(2.5); box-shadow: 0 0 8px currentColor; }
}

.bar-value {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.7);
  width: 32px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  transition: all 0.3s;
}

.status-dot.idle {
  background: #5eff7e;
  box-shadow: 0 0 8px rgba(94, 255, 126, 0.6);
}

.status-dot.listening {
  background: #5e9eff;
  box-shadow: 0 0 8px rgba(94, 158, 255, 0.6);
  animation: pulse 1.5s ease-in-out infinite;
}

.status-dot.thinking {
  background: #ffb85e;
  box-shadow: 0 0 8px rgba(255, 184, 94, 0.6);
  animation: pulse 1.2s ease-in-out infinite;
}

.status-dot.speaking {
  background: #ff5ea0;
  box-shadow: 0 0 8px rgba(255, 94, 160, 0.6);
  animation: pulse 1s ease-in-out infinite;
}

.status-text {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.7);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
