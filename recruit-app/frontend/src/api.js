import axios from 'axios'

// 搜索一轮可能要几十秒到几分钟（限速），超时放宽
const http = axios.create({
  baseURL: '/api',
  timeout: 1500000,
})

export const api = {
  // settings
  getSettings: () => http.get('/settings'),
  putSettings: (values) => http.put('/settings', { values }),
  testLiepin: (values) => http.post('/settings/liepin/test', values),
  importLiepin: () => http.post('/settings/liepin/import'),

  // profiles（人才画像）
  listProfiles: () => http.get('/profiles'),
  getProfile: (id) => http.get(`/profiles/${id}`),
  saveProfile: (data, id) => (id ? http.put(`/profiles/${id}`, data) : http.post('/profiles', data)),
  deleteProfile: (id) => http.delete(`/profiles/${id}`),
  parseProfilePdf: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post('/profiles/parse-pdf', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000,
    })
  },

  // search profiles（搜索方案）
  listSearchProfiles: () => http.get('/search-profiles'),
  saveSearchProfile: (data, id) => (id ? http.put(`/search-profiles/${id}`, data) : http.post('/search-profiles', data)),
  deleteSearchProfile: (id) => http.delete(`/search-profiles/${id}`),
  runSearchProfile: (id) => http.post(`/search-profiles/${id}/run`),
  stopSearchProfile: (id) => http.post(`/search-profiles/${id}/stop`),

  // runs
  listRuns: () => http.get('/runs'),
  getRun: (id) => http.get(`/runs/${id}`),
  getRunCandidates: (id) => http.get(`/runs/${id}/candidates`),
  scoreRun: (id) => http.post(`/runs/${id}/score`),
  exportRun: (id) => http.get(`/runs/${id}/export`, { responseType: 'blob' }),

  // 批量抓取简历（run 自动抓取的手动补充）
  batchFetchResumes: (id) => http.post(`/runs/${id}/refetch-resumes`),
  getResumeBatchStatus: (id) => http.get(`/runs/${id}/refetch-resumes/status`),
  stopResumeBatch: (id) => http.post(`/runs/${id}/refetch-resumes/stop`),

  // candidates
  getCandidate: (id) => http.get(`/candidates/${id}`),
  refetchCandidate: (id) => http.post(`/candidates/${id}/refetch`),

  // invites（run_id 或 tag_id 二选一过滤）
  listJobs: () => http.get('/liepin/jobs'),
  batchInvite: (data) => http.post('/invites/batch', data),
  stopInvites: () => http.post('/invites/stop'),
  retryInvite: (id) => http.post(`/invites/${id}/retry`),
  listInvites: ({ runId, tagId } = {}) =>
    http.get('/invites', { params: { ...(runId ? { run_id: runId } : {}), ...(tagId ? { tag_id: tagId } : {}) } }),

  // 标签（固定名单快照）
  listTags: () => http.get('/tags'),
  createTag: (name) => http.post('/tags', { name }),
  renameTag: (id, name) => http.put(`/tags/${id}`, { name }),
  deleteTag: (id) => http.delete(`/tags/${id}`),
  getTagMembers: (id) => http.get(`/tags/${id}/members`),
  addTagMembers: (id, candidateIds) => http.post(`/tags/${id}/members`, { candidate_ids: candidateIds }),
  removeTagMember: (id, candidateId) => http.delete(`/tags/${id}/members/${candidateId}`),
}

// 统一错误提示
export function errMsg(e, fallback = '请求失败') {
  return e?.response?.data?.detail || e?.message || fallback
}
