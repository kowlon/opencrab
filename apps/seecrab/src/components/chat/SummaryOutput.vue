<template>
  <div class="summary-output" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{ content: string }>()
const { render } = useMarkdown()
const rendered = computed(() => render(props.content))
</script>

<style scoped>
.summary-output {
  padding: 8px 0; font-size: 14px; line-height: 1.7; color: var(--text-primary);
}
.summary-output :deep(pre) {
  background: var(--bg-tertiary); padding: 12px; border-radius: 8px;
  overflow-x: auto; font-size: 13px; margin: 8px 0;
}
.summary-output :deep(code) { font-family: 'Fira Code', monospace; }
</style>
