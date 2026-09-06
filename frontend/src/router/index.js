import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', name: 'Dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '仪表盘' } },
  { path: '/selection', name: 'Selection', component: () => import('../views/SelectionView.vue'), meta: { title: '选股系统' } },
  { path: '/portfolio-optimizer', name: 'PortfolioOptimizer', component: () => import('../views/PortfolioOptimizerView.vue'), meta: { title: '组合优化' } },
  { path: '/kline', name: 'StockDecision', component: () => import('../views/SuperKlineView.vue'), meta: { title: '个股决策' } },
  { path: '/backtest', name: 'PortfolioDecision', component: () => import('../views/BacktestView.vue'), meta: { title: '组合决策' } },
  { path: '/reports', name: 'Reports', component: () => import('../views/ReportsView.vue'), meta: { title: '报告' } },
  { path: '/config', name: 'Config', component: () => import('../views/ConfigView.vue'), meta: { title: '配置' } }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} · Quanti` : 'Quanti'
})

export default router
