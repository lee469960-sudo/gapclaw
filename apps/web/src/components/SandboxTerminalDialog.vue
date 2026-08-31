<template>
  <el-dialog
    v-model="visible"
    title="沙箱终端"
    width="90%"
    top="5vh"
    destroy-on-close
    @opened="onOpened"
  >
    <XtermPanel
      v-if="visible && sandboxId"
      :key="sandboxId"
      ref="panelRef"
      variant="sandbox"
      ws-url="/pages/page_sandbox.ws"
      :init-payload="{ id: sandboxId, workdir }"
    />
  </el-dialog>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import XtermPanel from './XtermPanel.vue'

const props = defineProps({
  modelValue: Boolean,
  sandboxId: String,
  workdir: { type: String, default: '/workplace' },
})

const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const panelRef = ref(null)

function onOpened() {
  nextTick(() => panelRef.value?.initAndConnect?.())
}
</script>
