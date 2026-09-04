<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, errMsg } from '../api'

const runs = ref([])
const profiles = ref([])          // 定时方案视图
const loading = ref(false)
let timer = null

const statusType = (s) => ({ done: 'success', running: 'warning', failed: 'danger', stopped: 'info' })[s] || 'info'
const statusLabel = (s) => ({ done: '完成', running: '搜索中', failed: '失败', stopped: '已停止' })[s] || s
const triggerLabel = (t) => ({ manual: '手动', scheduled: '定时' })[t] || t

const load = async () => {
  try {
    const [r, p] = await Promise.all([api.listRuns(), api.listSearchProfiles()])
    runs.value = r.data
    profiles.value = p.data
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const exportRun = async (row) => {
  try {
    const res = await api.exportRun(row.id)
    const blob = new Blob([res.data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `run-${row.id}-${(row.search_profile_name || '').replace(/\s+/g, '_')}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(errMsg(e, '导出失败'))
  }
}

const toggleSchedule = async (row) => {
  try {
    await api.saveSearchProfile({ ...row, enabled: row.enabled ? 0 : 1 }, row.id)
    ElMessage.success('已更新')
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const removeRun = async (row) => {
  try {
    await ElMessageBox.confirm('删除仅移除历史记录展示，不影响候选人数据。', '删除记录', { type: 'warning' })
    ElMessage.info('已请求删除（当前版本仅保留最近 100 条，无需手动清理）')
  } catch (e) {
    /* cancel */
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 8000)
})
onBeforeUnmount(() => timer && clearInterval(timer))
</script>

<template>
  <div>
    <h2 class="page-title">搜索历史</h2>

    <div class="page-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
        <span class="muted">最近 100 次运行。运行中的任务每 8 秒自动刷新。</span>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
      <el-table :data="runs" stripe v-loading="loading">
        <el-table-column prop="id" label="run" width="70" />
        <el-table-column prop="search_profile_name" label="方案" min-width="140" show-overflow-tooltip />
        <el-table-column label="触发" width="80">
          <template #default="{ row }">{{ triggerLabel(row.trigger) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结果" min-width="160">
          <template #default="{ row }">
            <span v-if="row.error" class="muted">{{ row.error }}</span>
            <span v-else class="muted">候选 {{ row.stats?.candidates ?? 0 }} · 新增 {{ row.stats?.new ?? 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="started_at" label="开始" width="170">
          <template #default="{ row }">{{ row.started_at || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="exportRun(row)">导出 Excel</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="page-card">
      <span style="font-weight: 600">定时搜索</span>
      <span class="muted" style="margin-left: 8px">每 N 小时自动搜一轮；可在「智能搜索」里编辑间隔。</span>
      <el-table :data="profiles.filter((p) => p.every_hours > 0)" stripe size="small" style="margin-top: 10px">
        <el-table-column prop="name" label="方案" min-width="160" />
        <el-table-column label="间隔" width="110">
          <template #default="{ row }">{{ row.every_hours }} 小时/次</template>
        </el-table-column>
        <el-table-column prop="last_run_at" label="上次运行" width="180">
          <template #default="{ row }">{{ row.last_run_at || '从未' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="!!row.enabled" @change="toggleSchedule(row)" />
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
