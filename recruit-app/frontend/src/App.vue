<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()

const menus = [
  { path: '/search', label: '智能搜索', icon: 'Search' },
  { path: '/profiles', label: '岗位画像', icon: 'Document' },
  { path: '/runs', label: '搜索历史', icon: 'Clock' },
  { path: '/invites', label: '批量邀请', icon: 'Message' },
  { path: '/settings', label: '设置', icon: 'Setting' },
]
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px">
      <div class="brand">华联招聘</div>
      <el-menu :default-active="route.path" router>
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.layout {
  min-height: 100vh;
}
.brand {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 600;
  color: #1f2d3d;
  border-bottom: 1px solid #e6e6e6;
}
.el-aside {
  border-right: 1px solid #e6e6e6;
}
.el-menu {
  border-right: none;
}
</style>
