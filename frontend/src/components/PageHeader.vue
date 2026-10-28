<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    actionLabel?: string
  }>(),
  { subtitle: '', actionLabel: '' },
)

const emit = defineEmits<{ (event: 'action'): void }>()
</script>

<template>
  <header class="mb-5 flex flex-wrap items-end justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold tracking-tight">{{ title }}</h1>
      <p v-if="subtitle" class="mt-0.5 text-sm text-ink-soft">{{ subtitle }}</p>
    </div>
    <button v-if="actionLabel" class="btn btn-primary" @click="emit('action')">
      {{ actionLabel }}
    </button>
  </header>
</template>
