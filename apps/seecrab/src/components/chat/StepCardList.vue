<template>
  <div class="step-card-list">
    <StepCard v-for="card in cards" :key="card.stepId" :card="card" />
  </div>
</template>

<script setup lang="ts">
import type { StepCard as StepCardType } from '@/types'
import StepCard from './StepCard.vue'
defineProps<{ cards: StepCardType[] }>()
</script>

<style scoped>
.step-card-list { margin: 8px 0; }
</style>
