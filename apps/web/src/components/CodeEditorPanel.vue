<template>
  <div class="code-editor-panel">
    <div class="code-editor-toolbar">
      <span class="lang-badge">{{ languageLabel(language) }}</span>
      <span v-if="readonly" class="mode-hint">只读</span>
      <span v-else class="mode-hint">编辑</span>
    </div>
    <div ref="hostRef" class="code-editor-host" />
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, computed } from 'vue'
import { EditorView, lineNumbers, highlightActiveLineGutter, highlightSpecialChars, drawSelection, dropCursor, rectangularSelection, crosshairCursor, highlightActiveLine, keymap } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
import { defaultHighlightStyle, syntaxHighlighting, indentOnInput, bracketMatching, foldGutter, foldKeymap } from '@codemirror/language'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { vscodeDark, vscodeLight } from '@uiw/codemirror-theme-vscode'
import { theme } from '../theme'
import { getLanguageExtension, languageLabel as langLabel } from '../utils/codeLanguage'

const props = defineProps({
  modelValue: { type: String, default: '' },
  language: { type: String, default: 'plaintext' },
  readonly: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue'])

const hostRef = ref(null)
let view = null

const languageLabel = (id) => langLabel(id)

const cmTheme = computed(() => (theme.value === 'dark' ? vscodeDark : vscodeLight))

function buildExtensions() {
  const langExt = getLanguageExtension(props.language)
  return [
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightSpecialChars(),
    history(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    indentOnInput(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    bracketMatching(),
    foldGutter(),
    highlightActiveLine(),
    keymap.of([...defaultKeymap, ...historyKeymap, ...foldKeymap]),
    langExt,
    cmTheme.value,
    EditorView.editable.of(!props.readonly),
    EditorView.lineWrapping,
    EditorView.updateListener.of((update) => {
      if (update.docChanged && !props.readonly) {
        emit('update:modelValue', update.state.doc.toString())
      }
    }),
    EditorView.theme({
      '&': {
        height: '100%',
        minHeight: '360px',
        fontSize: '13px',
        backgroundColor: 'var(--code-editor-bg)',
      },
      '.cm-scroller': {
        fontFamily: "'Menlo', 'Monaco', 'Consolas', 'Courier New', monospace",
        lineHeight: '1.55',
      },
      '.cm-gutters': {
        borderRight: '1px solid var(--code-gutter-border)',
        backgroundColor: 'var(--code-gutter-bg)',
        color: 'var(--code-gutter-text)',
      },
      '.cm-activeLineGutter': {
        backgroundColor: 'var(--code-active-line-gutter)',
      },
      '.cm-content': {
        padding: '8px 0',
      },
    }),
  ].filter(Boolean)
}

function mountEditor() {
  if (!hostRef.value) return
  view?.destroy()
  view = new EditorView({
    state: EditorState.create({
      doc: props.modelValue || '',
      extensions: buildExtensions(),
    }),
    parent: hostRef.value,
  })
}

function syncContent(value) {
  if (!view) return
  const current = view.state.doc.toString()
  if (value === current) return
  view.dispatch({
    changes: { from: 0, to: current.length, insert: value || '' },
  })
}

watch(
  () => props.modelValue,
  (value) => syncContent(value),
)

watch(
  () => [props.readonly, props.language, theme.value],
  () => mountEditor(),
)

onMounted(mountEditor)

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})

defineExpose({
  getValue: () => view?.state.doc.toString() || '',
})
</script>

<style scoped>
.code-editor-panel {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 220px);
  min-height: 360px;
  border: 1px solid var(--code-panel-border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--code-editor-bg);
}
.code-editor-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--code-panel-border);
  background: var(--code-toolbar-bg);
  font-size: 12px;
}
.lang-badge {
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--code-badge-bg);
  color: var(--code-badge-text);
  font-weight: 600;
}
.mode-hint {
  color: var(--code-muted-text);
}
.code-editor-host {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.code-editor-host :deep(.cm-editor) {
  height: 100%;
}
.code-editor-host :deep(.cm-focused) {
  outline: none;
}
</style>
