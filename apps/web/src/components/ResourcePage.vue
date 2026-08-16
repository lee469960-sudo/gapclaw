<template>
  <div class="page">
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="搜索..." clearable style="width:240px" />
      <el-button type="primary" @click="openForm()">新增</el-button>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-table :data="filtered" v-loading="loading" stripe>
      <el-table-column v-for="col in columns" :key="col.prop" :prop="col.prop" :label="col.label" :min-width="col.width || 120" show-overflow-tooltip />
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openForm(row)">编辑</el-button>
          <el-button v-if="extraAction" link type="success" @click="extraAction(row)">{{ extraLabel }}</el-button>
          <el-button link type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="visible" :title="form.id ? '编辑' : '新增'" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item v-for="f in fields" :key="f.key" :label="f.label">
          <el-input v-if="f.type !== 'textarea' && f.type !== 'select'" v-model="form[f.key]" :type="f.type || 'text'" />
          <el-input v-else-if="f.type === 'textarea'" v-model="form[f.key]" type="textarea" :rows="4" />
          <el-select v-else v-model="form[f.key]" style="width:100%">
            <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessageBox } from 'element-plus'
import { getCgi, postCgi } from '../api'

const props = defineProps({
  cgiPath: { type: String, required: true },
  columns: { type: Array, required: true },
  fields: { type: Array, required: true },
  idKey: { type: String, default: 'id' },
  searchKeys: { type: Array, default: () => ['name'] },
  extraAction: { type: Function, default: null },
  extraLabel: { type: String, default: '测试' },
  mapRow: { type: Function, default: (r) => r },
  mapSave: { type: Function, default: (f) => ({ action: f.id ? 'update' : 'create', ...f }) },
})

const list = ref([])
const loading = ref(false)
const keyword = ref('')
const visible = ref(false)
const form = reactive({})

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((row) => props.searchKeys.some((k) => String(row[k] || '').toLowerCase().includes(kw)))
})

async function load() {
  loading.value = true
  try {
    const res = await getCgi(props.cgiPath, { action: 'list' })
    list.value = (res.data || []).map(props.mapRow)
  } finally {
    loading.value = false
  }
}

function openForm(row) {
  Object.keys(form).forEach((k) => delete form[k])
  if (row) Object.assign(form, { ...row })
  else props.fields.forEach((f) => { form[f.key] = f.default ?? '' })
  visible.value = true
}

async function save() {
  await postCgi(props.cgiPath, props.mapSave({ ...form }))
  visible.value = false
  load()
}

async function onDelete(row) {
  await ElMessageBox.confirm('确定删除？', '提示')
  await postCgi(props.cgiPath, { action: 'delete', id: row[props.idKey] })
  load()
}

onMounted(load)
defineExpose({ load, openForm })
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px; }
.toolbar { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; }
</style>
