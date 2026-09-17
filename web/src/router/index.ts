import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth.ts'
import DashboardView from '@/views/DashboardView.vue'
import AccountsView from '@/views/AccountsView.vue'
import SettingsView from '@/views/SettingsView.vue'
import LoginView from '@/views/LoginView.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    name: 'dashboard',
    component: DashboardView,
    meta: { title: '控制面板' },
  },
  {
    path: '/accounts',
    name: 'accounts',
    component: AccountsView,
    meta: { title: '账号管理' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: SettingsView,
    meta: { title: '系统配置' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to, _from, next) => {
  // 设置文档标题
  if (to.meta.title && typeof to.meta.title === 'string') {
    document.title = `${to.meta.title} - AI Studio Proxy`
  }

  const authStore = useAuthStore()

  // 初始化鉴权状态
  if (!authStore.initialized) {
    await authStore.checkAuth()
  }

  const isPublic = to.meta.public === true

  if (authStore.authEnabled) {
    if (!authStore.token) {
      if (!isPublic) {
        return next({ name: 'login', query: { redirect: to.fullPath } })
      }
    } else {
      if (to.name === 'login') {
        return next({ name: 'dashboard' })
      }
    }
  } else {
    if (to.name === 'login') {
      return next({ name: 'dashboard' })
    }
  }

  next()
})
