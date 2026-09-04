<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, errMsg } from '../api'
import CandidateList from '../components/CandidateList.vue'

const router = useRouter()

const presets = [
  { value: 'full', label: '完整（公司+岗位+技术）' },
  { value: 'companies', label: '仅目标公司' },
  { value: 'titles', label: '仅岗位词' },
  { value: 'tech', label: '仅技术词' },
]

const profiles = ref([])
const jobProfiles = ref([])
const dialog = ref(false)
const editingId = ref(null)
const form = ref({ name: '', job_profile_id: null, plan_preset: 'full', every_hours: 0, enabled: true })

// 运行状态
const activeRuns = ref([])       // 正在运行的 runs（轮询刷新）
const selectedRunId = ref(null)
const selectedStatus = ref('')   // 选中 run 的实时状态（驱动候选列表 running 期自动刷新）
let timer = null

const loadProfiles = async () => {
  try {
    const { data } = await api.listSearchProfiles()
    profiles.value = data
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const loadJobProfiles = async () => {
  try {
    const { data } = await api.listProfiles()
    jobProfiles.value = data
  } catch (e) {
    jobProfiles.value = []   // M3 前无画像接口
  }
}

const pollRuns = async () => {
  try {
    const { data } = await api.listRuns()
    const running = data.filter((r) => r.status === 'running')
    activeRuns.value = running
    if (running.length === 1) {
      selectedRunId.value = running[0].id        // 单个运行中：自动跟随展示其候选
    } else if (!running.length && !selectedRunId.value && data.length) {
      selectedRunId.value = data[0].id           // 无运行且未选中：跟随最新一条
    }
    // 同步选中 run 的状态 → CandidateList 在 running 期间每 4s 自动刷新候选，
    // 进入终态后补一次最终数据（历史缺陷：候选只在 runId 变化时 load 一次，运行中永为空表）
    const cur = data.find((r) => r.id === selectedRunId.value)
    selectedStatus.value = cur ? cur.status : ''
  } catch (e) {
    /* 轮询失败静默 */
  }
}

const startTimer = () => {
  stopTimer()
  timer = setInterval(pollRuns, 4000)
}
const stopTimer = () => timer && clearInterval(timer)

const openCreate = () => {
  editingId.value = null
  form.value = { name: '', job_profile_id: null, plan_preset: 'full', every_hours: 0, enabled: true }
  dialog.value = true
}

const openEdit = (row) => {
  editingId.value = row.id
  form.value = { name: row.name, job_profile_id: row.job_profile_id, plan_preset: row.plan_preset, every_hours: row.every_hours, enabled: !!row.enabled }
  dialog.value = true
}

const save = async () => {
  if (!form.value.name) {
    ElMessage.warning('请输入方案名称')
    return
  }
  try {
    await api.saveSearchProfile({ ...form.value, every_hours: Number(form.value.every_hours) || 0 }, editingId.value)
    ElMessage.success('已保存')
    dialog.value = false
    await loadProfiles()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const remove = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除搜索方案「${row.name}」？历史运行记录保留。`, '删除', { type: 'warning' })
    await api.deleteSearchProfile(row.id)
    ElMessage.success('已删除')
    await loadProfiles()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(errMsg(e))
  }
}

const run = async (row) => {
  try {
    const { data } = await api.runSearchProfile(row.id)
    if (data.ok) {
      ElMessage.success(`已启动搜索（run #${data.run_id}）`)
      selectedRunId.value = data.run_id
      startTimer()
      await pollRuns()
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const stop = async (row) => {
  try {
    const { data } = await api.stopSearchProfile(row.id)
    ElMessage.success(data.ok ? '已请求停止' : data.error || '无运行中任务')
    await pollRuns()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const activeRunFor = (row) => activeRuns.value.find((r) => r.search_profile_id === row.id)

const openDetail = (cand) => router.push(`/candidates/${cand.id}`)

// M5：对当前 run 的候选做 AI 深度评分（LLM 未配置时自动降级规则分）
const scoring = ref(false)
const scoreCurrent = async () => {
  if (!selectedRunId.value) {
    ElMessage.warning('请先选择/运行一个搜索方案')
    return
  }
  scoring.value = true
  try {
    const { data } = await api.scoreRun(selectedRunId.value)
    if (data.message) ElMessage.info(data.message)
    else ElMessage.success(`已评分 ${data.scored} 人（AI ${data.llm} / 规则兜底 ${data.fallback}）`)
  } catch (e) {
    ElMessage.error(errMsg(e, '评分失败'))
  } finally {
    scoring.value = false
  }
}

onMounted(async () => {
  await loadProfiles()
  await loadJobProfiles()
  await pollRuns()
  startTimer()
})
onBeforeUnmount(stopTimer)
</script>

<template>
  <div>
    <h2 class="page-title">智能搜索</h2>

    <div class="page-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
        <span class="muted">按人才画像自动生成关键词与筛选，逐条调用猎聘企业版搜索</span>
        <el-button type="primary" @click="openCreate">新建搜索方案</el-button>
      </div>
      <el-table :data="profiles" stripe>
        <el-table-column prop="name" label="方案名称" min-width="140" />
        <el-table-column label="预设" width="160">
          <template #default="{ row }">{{ presets.find((p) => p.value === row.plan_preset)?.label || row.plan_preset }}</template>
        </el-table-column>
        <el-table-column prop="job_profile_name" label="关联画像" min-width="120">
          <template #default="{ row }">{{ row.job_profile_name || '（默认种子）' }}</template>
        </el-table-column>
        <el-table-column label="定时" width="100">
          <template #default="{ row }">{{ row.every_hours > 0 ? `${row.every_hours}h/次` : '关' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag v-if="activeRunFor(row)" type="warning" size="small" effect="dark" style="margin-right:6px">搜索中</el-tag>
            <span v-else class="muted">空闲</span>
            <span class="muted" v-if="row.last_run_at">上次 {{ row.last_run_at.slice(5, 16) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230">
          <template #default="{ row }">
            <el-button size="small" type="primary" :disabled="!!activeRunFor(row)" @click="run(row)">运行</el-button>
            <el-button size="small" :disabled="!activeRunFor(row)" @click="stop(row)">停止</el-button>
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="page-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
        <div>
          <span style="font-weight: 600">候选人</span>
          <el-tag v-if="selectedRunId" size="small" style="margin-left: 8px" type="info">
            run #{{ selectedRunId }}
          </el-tag>
          <span v-if="activeRuns.length" class="muted" style="margin-left: 12px">
            正在搜索 {{ activeRuns.length }} 轮…候选列表每 4 秒自动刷新
          </span>
        </div>
        <el-button type="primary" plain :loading="scoring" @click="scoreCurrent">AI 深度评分</el-button>
      </div>
      <CandidateList :run-id="selectedRunId" :status="selectedStatus" />
    </div>

    <el-dialog v-model="dialog" :title="editingId ? '编辑搜索方案' : '新建搜索方案'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="方案名称">
          <el-input v-model="form.name" placeholder="如：技术1号位 · 每周扫描" />
        </el-form-item>
        <el-form-item label="关联画像">
          <el-select v-model="form.job_profile_id" clearable placeholder="（使用默认种子画像）" style="width: 100%">
            <el-option v-for="jp in jobProfiles" :key="jp.id" :value="jp.id" :label="jp.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="搜索预设">
          <el-select v-model="form.plan_preset" style="width: 100%">
            <el-option v-for="p in presets" :key="p.value" :value="p.value" :label="p.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="定时（小时）">
          <el-input-number v-model="form.every_hours" :min="0" :max="720" controls-position="right" />
          <span class="muted" style="margin-left: 10px">0 = 不自动定时；&gt;0 则每隔该小时自动搜一轮</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
