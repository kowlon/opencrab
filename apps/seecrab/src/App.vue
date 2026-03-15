<template>
  <div class="app-layout">
    <div class="sidebar" style="width: var(--sidebar-width); background: var(--bg-secondary); border-right: 1px solid var(--border);">
      <div style="padding: 16px; text-align: center; color: var(--text-muted);">SeeCrab Sidebar</div>
    </div>
    <div class="chat-area" style="flex: 1; display: flex; align-items: center; justify-content: center;">
      <div style="text-align: center;">
        <h1 style="font-size: 36px; margin-bottom: 8px;">🦀 SeeCrab</h1>
        <p style="color: var(--text-secondary);">OpenAkita Agent 实时可视化</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}
</style>
