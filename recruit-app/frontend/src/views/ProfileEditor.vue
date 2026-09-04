<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg } from '../api'

const profiles = ref([])
const loading = ref(false)

const dialog = ref(false)
const editingId = ref(null)
const form = ref({ name: '', version: '1.0', is_default: false, profile: {} })
const jsonText = ref('')
const parsedText = ref('')     // PDF 解析出的原文（供核对）
const parsing = ref(false)

const load = async () => {
  loading.value = true
  try {
    const { data } = await api.listProfiles()
    profiles.value = data
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  editingId.value = null
  form.value = { name: '', version: '1.0', is_default: false, profile: {} }
  jsonText.value = '{\n  "岗位名称": "",\n  "岗位名称变体": [],\n  "硬性门槛": {},\n  "加分项": [],\n  "五维权重": {}\n}'
  parsedText.value = ''
  dialog.value = true
}

const openEdit = async (row) => {
  editingId.value = row.id
  try {
    const { data } = await api.getProfile(row.id)
    form.value = { name: data.name, version: data.version, is_default: !!data.is_default, profile: data.profile }
    jsonText.value = JSON.stringify(data.profile, null, 2)
    parsedText.value = ''
    dialog.value = true
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const onFile = async (file) => {
  parsing.value = true
  try {
    const { data } = await api.parseProfilePdf(file.raw)
    if (data.ok === false) {
      ElMessage.error(data.error || 'PDF 解析失败')
      return
    }
    form.value.profile = data.profile
    jsonText.value = JSON.stringify(data.profile, null, 2)
    parsedText.value = data.text || ''
    ElMessage.success(data.source === 'llm' ? '已用 AI 解析 PDF' : 'AI 未配置，已用规则解析 PDF')
  } catch (e) {
    ElMessage.error(errMsg(e, 'PDF 解析失败'))
  } finally {
    parsing.value = false
  }
}

const save = async () => {
  if (!form.value.name) {
    ElMessage.warning('请输入画像名称')
    return
  }
  let profile
  try {
    profile = JSON.parse(jsonText.value)
  } catch (e) {
    ElMessage.warning('画像内容不是合法 JSON：' + e.message)
    return
  }
  try {
    const data = { name: form.value.name, version: form.value.version, profile, is_default: form.value.is_default }
    await api.saveProfile(data, editingId.value)
    ElMessage.success('已保存画像')
    dialog.value = false
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const remove = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除画像「${row.name}」？`, '删除', { type: 'warning' })
    await api.deleteProfile(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errMsg(e))
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2 class="page-title">岗位画像</h2>

    <div class="page-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
        <span class="muted">岗位要求 PDF → 结构化画像。AI 未配置时自动用规则抽取，可人工校对后保存。</span>
        <el-button type="primary" @click="openCreate">新建画像</el-button>
      </div>
      <el-table :data="profiles" stripe v-loading="loading">
        <el-table-column prop="name" label="画像名称" min-width="200" />
        <el-table-column prop="version" label="版本" width="90" />
        <el-table-column label="默认" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="180" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog" :title="editingId ? '编辑画像' : '新建画像'" width="720px">
      <el-form label-width="80px" label-position="top">
        <el-row :gutter="12">
          <el-col :span="14">
            <el-form-item label="画像名称">
              <el-input v-model="form.name" placeholder="如：技术1号位（研发负责人）" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="版本">
              <el-input v-model="form.version" placeholder="V1.0" />
            </el-form-item>
          </el-col>
          <el-col :span="4">
            <el-form-item label="设为默认">
              <el-switch v-model="form.is_default" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="从岗位要求 PDF 生成（可选）">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".pdf"
            :on-change="onFile"
          >
            <el-button :loading="parsing" type="primary" plain>上传 PDF 解析</el-button>
          </el-upload>
        </el-form-item>

        <el-form-item label="画像内容（JSON）">
          <el-input v-model="jsonText" type="textarea" :rows="14" style="font-family: monospace" />
        </el-form-item>

        <el-collapse v-if="parsedText">
          <el-collapse-item title="PDF 原文（核对用）" name="raw">
            <pre style="max-height: 200px; overflow: auto; font-size: 12px; white-space: pre-wrap">{{ parsedText }}</pre>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="save">保存画像</el-button>
      </template>
    </el-dialog>
  </div>
</template>
