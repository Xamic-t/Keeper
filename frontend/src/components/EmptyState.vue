<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    hint?: string
  }>(),
  { hint: '' },
)
</script>

<template>
  <div class="card flex flex-col items-center gap-2 px-6 py-12 text-center">
    <div class="text-2xl text-ink-soft">◌</div>
    <div class="font-medium">{{ title }}</div>
    <div v-if="hint" class="text-sm text-ink-soft">{{ hint }}</div>
    <slot />
  </div>
</template>
