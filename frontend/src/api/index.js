import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000
})

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const detail = err.response?.data?.detail
    return Promise.reject(new Error(detail || err.message || '请求失败'))
  }
)

export const api = {
  health: () => http.get('/health'),
  meta: () => http.get('/meta'),
  config: () => http.get('/config'),
  saveConfig: (config) => http.put('/config', { config }),
  tests: () => http.get('/tests'),
  strategies: () => http.get('/strategies'),
  dataSources: () => http.get('/data-sources'),
  kline: (code, params = {}) => http.get(`/kline/${encodeURIComponent(code)}`, { params }),
  stockName: (code) => http.get(`/stock-name/${encodeURIComponent(code)}`),
  portfolioOptimize: (payload) => http.post('/portfolio-optimize', payload),
  intradayT: (code, params = {}) => http.get(`/intraday-t/${encodeURIComponent(code)}`, { params }),
  intradayTDetail: (code, date) => http.get(`/intraday-t/${encodeURIComponent(code)}/detail`, { params: { date } }),
  intradayMonitorStatus: () => http.get('/intraday-monitor/status'),
  intradayMonitorStart: () => http.post('/intraday-monitor/start'),
  intradayMonitorStop: () => http.post('/intraday-monitor/stop'),
  reports: () => http.get('/reports'),
  deleteReport: (name) => http.delete(`/reports/${encodeURIComponent(name)}`),
  startJob: (payload) => http.post('/jobs', payload),
  jobs: () => http.get('/jobs'),
  job: (id) => http.get(`/jobs/${id}`),
  jobLogs: (id, after = 0) => http.get(`/jobs/${id}/logs`, { params: { after } }),
  jobResult: (id) => http.get(`/jobs/${id}/result`),
  cancelJob: (id) => http.post(`/jobs/${id}/cancel`)
}

export default api
