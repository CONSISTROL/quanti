import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000
})

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const detail = err.response?.data?.detail
    // FastAPI 的参数校验错误(422)把 detail 返回成数组;直接丢进 Error 会显示成
    // "[object Object]",所以这里把数组摊平成可读文本。
    let message
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail
        .map((d) => `${(d.loc || []).filter((x) => x !== 'body' && x !== 'query').join('.')}: ${d.msg || ''}`)
        .join(';')
    } else {
      message = err.message || '请求失败'
    }
    return Promise.reject(new Error(message))
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
  cancelJob: (id) => http.post(`/jobs/${id}/cancel`),
  marketIndices: () => http.get('/market/indices'),
  marketOverview: (refresh = false) => http.get('/market/overview', { params: { refresh } }),
  marketChan: (code, interval = '1d', refresh = false) =>
    http.get(`/market/chan/${encodeURIComponent(code)}`, { params: { interval, refresh } }),
  marketSimulate: (code, interval = '1d', capital = 100000, refresh = false) =>
    http.get(`/market/chan/${encodeURIComponent(code)}/simulate`,
             { params: { interval, capital, refresh }, timeout: 120000 })
}

export default api
