<template>
  <div class="welcome">
    <div class="welcome-inner">
      <h1 class="logo">🦀 SeeCrab</h1>
      <p class="subtitle">OpenAkita Agent 实时可视化</p>
      <div class="shortcuts">
        <button v-for="s in shortcuts" :key="s.label" class="shortcut" @click="$emit('prefill', s.prefill)">
          <span class="material-symbols-rounded">{{ s.icon }}</span>
          <span>{{ s.label }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineEmits<{ prefill: [text: string] }>()
const shortcuts = [
  { icon: 'search', label: '搜索', prefill: '帮我搜索 ' },
  { icon: 'code', label: '写代码', prefill: '帮我写 ' },
  { icon: 'description', label: '处理文档', prefill: '帮我处理文档 ' },
  { icon: 'analytics', label: '分析数据', prefill: '帮我分析 ' },
]
</script>

<style scoped>
.welcome { flex: 1; display: flex; align-items: center; justify-content: center; }
.welcome-inner { text-align: center; }
.logo { font-size: 36px; margin-bottom: 8px; }
.subtitle { color: var(--text-secondary); margin-bottom: 32px; }
.shortcuts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; max-width: 360px; }
.shortcut {
  display: flex; align-items: center; gap: 8px; padding: 12px 16px;
  background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 12px;
  color: var(--text-primary); cursor: pointer; font-size: 13px;
}
.shortcut:hover { background: var(--bg-hover); border-color: var(--accent); }
</style>
