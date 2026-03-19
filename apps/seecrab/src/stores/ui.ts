// apps/seecrab/src/stores/ui.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'

type RightPanelMode = 'step-detail' | 'subtask-output'

export const useUIStore = defineStore('ui', () => {
  const rightPanelOpen = ref(false)
  const selectedStepId = ref<string | null>(null)
  const thinkingExpanded = ref(false)
  const rightPanelMode = ref<RightPanelMode>('step-detail')
  const selectedSubtaskId = ref<string | null>(null)
  const selectedBPInstanceId = ref<string | null>(null)

  function selectStep(stepId: string) {
    selectedStepId.value = stepId
    rightPanelOpen.value = true
  }

  function openSubtaskOutput(instanceId: string, subtaskId: string) {
    selectedBPInstanceId.value = instanceId
    selectedSubtaskId.value = subtaskId
    rightPanelMode.value = 'subtask-output'
    rightPanelOpen.value = true
  }

  function closeRightPanel() {
    rightPanelOpen.value = false
    selectedStepId.value = null
    selectedSubtaskId.value = null
    selectedBPInstanceId.value = null
    rightPanelMode.value = 'step-detail'
  }

  function toggleThinking() {
    thinkingExpanded.value = !thinkingExpanded.value
  }

  return { rightPanelOpen, selectedStepId, thinkingExpanded, rightPanelMode, selectedSubtaskId, selectedBPInstanceId, selectStep, closeRightPanel, openSubtaskOutput, toggleThinking }
})
