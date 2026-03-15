import { ref, onUnmounted } from 'vue'

export function useTimer() {
  const displayTtft = ref<number | null>(null)
  const displayTotal = ref<number | null>(null)
  const isRunning = ref(false)
  let rafId = 0
  let startTime = 0

  function startPhase(phase: 'ttft' | 'total') {
    if (phase === 'total') {
      isRunning.value = true
      startTime = performance.now()
      tick()
    }
  }

  function endPhase(phase: 'ttft' | 'total', value: number) {
    if (phase === 'ttft') displayTtft.value = value
    if (phase === 'total') {
      displayTotal.value = value
      isRunning.value = false
      if (rafId) { cancelAnimationFrame(rafId); rafId = 0 }
    }
  }

  function tick() {
    displayTotal.value = Math.round((performance.now() - startTime) / 100) / 10
    rafId = requestAnimationFrame(tick)
  }

  onUnmounted(() => { if (rafId) cancelAnimationFrame(rafId) })

  return { displayTtft, displayTotal, isRunning, startPhase, endPhase }
}
