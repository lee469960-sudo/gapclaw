<template>
  <div class="page" v-loading="loading">
    <div class="header"><div><h3>模型路由</h3><p>角色模型组与路由策略独立于旧 LLM 组。</p></div></div>
    <el-row :gutter="16">
      <el-col :md="12"><el-card><template #header><span>角色模型组</span><el-button type="primary" size="small" @click="openGroup()">新建</el-button></template>
        <el-table :data="groups"><el-table-column prop="name" label="名称"/><el-table-column prop="role" label="角色"/><el-table-column prop="preferred_llm_id" label="首选模型"/><el-table-column label="操作" width="100"><template #default="{row}"><el-button link @click="openGroup(row)">编辑</el-button></template></el-table-column></el-table>
      </el-card></el-col>
      <el-col :md="12"><el-card><template #header><span>路由策略</span><el-button type="primary" size="small" @click="openPolicy()">新建</el-button></template>
        <el-table :data="policies"><el-table-column prop="name" label="名称"/><el-table-column prop="router_llm_id" label="Router LLM"/><el-table-column prop="default_role" label="默认角色"/><el-table-column label="操作" width="100"><template #default="{row}"><el-button link @click="openPolicy(row)">编辑</el-button></template></el-table-column></el-table>
      </el-card></el-col>
    </el-row>
    <el-dialog v-model="groupVisible" title="角色模型组" width="520px"><el-form :model="groupForm" label-width="100px"><el-form-item label="名称"><el-input v-model="groupForm.name"/></el-form-item><el-form-item label="角色"><el-select v-model="groupForm.role"><el-option v-for="r in roles" :key="r" :value="r"/></el-select></el-form-item><el-form-item label="首选模型"><el-select v-model="groupForm.preferred_llm_id" filterable><el-option v-for="l in eligible(groupForm.role)" :key="l.id" :label="l.name" :value="l.id"/></el-select></el-form-item><el-form-item label="回退模型"><el-select v-model="groupForm.fallback_llm_ids" multiple filterable><el-option v-for="l in eligible(groupForm.role)" :key="l.id" :label="l.name" :value="l.id"/></el-select></el-form-item><el-form-item label="预算"><el-input-number v-model="groupForm.budget" :min="0"/></el-form-item><el-form-item label="超时(秒)"><el-input-number v-model="groupForm.timeout" :min="0"/></el-form-item></el-form><template #footer><el-button @click="groupVisible=false">取消</el-button><el-button type="primary" @click="saveGroup">保存</el-button></template></el-dialog>
    <el-dialog v-model="policyVisible" title="路由策略" width="520px"><el-form :model="policyForm" label-width="100px"><el-form-item label="名称"><el-input v-model="policyForm.name"/></el-form-item><el-form-item label="Router LLM"><el-select v-model="policyForm.router_llm_id" filterable><el-option v-for="l in llms" :key="l.id" :label="l.name" :value="l.id"/></el-select></el-form-item><el-form-item label="角色模型组"><el-select v-model="policyForm.role_group_ids" multiple><el-option v-for="g in groups" :key="g.id" :label="`${g.name} (${g.role})`" :value="g.id"/></el-select></el-form-item><el-form-item label="默认角色"><el-select v-model="policyForm.default_role"><el-option v-for="g in selectedGroups" :key="g.id" :label="g.role" :value="g.role"/></el-select></el-form-item></el-form><template #footer><el-button @click="policyVisible=false">取消</el-button><el-button type="primary" @click="savePolicy">保存</el-button></template></el-dialog>
  </div>
</template>
<script setup>
import { computed, reactive, ref, onMounted } from 'vue'
import { getCgi, postCgi } from '../api'
const roles=['general','react_code','planner','multimodal','fast'], groups=ref([]), policies=ref([]), llms=ref([]), loading=ref(false), groupVisible=ref(false), policyVisible=ref(false)
const groupForm=reactive({}), policyForm=reactive({})
const selectedGroups=computed(()=>groups.value.filter(g=>policyForm.role_group_ids?.includes(g.id)))
const eligible=role=>llms.value.filter(l=>l.routing_capabilities?.enabled&&l.routing_capabilities.roles?.includes(role)&&l.routing_capabilities.runtimes?.includes('react'))
async function load(){loading.value=true;try{const [g,p,l]=await Promise.all(['role_group_list','policy_list','capability_list'].map(action=>getCgi('/pages/page_model_routing.cgi',{action})));groups.value=g.data||[];policies.value=p.data||[];llms.value=l.data||[]}finally{loading.value=false}}
function openGroup(row={}){Object.assign(groupForm,{id:'',name:'',role:'general',preferred_llm_id:'',fallback_llm_ids:[],budget:0,timeout:0,...row});groupVisible.value=true}
function openPolicy(row={}){Object.assign(policyForm,{id:'',name:'',router_llm_id:'',role_group_ids:[],default_role:'general',...row});policyVisible.value=true}
async function saveGroup(){await postCgi('/pages/page_model_routing.cgi',{action:groupForm.id?'role_group_update':'role_group_create',...groupForm});groupVisible.value=false;load()}
async function savePolicy(){await postCgi('/pages/page_model_routing.cgi',{action:policyForm.id?'policy_update':'policy_create',...policyForm});policyVisible.value=false;load()}
onMounted(load)
</script>
<style scoped>.page{background:#fff;padding:16px;border-radius:8px}.header p{color:#909399}.el-card :deep(.el-card__header){display:flex;justify-content:space-between;align-items:center}</style>
