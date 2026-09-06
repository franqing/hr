<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'

const route = useRoute()
const router = useRouter()

const cand = ref(null)
const loading = ref(false)
const refetching = ref(false)

const dims = ['专业能力', '技术领导力', '经营思维', '资源整合', '岗位契合度']

const load = async () => {
  loading.value = true
  try {
    const { data } = await api.getCandidate(route.params.id)
    cand.value = data
  } catch (e) {
    ElMessage.error(errMsg(e, '加载候选人失败'))
    router.push('/search')
  } finally {
    loading.value = false
  }
}

const refetch = async () => {
  refetching.value = true
  try {
    const { data } = await api.refetchCandidate(cand.value.id)
    if (data.ok === false) {
      ElMessage.error(data.error || '抓取失败')
    } else if (data.resume_fetched === false) {
      ElMessage.warning(data.error || '未取到简历内容（可能该简历不公开）')
    } else {
      ElMessage.success(`已抓取简历并重新评分（hard_gate=${data.hard_gate}）`)
    }
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e, '抓取失败'))
  } finally {
    refetching.value = false
  }
}

// 简历页在专用登录浏览器（与登录同 profile）新标签打开：默认浏览器未登录会要求重登
const openResume = async () => {
  const url = cand.value?.resume_link
  if (!url) return
  try {
    const { data } = await api.openResume(url)
    if (data && data.ok === false) ElMessage.error(data.error || '打开失败')
    else ElMessage.success(data?.message || '已在登录窗口打开简历页')
  } catch (e) {
    ElMessage.error(errMsg(e, '打开简历页失败'))
  }
}

const cardRows = () => {
  const c = cand.value?.card || {}
  return [
    ['期望职位', c.desired_title],
    ['期望薪资', c.desired_salary],
    ['现居地', c.current_city],
    ['工作年限', c.work_years],
    ['学历', c.edu],
    ['学校', c.school],
    ['当前公司', c.current_company],
    ['年龄', c.age ?? ''],
  ].filter(([, v]) => v !== undefined && v !== null && v !== '')
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <div style="margin-bottom: 12px">
      <el-button @click="router.push('/search')">← 返回搜索</el-button>
    </div>

    <template v-if="cand">
      <h2 class="page-title">
        {{ cand.name || '候选人' }}
        <el-tag v-if="cand.is_new" type="success" size="small" style="margin-left: 6px">新</el-tag>
        <el-tag v-if="cand.hard_gate" type="danger" size="small" style="margin-left: 6px">过硬性门槛</el-tag>
        <el-tag v-else type="info" size="small" style="margin-left: 6px">未过门槛</el-tag>
        <el-tag v-if="cand.resume_fetched" type="warning" size="small" style="margin-left: 6px">已抓简历</el-tag>
      </h2>

      <el-row :gutter="16">
        <el-col :span="12">
          <div class="page-card">
            <h3>候选人卡片</h3>
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item v-for="[k, v] in cardRows()" :key="k" :label="k">{{ v }}</el-descriptions-item>
            </el-descriptions>
            <p class="muted" style="margin-top: 8px">
              {{ cand.quick_reason || '未做过快速筛选' }}
            </p>
            <p v-if="cand.hard_reason" class="muted">{{ cand.hard_reason }}</p>
          </div>
        </el-col>

        <el-col :span="12">
          <div class="page-card">
            <div style="display: flex; justify-content: space-between; align-items: center">
              <h3>评分</h3>
              <div style="display: flex; align-items: center; gap: 14px">
                <el-tooltip content="在专用登录浏览器新标签打开（与登录同 profile，企业账号已登录）" placement="top">
                  <el-link type="primary" :disabled="!cand || !cand.resume_link" @click="openResume">
                    打开猎聘简历页
                  </el-link>
                </el-tooltip>
                <el-button size="small" type="primary" plain :loading="refetching" @click="refetch">
                  抓取简历并重新评分
                </el-button>
              </div>
            </div>
            <div v-if="cand.score">
              <div style="font-size: 28px; font-weight: 700">
                {{ cand.score.total.toFixed(1) }}
                <span class="muted" style="font-size: 13px; font-weight: 400">
                  / 100 · {{ cand.score.method === 'llm' ? 'LLM 深度评分' : '规则评分' }}
                </span>
              </div>
              <el-table :data="dims.map((d) => ({ d, v: cand.score.dims[d] ?? 0 }))" size="small" style="margin-top: 10px">
                <el-table-column prop="d" label="维度" />
                <el-table-column label="得分（0-20）">
                  <template #default="{ row }">
                    <el-progress :percentage="row.v * 5" :stroke-width="14" :show-text="true">
                      <span style="font-size: 12px">{{ row.v }}</span>
                    </el-progress>
                  </template>
                </el-table-column>
              </el-table>
              <p class="muted" style="margin-top: 8px">{{ cand.score.source }}</p>
              <p class="muted" v-if="cand.score.explanation?.LLM理由">{{ cand.score.explanation.LLM理由 }}</p>
            </div>
            <p v-else class="muted">暂无评分</p>
          </div>
        </el-col>
      </el-row>

      <div class="page-card">
        <h3>简历文本（已脱敏，不含手机/邮箱）</h3>
        <pre v-if="cand.resume?.text" class="resume-text">{{ cand.resume.text }}</pre>
        <p v-else-if="cand.resume?.error" class="muted">
          抓取失败：{{ cand.resume.error }}。可点击「抓取简历并重新评分」重试。
        </p>
        <p v-else class="muted">
          {{ cand.resume_fetched ? '简历内容为空（可能不公开）' : '尚未抓取。点击上方「抓取简历并重新评分」。' }}
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.resume-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.7;
  max-height: 480px;
  overflow: auto;
  background: #f8f9fb;
  border-radius: 6px;
  padding: 12px;
}
</style>
