<template>
  <div class="output-viewer">
    <h4>输出</h4>
    <div class="output-content" v-html="rendered"></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'
const props = defineProps<{ content: string }>()
const { render } = useMarkdown()
const rendered = computed(() => render(props.content))
</script>

<style scoped>
.output-viewer h4 { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }
.output-content {
  background: var(--bg-primary); padding: 12px; border-radius: 8px;
  font-size: 13px; line-height: 1.6; max-height: 400px; overflow-y: auto;
}
</style>
