<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'

const runs = ref([])
const selectedRunId = ref(null)
const candidates = ref([])
const selectedIds = ref([])
const jobs = ref([])
const jobId = ref('')
const greetMessage = ref('')
const invites = ref([])
const loadingJobs = ref(false)
const sending = ref(false)
const loadingInvites = ref(false)
let pollTimer = null

// —— M8 来源切换：按 Run（原逻辑）/ 按标签（跨 run 固定名单，不做门槛过滤） ——
const source = ref('run')
const tags = ref([])
const tagId = ref(null)
const tagMembers = ref([])
const loadingMembers = ref(false)

const loadRuns = async () => {
  try {
    const { data } = await api.listRuns()
    runs.value = data.filter((r) => r.status === 'done')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const onRun = async (rid) => {
  selectedRunId.value = rid
  selectedIds.value = []
  invites.value = []
  try {
    const { data } = await api.getRunCandidates(rid)
    candidates.value = data
    await loadInvites()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const loadTags = async () => {
  try {
    const { data } = await api.listTags()
    tags.value = data
  } catch (e) {
    ElMessage.error(errMsg(e, '加载标签失败'))
  }
}

const onTag = async (tid) => {
  tagId.value = tid
  selectedIds.value = []
  invites.value = []
  if (tid == null) {
    tagMembers.value = []
    return
  }
  loadingMembers.value = true
  try {
    const { data } = await api.getTagMembers(tid)
    tagMembers.value = data
    await loadInvites()
  } catch (e) {
    if (e?.response?.status === 404) {
      ElMessage.warning('该标签已不存在，请重新选择')
      tagId.value = null
      tagMembers.value = []
      loadTags()
    } else {
      ElMessage.error(errMsg(e))
    }
  } finally {
    loadingMembers.value = false
  }
}

const onSourceChange = async () => {
  selectedIds.value = []
  candidates.value = []
  tagMembers.value = []
  tagId.value = null
  invites.value = []
  if (source.value === 'tag') {
    loadTags()
  } else if (selectedRunId.value) {
    await loadInvites() // 回到 run 模式：恢复该 run 的邀请记录
  }
}

const loadJobs = async () => {
  loadingJobs.value = true
  try {
    const { data } = await api.listJobs()
    if (data.ok === false) {
      ElMessage.error(data.error || '拉取岗位失败')
      return
    }
    jobs.value = data.jobs || []
    if (!jobs.value.length) ElMessage.warning('未拉取到岗位，请先在企业端确认账号可发起邀请')
  } catch (e) {
    ElMessage.error(errMsg(e, '拉取岗位失败'))
  } finally {
    loadingJobs.value = false
  }
}

const loadInvites = async () => {
  // 按当前来源读：run 模式按 run_id；标签模式按 tag_id（标签内候选的邀请，跨 run）
  loadingInvites.value = true
  try {
    const byTag = source.value === 'tag' && tagId.value != null
    const byRun = source.value === 'run' && !!selectedRunId.value
    if (!byTag && !byRun) {
      invites.value = []
      return
    }
    const { data } = await api.listInvites(byTag ? { tagId: tagId.value } : { runId: selectedRunId.value })
    invites.value = data
  } catch (e) {
    if (source.value === 'tag' && e?.response?.status === 404) {
      ElMessage.warning('该标签已不存在，请重新选择')
      tagId.value = null
      invites.value = []
      loadTags()
    }
    /* 其余失败静默（轮询场景避免弹窗轰炸） */
  } finally {
    loadingInvites.value = false
  }
}

const send = async () => {
  if (!selectedIds.value.length) {
    ElMessage.warning('请勾选要邀请的候选人')
    return
  }
  if (!jobId.value) {
    ElMessage.warning('请选择邀请岗位')
    return
  }
  sending.value = true
  try {
    const { data } = await api.batchInvite({
      candidate_ids: selectedIds.value,
      job_id: jobId.value,
      run_id: source.value === 'tag' ? null : selectedRunId.value, // 标签模式无 run → 后端按 0 哨兵落库
      greet_message: greetMessage.value,
    })
    if (data.ok === false) {
      ElMessage.error(data.error || '发起失败')
    } else {
      ElMessage.success(`已创建 ${data.total} 条邀请（新增 ${data.created} 条），后台按间隔发送`)
      startPoll()
      await loadInvites()
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '发起失败'))
  } finally {
    sending.value = false
  }
}

const stop = async () => {
  try {
    await api.stopInvites()
    ElMessage.success('已请求停止后续邀请')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const retry = async (row) => {
  try {
    const { data } = await api.retryInvite(row.id)
    if (data.ok) ElMessage.success('已重新发送该条邀请')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const startPoll = () => {
  stopPoll()
  pollTimer = setInterval(() => loadInvites(), 4000)
}
const stopPoll = () => pollTimer && clearInterval(pollTimer)

const statusType = (s) => ({ sent: 'success', failed: 'danger', pending: 'warning' })[s] || 'info'
const candidatesWithGate = computed(() => candidates.value.filter((c) => c.hard_gate))
// 标签模式展示成员全量（标签是人工圈的名单，不再重复过滤硬门槛）
const rowSource = computed(() => (source.value === 'tag' ? tagMembers.value : candidatesWithGate.value))
const hasSource = computed(() => (source.value === 'tag' ? tagId.value != null : !!selectedRunId.value))
const panelTitle = computed(() => {
  if (source.value === 'tag') {
    const t = tags.value.find((x) => x.id === tagId.value)
    return `标签「${t ? t.name : tagId.value}」的成员（${tagMembers.value.length} 人）`
  }
  return `过硬性门槛的候选人（${candidatesWithGate.value.length} 人）`
})

onMounted(async () => {
  await loadRuns()
  loadTags()
})
</script>

<template>
  <div>
    <h2 class="page-title">批量邀请</h2>

    <div class="page-card">
      <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 8px">
        <span style="font-weight: 600">邀请来源</span>
        <el-radio-group v-model="source" @change="onSourceChange">
          <el-radio-button label="run">按 Run（单次搜索名单）</el-radio-button>
          <el-radio-button label="tag">按标签（跨 run 固定名单）</el-radio-button>
        </el-radio-group>
      </div>
      <div v-if="source === 'run'" style="display: flex; align-items: center; gap: 12px">
        <span class="muted">选择已完成的搜索</span>
        <el-select v-model="selectedRunId" placeholder="选择 run" style="width: 320px" @change="onRun">
          <el-option v-for="r in runs" :key="r.id" :value="r.id"
                     :label="`run #${r.id} · ${r.search_profile_name || ''} · ${r.started_at?.slice(5, 16) || ''}`" />
        </el-select>
        <el-button size="small" @click="loadRuns">刷新</el-button>
      </div>
      <div v-else style="display: flex; align-items: center; gap: 12px">
        <span class="muted">选择标签（在候选列表筛选后「存为新标签」/「加入已有标签」）</span>
        <el-select v-model="tagId" placeholder="选择标签" style="width: 320px" clearable @change="onTag">
          <el-option v-for="t in tags" :key="t.id" :value="t.id" :label="`${t.name}（${t.member_count} 人）`"
                     :disabled="t.member_count === 0" />
        </el-select>
        <el-button size="small" @click="loadTags">刷新</el-button>
      </div>
    </div>

    <div class="page-card" v-if="hasSource">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
        <span style="font-weight: 600">{{ panelTitle }}</span>
        <el-tag v-if="source === 'tag'" type="info" size="small" effect="plain">标签模式展示名单全量，需人工甄别门槛</el-tag>
        <el-button size="small" :loading="loadingJobs" @click="loadJobs">拉取我的岗位</el-button>
      </div>
      <el-table :data="rowSource" stripe size="small" max-height="360" v-loading="loadingMembers" @selection-change="(v) => (selectedIds = v.map((x) => x.id))">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="desired_title" label="期望职位" min-width="130" show-overflow-tooltip />
        <el-table-column prop="current_company" label="当前公司" min-width="130" show-overflow-tooltip />
        <el-table-column prop="desired_salary" label="期望薪资" width="110" show-overflow-tooltip />
        <el-table-column label="评分" width="80">
          <template #default="{ row }">{{ row.score ? row.score.total.toFixed(1) : '—' }}</template>
        </el-table-column>
      </el-table>

      <el-form label-width="90px" style="margin-top: 14px">
        <el-form-item label="邀请岗位">
          <el-select v-model="jobId" placeholder="请先拉取岗位，或直接填 ejobId" filterable allow-create style="width: 460px">
            <el-option v-for="j in jobs" :key="j.ejobId" :value="j.ejobId" :label="`${j.title || '未命名岗位'} (${j.ejobId})`" />
          </el-select>
        </el-form-item>
        <el-form-item label="招呼语">
          <el-input v-model="greetMessage" type="textarea" :rows="2"
                    placeholder="本轮仅发送职位预设招呼语；自定义内容暂未支持，请留空" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="sending" @click="send" :disabled="!selectedIds.length">批量发起邀请（{{ selectedIds.length }}）</el-button>
          <el-button type="danger" plain @click="stop">停止</el-button>
          <span class="muted" style="margin-left: 12px">后台按设置间隔逐条发送，本页 4 秒刷新状态</span>
        </el-form-item>
      </el-form>
    </div>

    <div class="page-card" v-if="hasSource">
      <span style="font-weight: 600">邀请记录</span>
      <el-tag v-if="source === 'tag'" size="small" style="margin-left: 8px" effect="plain">该标签内候选的邀请（跨 run）</el-tag>
      <el-table :data="invites" stripe size="small" style="margin-top: 8px" v-loading="loadingInvites">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="candidate_name" label="候选人" min-width="120" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ { sent: '已发送', failed: '失败', pending: '待发送' }[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="200" show-overflow-tooltip />
        <el-table-column prop="sent_at" label="发送时间" width="170">
          <template #default="{ row }">{{ row.sent_at || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button v-if="row.status === 'failed'" size="small" type="primary" plain @click="retry(row)">重试</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
