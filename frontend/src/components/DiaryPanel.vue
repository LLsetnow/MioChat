<template>
  <Teleport to="body">
    <!-- 侧边栏：日记列表 -->
    <transition name="slide">
      <div v-if="visible" class="diary-overlay" @click.self="$emit('close')">
        <div class="diary-panel">
          <div class="panel-header">
            <h3>Mio 的日记</h3>
            <button class="close-btn" @click="$emit('close')">&times;</button>
          </div>
          <div class="panel-body">
            <div class="diary-list">
              <div
                v-for="entry in entries"
                :key="entry.name"
                class="diary-item"
                @click="selectEntry(entry)"
              >
                <div class="diary-time">{{ entry.time }}</div>
              </div>
              <div v-if="entries.length === 0 && !loading" class="empty">暂无日记</div>
              <div v-if="loading" class="empty">加载中...</div>
            </div>
          </div>
        </div>
      </div>
    </transition>

    <!-- 居中弹窗：日记阅读（纸张样式） -->
    <transition name="fade-scale">
      <div v-if="selectedEntry" class="reader-overlay" @click.self="closeReader">
        <div
          class="reader-modal"
          ref="readerModalRef"
          :style="tiltStyle"
          @mousemove="handleTilt"
          @mouseleave="resetTilt"
        >
          <div class="reader-scroll">
            <div class="reader-content" v-html="renderedContent" />
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

// ── 3D 鼠标倾斜 ──
const readerModalRef = ref(null)
const tiltX = ref(0)
const tiltY = ref(0)

function handleTilt(event) {
  const el = readerModalRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const centerX = rect.left + rect.width / 2
  const centerY = rect.top + rect.height / 2
  const deltaX = (event.clientX - centerX) / (rect.width / 2)
  const deltaY = (event.clientY - centerY) / (rect.height / 2)
  tiltX.value = deltaX * 6   // max ±6°
  tiltY.value = -deltaY * 6
}

function resetTilt() {
  tiltX.value = 0
  tiltY.value = 0
}

const tiltStyle = computed(() => ({
  transform: `perspective(1000px) rotateX(${tiltY.value}deg) rotateY(${tiltX.value}deg)`,
}))

const renderedContent = computed(() => {
  if (!selectedContent.value) return ''
  // 按空行分段，第一段为标题行（如 "5月5日 晴"）
  const blocks = selectedContent.value.trim().split(/\n{2,}/)
  return blocks.map((block, i) => {
    let inner = block.replace(/\n/g, '<br>')
    if (i === 0) {
      // 兼容旧格式：去掉 markdown 标题前缀
      inner = inner.replace(/^#\s+/, '')
      return `<p class="diary-title">${inner}</p>`
    }
    return `<p>${inner}</p>`
  }).join('')
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

function closeReader() {
  selectedEntry.value = null
  selectedContent.value = ''
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
/* ── 侧边栏 ── */
.diary-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: flex-end;
}

.diary-panel {
  width: 360px;
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

/* ── 居中阅读弹窗（纸张样式） ── */
.reader-overlay {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.reader-modal {
  position: relative;
  width: 640px;
  max-width: 90vw;
  max-height: 90vh;
  background: #f5f0e1;
  border-radius: 4px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.5), 0 2px 8px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: transform 0.08s ease-out;
}

.reader-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 28px 48px 28px;
  /* 笔记本横线：间距 48px 与 line-height 严格对齐 */
  background-image: repeating-linear-gradient(
    transparent 0px,
    transparent 47px,
    #d8d0c0 47px,
    #d8d0c0 48px
  );
  background-position: 0 15px;
}

.reader-content {
  font-family: 'Ma Shan Zheng', cursive;
  font-size: 22px;
  line-height: 48px;
  color: #3a3a3a;
  min-height: 200px;
}

/* 标题行：居中，无缩进 */
.reader-content :deep(p.diary-title) {
  margin: 0;
  text-align: center;
  font-size: 24px;
  color: #5a4a3a;
}

/* 正文段落：首行缩进两格 */
.reader-content :deep(p) {
  margin: 0;
  text-indent: 2em;
}

/* 覆盖标题行的 text-indent */
.reader-content :deep(p.diary-title) {
  text-indent: 0;
}

/* ── 过渡动画 ── */
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

.fade-scale-enter-active,
.fade-scale-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.fade-scale-enter-from,
.fade-scale-leave-to {
  opacity: 0;
  transform: scale(0.92);
}

.fade-scale-enter-to,
.fade-scale-leave-from {
  opacity: 1;
  transform: scale(1);
}
</style>
