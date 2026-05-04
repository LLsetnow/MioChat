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
