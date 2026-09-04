<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, errMsg } from '../api'

const props = defineProps({
  runId: { type: [Number, String], default: null },
  // 选中 run 的实时状态（''=未选中；running=搜索/抓取中）。running 期间本组件
  // 每 4s 静默重载候选（此前只在 runId 变化时 load 一次 → 运行中表格永远空）。
  status: { type: String, default: '' },
})
const router = useRouter()
const openDetail = (row) => router.push(`/candidates/${row.id}`)

const loading = ref(false)
const candidates = ref([])

// —— M8 筛选（纯前端计算，作用于当前已加载行；维度间 AND、维度内 OR） ——
const f = reactive({
  edu: [], workYears: '', city: [], ageMin: null, ageMax: null,
  scoreTier: '', status: [],
})
const WORK_YEAR_BUCKETS = [
  { label: '应届', min: 0, max: 1 },
  { label: '1-3年', min: 1, max: 3 },
  { label: '3-5年', min: 3, max: 5 },
  { label: '5-10年', min: 5, max: 10 },
  { label: '10年以上', min: 10, max: Infinity },
]
const STATUS_OPTS = [
  { key: 'is_new', label: '新简历' },
  { key: 'resume_fetched', label: '简历已抓取' },
  { key: 'invited', label: '已沟通过' },
  { key: 'hard_gate', label: '硬门槛通过' },
]
const SCORE_TIERS = [
  { key: '', label: '不限' },
  { key: 'high', label: '高（≥80）' },
  { key: 'mid', label: '中（60-80）' },
  { key: 'low', label: '低（<60）' },
  { key: 'unrated', label: '未评分' },
]
const eduOptions = computed(() => [...new Set(candidates.value.map((c) => c.edu).filter(Boolean))])
const cityOptions = computed(() => [...new Set(candidates.value.map((c) => c.current_city).filter(Boolean))])

// "12年"→12、"10年以上"→10、"应届"→0、无法解析→null
const wyToNum = (wy) => {
  if (wy == null) return null
  const m = String(wy).match(/\d+(?:\.\d+)?/)
  if (m) return parseFloat(m[0])
  return /应/.test(String(wy)) ? 0 : null
}
const tierHit = (c, tier) => {
  if (tier === 'unrated') return !c.score
  if (!c.score) return false
  const t = Number(c.score.total)
  if (tier === 'high') return t >= 80
  if (tier === 'mid') return t >= 60 && t < 80
  if (tier === 'low') return t < 60
  return false
}
const filtersActive = computed(() =>
  f.edu.length > 0 || !!f.workYears || f.city.length > 0 ||
  f.ageMin != null || f.ageMax != null || !!f.scoreTier || f.status.length > 0)
const shown = computed(() => {
  if (!filtersActive.value) return candidates.value
  const ageMin = f.ageMin != null ? Number(f.ageMin) : null
  const ageMax = f.ageMax != null ? Number(f.ageMax) : null
  const wyBucket = WORK_YEAR_BUCKETS.find((b) => b.label === f.workYears)
  return candidates.value.filter((c) => {
    if (f.edu.length && !f.edu.includes(c.edu)) return false
    if (wyBucket) {
      const n = wyToNum(c.work_years)
      if (n == null || n < wyBucket.min || n >= wyBucket.max) return false
    }
    if (f.city.length && !f.city.includes(c.current_city)) return false
    if (ageMin != null || ageMax != null) {
      const a = Number(c.age)
      if (Number.isNaN(a) || (ageMin != null && a < ageMin) || (ageMax != null && a > ageMax)) return false
    }
    if (f.scoreTier && !tierHit(c, f.scoreTier)) return false
    if (f.status.length && !f.status.some((k) => !!c[k])) return false
    return true
  })
})
const shownIds = () => shown.value.map((c) => c.id)
const resetFilters = () => {
  f.edu = []; f.workYears = ''; f.city = []
  f.ageMin = null; f.ageMax = null; f.scoreTier = ''; f.status = []
}

