<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errMsg } from '../api'

const loading = ref(false)
const testing = ref(false)
const importing = ref(false)

// 表单值。密文字段用 "已配置" 占位表示已有值。
const form = reactive({
  liepin_cookie: '',
  liepin_cookie_type: 'header',
  llm_base_url: '',
  llm_api_key: '',
  llm_model: 'deepseek-chat',
  delay_search: '5',
  delay_resume: '15',
  delay_greet: '45',
  invite_job_id: '',
  greet_message: '',
  invite_verified: false,
  browser_headless: 'true',
  browser_channel: 'chromium',
  browser_executable: '',
  browser_attach: 'false',
  http_proxy: '',
  https_proxy: '',
})

const MASKED = '已配置'
const masked = reactive({ liepin_cookie: false, llm_api_key: false })

const load = async () => {
  loading.value = true
  try {
    const { data } = await api.getSettings()
    for (const k of Object.keys(form)) {
      if (data[k] === MASKED) {
        form[k] = ''
        masked[k] = true
      } else {
        form[k] = data[k] ?? ''
      }
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}

const save = async () => {
  const values = {}
  for (const k of Object.keys(form)) {
    if (k === 'liepin_cookie' || k === 'llm_api_key') {
      // 未填且已有值：不提交（后端跳过掩码占位）；清空占位 = 清除
      if (masked[k] && !form[k]) continue
      if (masked[k] && form[k] === MASKED) continue
    }
    values[k] = form[k]
  }
  try {
    const { data } = await api.putSettings(values)
    ElMessage.success(`已保存 ${data.changed.length} 项`)
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const testLiepin = async () => {
  // 关键语义：搜索/邀请用的是「已保存」的设置，不是输入框里未保存的内容。
  // 因此：粘贴了新 Cookie → **先保存再探测**；『liepin 浏览器』开关同理——
  // 探测走「复用常驻 Chrome」还是「已保存 Cookie」，取决于保存后的 browser_attach，
  // 所以点击探测前先把开关值同步保存（幂等，非密文）。
  const attachOn = String(form.browser_attach) === 'true'
  const pasted = form.liepin_cookie && form.liepin_cookie.trim() !== MASKED ? form.liepin_cookie.trim() : ''
  if (!pasted && !masked.liepin_cookie && !attachOn) {
    ElMessage.warning('请先粘贴猎聘 Cookie（点击测试连接会自动保存）；或开启『liepin 浏览器』模式，复用已登录的 Chrome 直接测试')
    return
  }
  testing.value = true
  try {
    // 注意：api.putSettings 内部会包一层 {values}，这里直接传值对象，不要再包。
    if (pasted) {
      await api.putSettings({
        liepin_cookie: pasted,
        liepin_cookie_type: form.liepin_cookie_type,
        browser_attach: form.browser_attach,
      })
      masked.liepin_cookie = false
    } else {
      await api.putSettings({ browser_attach: form.browser_attach })
    }
    const { data } = await api.testLiepin({
      liepin_cookie: '',
      liepin_cookie_type: form.liepin_cookie_type,
      use_existing: true,
    })
    if (data.ok) {
      ElMessage.success((pasted ? 'Cookie 已保存；' : '') + (data.message || (attachOn ? '已复用常驻 Chrome，猎聘登录态有效' : '会话有效，猎聘连接成功')))
    } else {
      ElMessage.error(data.error || '验证失败')
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '测试连接失败'))
  } finally {
    testing.value = false
  }
}

const importLiepin = async () => {
  // 手动触发「从 liepin login 导入」：从常驻 Chrome 导出会话 → 自动保存到设置。
  // 平时无需点：每次终端里跑完 liepin login，系统会在 ~20 秒内自动导入。
  importing.value = true
  try {
    const { data } = await api.importLiepin()
    if (data.ok) {
      ElMessage.success(data.message || 'Cookie 已从 liepin login 导入')
      masked.liepin_cookie = true
      form.liepin_cookie = ''
      await load()
    } else {
      ElMessage.error(data.error || '导入失败（常见：liepin 登录浏览器没在运行）')
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '导入失败'))
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2 class="page-title">设置</h2>

    <div class="page-card">
      <h3>猎聘企业版账号</h3>
      <el-form label-width="140px" label-position="top">
        <el-form-item label="Cookie 格式">
          <el-radio-group v-model="form.liepin_cookie_type">
            <el-radio value="header">Cookie 头（浏览器 DevTools 复制的完整字符串）</el-radio>
            <el-radio value="json">EditThisCookie 导出的 JSON</el-radio>
            <el-radio value="pairs">name=value 对（每行一个）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="猎聘 Cookie（仅本机加密存储，绝不外传）">
          <el-input
            v-model="form.liepin_cookie"
            type="textarea"
            :rows="4"
            :placeholder="masked.liepin_cookie ? '当前已配置（粘贴新值覆盖，或清空后保存以清除）' : '从浏览器 DevTools → Application → Cookies 复制 Cookie 头'"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" plain :loading="importing" @click="importLiepin">从 liepin login 导入</el-button>
          <el-button type="primary" :loading="testing" @click="testLiepin">测试连接</el-button>
          <span class="muted" style="margin-left: 12px">
            <b>每次在终端里跑完 liepin login，系统都会在约 20 秒内自动把新会话导入到这里</b>
            （静默模式跑任务全靠这份 Cookie 登录，无需粘贴）；也可以点「从 liepin login 导入」立即导入。
            登录态失效（-1701）后跑一次 liepin login 即可自愈。
            系统绝不会自动登录，登录始终由你在终端手动完成。
          </span>
        </el-form-item>
      </el-form>
    </div>

    <div class="page-card">
      <h3>AI 评分（可选，OpenAI 兼容）</h3>
      <el-form label-width="140px" label-position="top">
        <el-form-item label="Base URL">
          <el-input v-model="form.llm_base_url" placeholder="如 https://api.deepseek.com/v1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.llm_api_key"
            type="password"
            show-password
            :placeholder="masked.llm_api_key ? '当前已配置（粘贴新值覆盖，或清空后保存以清除）' : ''"
          />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.llm_model" placeholder="deepseek-chat" />
        </el-form-item>
        <el-form-item>
          <span class="muted">
            不配置也能用：系统会以规则打分兜底。配置后可对候选简历做 AI 深度评分。
          </span>
        </el-form-item>
      </el-form>
    </div>

    <div class="page-card">
      <h3>搜索与触达</h3>
      <el-form label-width="140px" label-position="top">
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="搜索间隔（秒）">
              <el-input-number v-model="form.delay_search" :min="2" :max="60" controls-position="right" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="简历抓取间隔（秒）">
              <el-input-number v-model="form.delay_resume" :min="5" :max="120" controls-position="right" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="打招呼间隔（秒）">
              <el-input-number v-model="form.delay_greet" :min="10" :max="300" controls-position="right" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="默认邀请岗位 ejobId">
          <el-input v-model="form.invite_job_id" placeholder="猎聘岗位链接中的 ejobId（邀请投递目标）" />
        </el-form-item>
        <el-form-item label="打招呼模板（暂不支持自定义，请留空）">
          <el-input
            v-model="form.greet_message"
            type="textarea"
            :rows="3"
            placeholder="本轮仅发送职位预设招呼语；自定义内容暂未支持，留空否则发送会失败"
          />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.invite_verified">
            邀请链路已验证（用 1 位候选人真实发送成功后再勾选；勾选后开放批量邀请）
          </el-checkbox>
          <span class="muted" style="display: block; margin-top: 4px">
            未勾选时单批最多 1 位，防止未经验证就静默批量触达候选人。
          </span>
        </el-form-item>
      </el-form>
    </div>

    <div class="page-card">
      <h3>浏览器（猎聘自动操作）</h3>
      <el-form label-width="140px" label-position="top">
        <el-form-item>
          <el-switch
            v-model="form.browser_attach"
            active-value="true"
            inactive-value="false"
            active-text="在登录的 Chrome 里开着页面操作（可选）"
            inactive-text="完全静默运行（默认，与原版一致）"
          />
          <div class="muted" style="margin-top: 6px; line-height: 1.6">
            <b>默认（开关关闭）＝ 原版静默模式</b>：运行/拉岗位/邀请全程<b>不弹出任何猎聘页面</b>，
            用下方设置的浏览器（默认 Chrome 无头引擎）在后台跑，靠已保存的 Cookie 保持登录。
            Cookie 来源：在终端跑一次 <b>liepin login</b>（会自动打开 Chrome 完成登录），
            系统约 20 秒内自动把会话导入；也可点「从 liepin login 导入」立即导入。
            <br />
            仅在你想<b>亲眼看着</b>每一步操作时，再打开上面的开关：页面会在你登录的那个 Chrome 里
            以标签页形式打开（结束后自动关闭），不另弹新窗口，也绝不会关闭你的 Chrome。
          </div>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="无头模式">
              <el-select v-model="form.browser_headless" style="width: 100%">
                <el-option value="false" label="关闭（推荐：真实浏览器，降低风控）" />
                <el-option value="true" label="开启" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="浏览器通道">
              <el-select v-model="form.browser_channel" style="width: 100%">
                <el-option value="chrome" label="本机 Chrome（推荐）" />
                <el-option value="chromium" label="Playwright Chromium" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="浏览器可执行文件（可选）">
          <el-input v-model="form.browser_executable" placeholder="留空自动探测。WSL 下可用：/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" />
        </el-form-item>
        <el-form-item>
          <span class="muted">
            默认静默模式使用下方「无头模式 / 浏览器通道 / 可执行文件」以已保存 Cookie 后台运行：
            <b>无头模式保持开启</b>即完全不弹任何页面（原版行为）；「浏览器通道」选 Chrome 即用
            本机 Chrome 引擎跑（本机为 WSL2 无图形界面，走 Windows Chrome 无头）。
            仅当你打开上方「在登录的 Chrome 里开着页面操作」开关时，才忽略下方这些设置。
          </span>
        </el-form-item>
      </el-form>
    </div>

    <div class="page-card">
      <h3>代理（访问猎聘必需，通常是 Windows 科学上网工具）</h3>
      <el-form label-width="140px" label-position="top">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="HTTP 代理">
              <el-input v-model="form.http_proxy" placeholder="如 http://127.0.0.1:7890" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="HTTPS 代理">
              <el-input v-model="form.https_proxy" placeholder="如 http://127.0.0.1:7890" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item>
          <span class="muted">
            WSL 无法直连猎聘时（连接被重置）必填。填 Windows 侧代理端口即可，系统会自动走 WSL → 宿主机代理。
          </span>
        </el-form-item>
      </el-form>
    </div>

    <div style="text-align: right">
      <el-button type="primary" :loading="loading" @click="save">保存设置</el-button>
    </div>
  </div>
</template>
