<script setup lang="ts">
import { reactive, ref } from 'vue'

const props = defineProps<{
  askUser: {
    instanceId: string
    subtaskId: string
    subtaskName: string
    missingFields: string[]
    inputSchema?: Record<string, unknown>
  }
}>()

const emit = defineEmits<{
  submit: [data: Record<string, unknown>]
}>()

type FormFieldValue = string | number | null

const formData = reactive<Record<string, FormFieldValue>>({})
const submitting = ref(false)

// Initialize form data from missing fields
for (const field of props.askUser.missingFields) {
  formData[field] = ''
}

function getFieldMeta(field: string) {
  const schema = props.askUser.inputSchema as Record<string, unknown> | undefined
  const properties = (schema?.properties ?? {}) as Record<string, Record<string, unknown>>
  return properties[field] ?? {}
}

function getFieldType(field: string): string {
  return (getFieldMeta(field).type as string) ?? 'string'
}

function getFieldLabel(field: string): string {
  return (getFieldMeta(field).description as string) ?? field
}

function handleSubmit() {
  submitting.value = true
  const normalized: Record<string, unknown> = {}
  for (const field of props.askUser.missingFields) {
    const raw = formData[field]
    if (getFieldType(field) === 'boolean') {
      normalized[field] = raw === 'true' ? true : raw === 'false' ? false : null
    } else {
      normalized[field] = raw
    }
  }
  emit('submit', normalized)
}
</script>

<template>
  <div class="bp-ask-user-block">
    <div class="header">
      <span class="material-symbols-rounded header-icon">edit_note</span>
      <span class="header-text">子任务「{{ askUser.subtaskName }}」需要补充以下信息：</span>
    </div>
    <form class="form" @submit.prevent="handleSubmit">
      <div v-for="field in askUser.missingFields" :key="field" class="field">
        <label :for="'bp-field-' + field">{{ getFieldLabel(field) }}</label>
        <textarea
          v-if="getFieldType(field) === 'object' || getFieldType(field) === 'array'"
          :id="'bp-field-' + field"
          v-model="formData[field]"
          rows="3"
          placeholder="输入 JSON..."
        />
        <input
          v-else-if="getFieldType(field) === 'number'"
          :id="'bp-field-' + field"
          v-model.number="formData[field]"
          type="number"
        />
        <select
          v-else-if="getFieldType(field) === 'boolean'"
          :id="'bp-field-' + field"
          v-model="formData[field]"
        >
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
        <input
          v-else
          :id="'bp-field-' + field"
          v-model="formData[field]"
          type="text"
          :placeholder="getFieldLabel(field)"
        />
      </div>
      <button type="submit" class="submit-btn" :disabled="submitting">
        提交
      </button>
    </form>
  </div>
</template>

<style scoped>
.bp-ask-user-block {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-left: 3px solid var(--warning);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  margin: 10px 0;
  animation: fadeIn 0.35s var(--ease-out) both;
}
.header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.header-icon {
  font-size: 18px;
  color: var(--warning);
  flex-shrink: 0;
}
.header-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-bright);
}
.form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-left: 26px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field label {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}
.field input,
.field textarea,
.field select {
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-primary);
  font-size: 14px;
  font-family: inherit;
  transition: border-color 0.15s var(--ease-out);
}
.field input:focus,
.field textarea:focus,
.field select:focus {
  outline: none;
  border-color: var(--accent);
}
.submit-btn {
  padding: 7px 18px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--accent);
  background: var(--accent-dim);
  color: var(--accent);
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  font-weight: 500;
  align-self: flex-start;
  transition: all 0.15s var(--ease-out);
}
.submit-btn:hover:not(:disabled) {
  background: var(--accent-glow);
}
.submit-btn:disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}
</style>