// —— M8 存标签（新标签 / 追加已有） ——
const tags = ref([])
const newDlg = ref(false)
const newName = ref('')
const addDlg = ref(false)
const addTagId = ref(null)
const savingTag = ref(false)
const loadTags = async () => {
  try {
    const { data } = await api.listTags()
    tags.value = data
  } catch (e) {
    ElMessage.error(errMsg(e, '加载标签失败'))
  }
}
const saveAsNewTag = async () => {
  const name = newName.value.trim()
  if (!name) return
  savingTag.value = true
  try {
    const { data } = await api.createTag(name)
    const r = await api.addTagMembers(data.id, shownIds())
    ElMessage.success(`已存入「${name}」${r.data.added} 人`)
    newDlg.value = false
    newName.value = ''
    loadTags()
  } catch (e) {
    ElMessage.error(errMsg(e, '保存标签失败'))
  } finally {
    savingTag.value = false
  }
}
const openAddDlg = () => { loadTags(); addDlg.value = true }
const doAddToTag = async () => {
  const tid = addTagId.value
  if (tid == null) return
  savingTag.value = true
  try {
    const t = tags.value.find((x) => x.id === tid)
    const r = await api.addTagMembers(tid, shownIds())
    ElMessage.success(`已加入「${t ? t.name : tid}」：新增 ${r.data.added} 人，跳过已在标签的 ${r.data.skipped} 人`)
    addDlg.value = false
    addTagId.value = null
    loadTags()
  } catch (e) {
    ElMessage.error(errMsg(e, '加入标签失败'))
  } finally {
    savingTag.value = false
  }
}

// —— M8 标签管理抽屉 ——
const drawerVisible = ref(false)
const curTagId = ref(null)
const tagMembers = ref([])
const memLoading = ref(false)
const curTag = computed(() => tags.value.find((t) => t.id === curTagId.value) || null)
const openTagDrawer = async () => {
  drawerVisible.value = true
  await loadTags()
  if (curTagId.value == null && tags.value.length) curTagId.value = tags.value[0].id
}
const loadTagMembers = async () => {
  if (curTagId.value == null) {
    tagMembers.value = []
    return
  }
  memLoading.value = true
  try {
    const { data } = await api.getTagMembers(curTagId.value)
    tagMembers.value = data
  } catch (e) {
    ElMessage.error(errMsg(e, '加载标签成员失败'))
  } finally {
    memLoading.value = false
  }
}
watch(curTagId, loadTagMembers)
const renameTag = async (t) => {
  let nv
  try {
    const r = await ElMessageBox.prompt('输入新名称', `重命名「${t.name}」`, {
      inputValue: t.name,
      inputValidator: (v) => (v && v.trim() ? true : '名称不能为空'),
    })
    nv = r.value
  } catch {
    return // 用户取消
  }
  try {
    await api.renameTag(t.id, nv.trim())
    ElMessage.success('已重命名')
    loadTags()
  } catch (e) {
    ElMessage.error(errMsg(e, '重命名失败'))
  }
}
const confirmDeleteTag = async (t) => {
  try {
    await api.deleteTag(t.id)
    ElMessage.success(`已删除「${t.name}」`)
    if (curTagId.value === t.id) curTagId.value = null
    await loadTags()
    if (curTagId.value == null && tags.value.length) curTagId.value = tags.value[0].id
  } catch (e) {
    ElMessage.error(errMsg(e, '删除标签失败'))
  }
}
const confirmRemoveMember = async (row) => {
  if (curTagId.value == null) return
  try {
    await api.removeTagMember(curTagId.value, row.id)
    ElMessage.success(`已从标签移除 ${row.name || '该候选人'}`)
    loadTags()
    loadTagMembers()
  } catch (e) {
    ElMessage.error(errMsg(e, '移除成员失败'))
  }
}

// —— 批量抓取简历：复刻 SearchConsole 的 4s 轮询 ——
const batchRunning = ref(false)
const batchInfo = ref(null) // { done, total }
let pollTimer = null

// —— 运行中候选自动刷新：run 处于 running 时每 4s 静默重载（runId 变化只 load 一次的历史缺陷）——
let runPollTimer = null

const stopRunPoll = () => {
  if (runPollTimer) {
    clearInterval(runPollTimer)
    runPollTimer = null
  }
}

