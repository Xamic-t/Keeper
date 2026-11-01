import { createPinia } from 'pinia'
import { createApp } from 'vue'

import { onUnauthorized } from './api/client'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import './styles.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 会话过期（401）统一回登录页。注册放在这里而不是 client.ts 里 import router：
// client 是最底层，反过来依赖路由会形成循环引用。
onUnauthorized(() => {
  useAuthStore().reset()
  // replace 而不是 push：别把过期页面留在后退栈里
  void router.replace({ name: 'login' })
})

app.mount('#app')
