<template>
  <div class="collapse-root">
    <div
      class="collapse-wrap"
      :class="{ collapsed: !expanded && overflow }"
      :style="wrapStyle"
    >
      <pre ref="preRef" class="content">{{ text }}</pre>
    </div>
    <button
      v-if="overflow"
      type="button"
      class="collapse-toggle"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起' : '展开' }}
    </button>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  content: { type: [String, Number], default: '' },
  /** Collapsed viewport height in px */
  maxHeight: { type: Number, default: 160 },
})

const text = computed(() => String(props.content ?? ''))
const preRef = ref(null)
const expanded = ref(false)
const overflow = ref(false)
let ro = null

const wrapStyle = computed(() => (
  !expanded.value && overflow.value
    ? { maxHeight: `${props.maxHeight}px` }
    : null
))

function measure() {
  const el = preRef.value
  if (!el) {
    overflow.value = false
    return
  }
  overflow.value = el.scrollHeight > props.maxHeight + 4
  if (!overflow.value) expanded.value = false
}

onMounted(async () => {
  await nextTick()
  measure()
  if (typeof ResizeObserver !== 'undefined' && preRef.value) {
    ro = new ResizeObserver(() => measure())
    ro.observe(preRef.value)
  }
})

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
})

watch(text, async () => {
  expanded.value = false
  await nextTick()
  measure()
})
</script>

<style scoped>
.collapse-root {
  width: 100%;
}
.collapse-wrap {
  overflow: hidden;
  position: relative;
}
.collapse-wrap.collapsed::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 36px;
  pointer-events: none;
  background: linear-gradient(to bottom, rgba(64, 158, 255, 0), #409eff);
}
.content {
  white-space: pre-wrap;
  padding: 10px 12px;
  margin: 0;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.55;
  word-break: break-word;
  color: #fff;
}
.collapse-toggle {
  display: block;
  width: 100%;
  margin: 0;
  padding: 4px 12px 8px;
  border: 0;
  background: transparent;
  color: rgba(255, 255, 255, 0.92);
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  text-align: center;
}
.collapse-toggle:hover {
  color: #fff;
  text-decoration: underline;
}
</style>