const syncRunPoll = () => {
  stopRunPoll()
  if (props.runId && props.status === 'running') {
    runPollTimer = setInterval(() => load(true), 4000)
  }
}

const clearPoll = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const pollBatch = async () => {
  try {
    const { data } = await api.getResumeBatchStatus(props.runId)
    if (data.running) {
      batchInfo.value = { done: data.done, total: data.total }
      return // 继续轮询
    }
    clearPoll()
    batchRunning.value = false
    batchInfo.value = null
    const done = data.done ?? 0
    if (data.error) {
      ElMessage.warning(`批量抓取结束：${data.error}（已抓 ${done} 条会保留）`)
    } else {
      ElMessage.success(`批量抓取完成：共抓 ${done} 条简历`)
    }
    load() // 刷新「已抓」列
  } catch (e) {
    clearPoll()
    batchRunning.value = false
    batchInfo.value = null
    ElMessage.error(errMsg(e, '查询批量抓取状态失败'))
  }
}

const startBatch = async () => {
  batchRunning.value = true
  try {
    const { data } = await api.batchFetchResumes(props.runId)
    if (data.total === 0) {
      batchRunning.value = false
      ElMessage.info(data.message || '没有待抓取的简历')
      return
    }
    batchInfo.value = { done: 0, total: data.total }
    pollBatch() // 立即查一次，再进入轮询
    pollTimer = setInterval(pollBatch, 4000)
  } catch (e) {
    batchRunning.value = false
    if (e?.response?.status === 409) {
      ElMessage.warning(errMsg(e, '暂时无法启动批量抓取'))
    } else {
      ElMessage.error(errMsg(e, '启动批量抓取失败'))
    }
  }
}

const stopBatch = async () => {
  try {
    await api.stopResumeBatch(props.runId)
    ElMessage.info('已请求停止，正在抓取的最后一条完成后结束')
  } catch (e) {
    ElMessage.error(errMsg(e, '停止失败'))
  }
}

