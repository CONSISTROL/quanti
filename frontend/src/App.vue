<template>
  <el-container class="app-layout">
    <el-aside width="210px" class="sidebar">
      <div class="brand">
        <span class="brand-dot">Q</span>
        <div>
          <div class="brand-name">Quanti</div>
          <div class="brand-sub">A股量化控制台</div>
        </div>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#1e293b"
        text-color="#cbd5e1"
        active-text-color="#ffffff"
        class="side-menu"
      >
        <el-menu-item index="/dashboard">
          <el-icon><Odometer /></el-icon>
          <span>仪表盘</span>
        </el-menu-item>
        <el-menu-item index="/chan">
          <el-icon><Compass /></el-icon>
          <span>缠论看盘</span>
        </el-menu-item>
        <el-menu-item index="/selection">
          <el-icon><Grid /></el-icon>
          <span>选股系统</span>
        </el-menu-item>
        <el-menu-item index="/portfolio-optimizer">
          <el-icon><PieChart /></el-icon>
          <span>组合优化</span>
        </el-menu-item>
        <el-menu-item index="/kline">
          <el-icon><DataLine /></el-icon>
          <span>个股决策</span>
        </el-menu-item>
        <el-menu-item index="/intraday-t">
          <el-icon><AlarmClock /></el-icon>
          <span>日内做T</span>
        </el-menu-item>
        <el-menu-item index="/backtest">
          <el-icon><TrendCharts /></el-icon>
          <span>组合决策</span>
        </el-menu-item>
        <el-menu-item index="/reports">
          <el-icon><Document /></el-icon>
          <span>报告</span>
        </el-menu-item>
        <el-menu-item index="/config">
          <el-icon><Setting /></el-icon>
          <span>配置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-main class="main-content">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </el-main>
  </el-container>
</template>

<script setup>
</script>

<style scoped>
.app-layout {
  min-height: 100vh;
}
.sidebar {
  background: #1e293b;
  display: flex;
  flex-direction: column;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 18px 16px;
  color: #fff;
}
.brand-dot {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 20px;
}
.brand-name {
  font-weight: 700;
  font-size: 17px;
}
.brand-sub {
  font-size: 11px;
  opacity: 0.6;
}
.side-menu {
  border-right: none;
  flex: 1;
}
.side-menu .el-menu-item.is-active {
  background: rgba(59, 130, 246, 0.25) !important;
  border-right: 3px solid #3b82f6;
}
.main-content {
  padding: 0;
  background: #f0f2f5;
}
</style>
