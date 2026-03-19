// apps/seecrab/src/stores/bestpractice.ts
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { BPInstanceState, BPSubtaskInfo, BPRunMode } from '@/types'

export const useBestPracticeStore = defineStore('bestpractice', () => {
  const instances = ref<Map<string, BPInstanceState>>(new Map())
  const activeInstanceId = ref<string | null>(null)

  const activeInstance = computed(() =>
    activeInstanceId.value ? instances.value.get(activeInstanceId.value) ?? null : null
  )

  function updateFromProgress(event: {
    instance_id: string
    bp_name: string
    statuses: Record<string, string>
    current_subtask_index: number
    run_mode: string
    status: string
  }) {
    const existing = instances.value.get(event.instance_id)
    const subtasks: BPSubtaskInfo[] = Object.entries(event.statuses).map(([id, status]) => ({
      id,
      name: existing?.subtasks.find(s => s.id === id)?.name ?? id,
      status: status as BPSubtaskInfo['status'],
      output: existing?.subtasks.find(s => s.id === id)?.output,
    }))

    const state: BPInstanceState = {
      instanceId: event.instance_id,
      bpId: existing?.bpId ?? '',
      bpName: event.bp_name,
      status: event.status as BPInstanceState['status'],
      runMode: event.run_mode as BPRunMode,
      subtasks,
      currentSubtaskIndex: event.current_subtask_index,
    }
    instances.value.set(event.instance_id, state)
    activeInstanceId.value = event.instance_id
  }

  function updateSubtaskOutput(
    instanceId: string,
    subtaskId: string,
    output: Record<string, unknown>,
  ) {
    const inst = instances.value.get(instanceId)
    if (!inst) return
    const st = inst.subtasks.find(s => s.id === subtaskId)
    if (st) {
      st.output = output
      st.status = 'done'
    }
  }

  function markStale(instanceId: string, staleIds: string[]) {
    const inst = instances.value.get(instanceId)
    if (!inst) return
    for (const id of staleIds) {
      const st = inst.subtasks.find(s => s.id === id)
      if (st) st.status = 'stale'
    }
  }

  function clear() {
    instances.value.clear()
    activeInstanceId.value = null
  }

  return {
    instances,
    activeInstanceId,
    activeInstance,
    updateFromProgress,
    updateSubtaskOutput,
    markStale,
    clear,
  }
})
