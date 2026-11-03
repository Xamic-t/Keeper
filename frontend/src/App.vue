<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'

const route = useRoute()
const isLogin = computed(() => route.name === 'login')
// 管理后台是独立界面：不显示业务侧栏/底部 tab，避免"后台里还挂着业务菜单"
const isAdminConsole = computed(() => route.meta.layout === 'admin')
</script>

<template>
  <RouterView v-if="isLogin || isAdminConsole" />
  <AppShell v-else>
    <RouterView />
  </AppShell>
</template>
