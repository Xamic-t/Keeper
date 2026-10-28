import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    { path: '/', name: 'today', component: () => import('@/views/TodayView.vue') },
    { path: '/ingest', name: 'ingest', component: () => import('@/views/IngestView.vue') },
    { path: '/persons', name: 'persons', component: () => import('@/views/PersonsView.vue') },
    {
      path: '/persons/:id',
      name: 'person',
      component: () => import('@/views/PersonDetailView.vue'),
    },
    { path: '/confirm', name: 'confirm', component: () => import('@/views/ConfirmView.vue') },
    { path: '/calendar', name: 'calendar', component: () => import('@/views/CalendarView.vue') },
    { path: '/todos', name: 'todos', component: () => import('@/views/TodosView.vue') },
    { path: '/settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  if (to.name === 'login') return true

  const auth = useAuthStore()
  const user = await auth.ensureSession()
  if (!user) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

export default router