const load = async (silent = false) => {
  if (!props.runId) {
    candidates.value = []
    return
  }
  if (!silent) loading.value = true // 轮询刷新不打遮罩，避免表格 4s 闪一次
  try {
    const { data } = await api.getRunCandidates(props.runId)
    candidates.value = data
  } catch (e) {
    if (!silent) ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}

watch(() => props.runId, () => {
  clearPoll()
  stopRunPoll()
  batchRunning.value = false
  batchInfo.value = null
  resetFilters()
  load()
  syncRunPoll()
})
// running → 终态（done/failed/stopped）：停轮询并补一次最终数据（resume 抓取刚结束的最终状态）
watch(() => props.status, (s, prev) => {
  if (s === 'running') {
    syncRunPoll()
  } else if (prev === 'running' && s) {
    stopRunPoll()
    load(true)
  }
})
onMounted(() => {
  load()
  syncRunPoll()
})
onBeforeUnmount(() => {
  clearPoll()
  stopRunPoll()
})
</script>

<template>
  <div>
    <!-- M8 筛选栏：纯前端实时过滤表格（计数与表格同源 shown）；存标签作用于「已筛选出的行」 -->
    <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px">
      <el-select v-model="f.edu" multiple collapse-tags placeholder="学历" clearable style="width: 130px">
        <el-option v-for="e in eduOptions" :key="e" :label="e" :value="e" />
      </el-select>
      <el-select v-model="f.workYears" placeholder="工作年限" clearable style="width: 118px">
        <el-option v-for="b in WORK_YEAR_BUCKETS" :key="b.label" :label="b.label" :value="b.label" />
      </el-select>
      <el-select v-model="f.city" multiple collapse-tags placeholder="城市" clearable style="width: 130px">
        <el-option v-for="ct in cityOptions" :key="ct" :label="ct" :value="ct" />
      </el-select>
      <el-input-number v-model="f.ageMin" :min="18" :max="99" controls-position="right" placeholder="年龄≥" style="width: 100px" />
      <el-input-number v-model="f.ageMax" :min="18" :max="99" controls-position="right" placeholder="年龄≤" style="width: 100px" />
      <el-select v-model="f.scoreTier" placeholder="AI 评分" clearable style="width: 140px">
        <el-option v-for="t in SCORE_TIERS" :key="t.key" :label="t.label" :value="t.key" />
      </el-select>
      <el-select v-model="f.status" multiple collapse-tags placeholder="状态" clearable style="width: 160px">
        <el-option v-for="s in STATUS_OPTS" :key="s.key" :label="s.label" :value="s.key" />
      </el-select>
      <span v-if="filtersActive" class="muted">已筛选 {{ shown.length }} / {{ candidates.length }}</span>
      <el-button v-if="filtersActive" text size="small" @click="resetFilters">重置</el-button>
      <div style="flex: 1"></div>
      <el-button size="small" type="primary" plain :disabled="!shown.length" @click="newDlg = true">
        存为新标签
      </el-button>
      <el-button size="small" plain :disabled="!shown.length" @click="openAddDlg">
        加入已有标签
      </el-button>
      <el-button size="small" plain @click="openTagDrawer">标签管理</el-button>
    </div>

    <div style="display: flex; align-items: center; margin-bottom: 8px">
      <el-button size="small" type="primary" plain :loading="batchRunning"
                 :disabled="!props.runId" @click="startBatch">
        批量抓取简历
      </el-button>
      <el-link v-if="batchRunning" type="info" style="margin-left: 10px" @click="stopBatch">停止</el-link>
      <span v-if="batchRunning && batchInfo" class="muted" style="margin-left: 12px">
        正在抓取简历 {{ batchInfo.done }}/{{ batchInfo.total }}（每条约 15 秒）
      </span>
    </div>

    <div v-loading="loading">
      <!-- 历史缺陷：表格绑 candidates（全集）→ 筛选只影响计数与存标签，列表永远不动。
           改绑 shown（过滤后）→ 选条件即实时过滤显示。 -->
      <el-table :data="shown" stripe size="small"
                :empty-text="filtersActive ? '无符合筛选条件的候选人' : '暂无候选人'">
        <el-table-column prop="name" label="姓名" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.is_new" type="success" size="small" effect="plain" style="margin-right:4px">新</el-tag>
            <el-link type="primary" @click.prevent="openDetail(row)">{{ row.name || '—' }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="desired_title" label="期望职位" min-width="120" show-overflow-tooltip />
        <el-table-column prop="desired_salary" label="期望薪资" width="100" show-overflow-tooltip />
        <el-table-column prop="current_company" label="当前公司" min-width="120" show-overflow-tooltip />
        <el-table-column prop="work_years" label="年限" width="70" />
        <el-table-column prop="edu" label="学历" width="70" />
        <el-table-column prop="school" label="学校" min-width="110" show-overflow-tooltip />
        <el-table-column prop="age" label="年龄" width="60">
          <template #default="{ row }">{{ row.age ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="简历" width="90" fixed="right">
          <template #default="{ row }">
            <el-tooltip content="需企业账号已登录猎聘，未登录会跳转登录页" placement="top">
              <el-link v-if="row.resume_link" type="primary" :href="row.resume_link" target="_blank">打开</el-link>
              <span v-else class="muted">—</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="已抓" width="70" fixed="right">
          <template #default="{ row }">
            <el-tag v-if="row.resume_fetched" type="success" size="small" effect="plain">已抓</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">未抓</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="评分" width="120" sortable fixed="right">
          <template #default="{ row }">
            <template v-if="row.score">
              <span :class="row.score.total >= 80 ? 'score-high' : row.score.total >= 60 ? 'score-mid' : 'score-low'">
                {{ row.score.total.toFixed(1) }}
              </span>
              <el-tag :type="row.score.method === 'llm' ? 'danger' : 'info'" size="small" effect="plain" style="margin-left: 4px">
                {{ row.score.method === 'llm' ? 'AI' : '规则' }}
              </el-tag>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- M8 存为新标签 -->
    <el-dialog v-model="newDlg" title="存为新标签" width="440px">
      <el-input v-model="newName" placeholder="标签名（必填，如：A 组-待沟通）" maxlength="30"
                @keyup.enter="saveAsNewTag" />
      <p class="muted" style="margin: 10px 0 0">
        保存当前筛选出的 {{ shown.length }} 人作为固定名单；标签跨 run 累积，之后其他 run 的人可继续追加。
      </p>
      <template #footer>
        <el-button @click="newDlg = false">取消</el-button>
        <el-button type="primary" :loading="savingTag" :disabled="!newName.trim()" @click="saveAsNewTag">
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- M8 加入已有标签 -->
    <el-dialog v-model="addDlg" title="加入已有标签" width="440px">
      <el-select v-model="addTagId" placeholder="选择标签" style="width: 100%">
        <el-option v-for="t in tags" :key="t.id" :label="`${t.name}（${t.member_count} 人）`" :value="t.id" />
      </el-select>
      <p class="muted" style="margin: 10px 0 0">
        把当前筛选出的 {{ shown.length }} 人追加进标签；已在标签内的会自动跳过。
      </p>
      <template #footer>
        <el-button @click="addDlg = false">取消</el-button>
        <el-button type="primary" :loading="savingTag" :disabled="addTagId == null" @click="doAddToTag">
          加入
        </el-button>
      </template>
    </el-dialog>

    <!-- M8 标签管理抽屉 -->
    <el-drawer v-model="drawerVisible" title="标签管理（跨 run 累积的固定名单）" size="720px">
      <div style="display: flex; gap: 12px">
        <div class="tag-side">
          <div v-for="t in tags" :key="t.id" class="tag-item" :class="{ active: t.id === curTagId }"
               @click="curTagId = t.id">
            <span class="tag-name">{{ t.name }}<span class="muted"> · {{ t.member_count }}</span></span>
            <span class="tag-ops">
              <el-button text size="small" @click.stop="renameTag(t)">改名</el-button>
              <el-popconfirm title="删除该标签？成员会一并移除。" width="210" @confirm="confirmDeleteTag(t)">
                <template #reference>
                  <el-button text size="small" type="danger" @click.stop>删除</el-button>
                </template>
              </el-popconfirm>
            </span>
          </div>
          <div v-if="!tags.length" class="muted" style="padding: 8px">
            还没有标签：先在上方筛选，点「存为新标签」。
          </div>
        </div>
        <div style="flex: 1; min-width: 0">
          <div v-if="curTag" class="muted" style="margin-bottom: 6px">
            「{{ curTag.name }}」的 {{ tagMembers.length }} 名成员（可能来自多个 run）
          </div>
          <el-table :data="tagMembers" size="small" v-loading="memLoading" height="calc(100vh - 210px)"
                    empty-text="该标签暂无成员">
            <el-table-column prop="name" label="姓名" width="96">
              <template #default="{ row }">
                <el-link type="primary" @click.prevent="openDetail(row)">{{ row.name || '—' }}</el-link>
              </template>
            </el-table-column>
            <el-table-column prop="desired_title" label="期望职位" min-width="110" show-overflow-tooltip />
            <el-table-column prop="current_company" label="当前公司" min-width="100" show-overflow-tooltip />
            <el-table-column prop="work_years" label="年限" width="62" />
            <el-table-column prop="edu" label="学历" width="62" />
            <el-table-column prop="current_city" label="城市" width="80" show-overflow-tooltip />
            <el-table-column label="操作" width="70" fixed="right">
              <template #default="{ row }">
                <el-popconfirm title="从标签移除该候选人？" width="200" @confirm="confirmRemoveMember(row)">
                  <template #reference>
                    <el-button text type="danger" size="small">移除</el-button>
                  </template>
                </el-popconfirm>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.score-high { color: #67c23a; font-weight: 600; }
.score-mid { color: #e6a23c; font-weight: 600; }
.score-low { color: #f56c6c; font-weight: 600; }
.tag-side { width: 210px; flex-shrink: 0; border-right: 1px solid #ebeef5; padding-right: 10px; }
.tag-item { display: flex; justify-content: space-between; align-items: center; gap: 4px;
             padding: 5px 8px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; }
.tag-item:hover { background: #f5f7fa; }
.tag-item.active { background: #ecf5ff; color: #409eff; }
.tag-item .tag-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag-ops { visibility: hidden; flex-shrink: 0; }
.tag-item:hover .tag-ops,
.tag-item.active .tag-ops { visibility: visible; }
</style>
