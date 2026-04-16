<template>
  <div class="step-card-list">
    <template v-for="item in renderItems" :key="item.key">
      <ThinkingBlock
        v-if="item.type === 'thinking'"
        :content="item.thinkingContent!"
        :done="item.thinkingDone!"
      />
      <StepCard v-else-if="item.type === 'card'" :card="item.card!" />
      <AgentSummaryBlock
        v-else-if="item.type === 'summary'"
        :agent-id="item.agentId!"
        :summary="item.summary!"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { StepCard as StepCardType } from '@/types'
import StepCard from './StepCard.vue'
import AgentSummaryBlock from './AgentSummaryBlock.vue'
import ThinkingBlock from './ThinkingBlock.vue'

interface RenderItem {
  type: 'card' | 'summary' | 'thinking'
  key: string
  card?: StepCardType
  agentId?: string
  summary?: string
  thinkingContent?: string
  thinkingDone?: boolean
}

const props = defineProps<{
  cards: StepCardType[]
  agentSummaries?: Record<string, string>
  agentThinking?: Record<string, { content: string; done: boolean }>
}>()

function orderCardsByDelegation(cards: StepCardType[]): StepCardType[] {
  const byStepId = new Map(cards.map(card => [card.stepId, card]))
  const children = new Map<string, StepCardType[]>()
  const roots: StepCardType[] = []
  let linkedCount = 0
  let orphanCount = 0

  for (const card of cards) {
    const parentId = card.parentStepId
    if (parentId && byStepId.has(parentId)) {
      if (!children.has(parentId)) children.set(parentId, [])
      children.get(parentId)!.push(card)
      linkedCount += 1
    } else {
      roots.push(card)
      if (parentId) orphanCount += 1
    }
  }

  if (linkedCount > 0 || orphanCount > 0) {
    console.log(
      '[SeeCrab][StepCardList] delegation ordering:',
      'cards=',
      cards.length,
      'linked=',
      linkedCount,
      'orphans=',
      orphanCount,
    )
  }

  const ordered: StepCardType[] = []
  const visited = new Set<string>()
  const appendTree = (card: StepCardType) => {
    if (visited.has(card.stepId)) return
    visited.add(card.stepId)
    ordered.push(card)
    for (const child of children.get(card.stepId) ?? []) {
      appendTree(child)
    }
  }

  for (const root of roots) appendTree(root)
  return ordered
}

const renderItems = computed<RenderItem[]>(() => {
  const items: RenderItem[] = []
  const summaries = props.agentSummaries ?? {}
  const thinking = props.agentThinking ?? {}
  const emittedThinking = new Set<string>()
  const orderedCards = orderCardsByDelegation(props.cards)

  for (let i = 0; i < orderedCards.length; i++) {
    const card = orderedCards[i]

    // Insert thinking block before delegate card using subtaskId (fallback delegateAgentId)
    if (card.cardType === 'delegate' && (card.subtaskId || card.delegateAgentId)) {
      const dedupKey = card.stepId  // unique per subtask delegation
      if (!emittedThinking.has(dedupKey)) {
        // Try subtaskId first, then delegateAgentId as fallback
        const at = (card.subtaskId && thinking[card.subtaskId])
          || (card.delegateAgentId && thinking[card.delegateAgentId])
          || null
        const thinkingKey = card.subtaskId || card.delegateAgentId!
        if (at && at.content) {
          emittedThinking.add(dedupKey)
          items.push({
            type: 'thinking',
            key: `thinking_${thinkingKey}_${i}`,
            thinkingContent: at.content,
            thinkingDone: at.done,
          })
        }
      }
    }

    // For non-BP sub-agents (no preceding delegate card), insert thinking before first card of group
    if (card.agentId && card.agentId !== 'main' && !emittedThinking.has(card.agentId)) {
      const prev = orderedCards[i - 1]
      if (!prev || (prev.agentId !== card.agentId && prev.cardType !== 'delegate')) {
        const at = thinking[card.agentId]
        if (at && at.content) {
          emittedThinking.add(card.agentId)
          items.push({
            type: 'thinking',
            key: `thinking_${card.agentId}_${i}`,
            thinkingContent: at.content,
            thinkingDone: at.done,
          })
        }
      }
    }

    items.push({ type: 'card', key: card.stepId, card })

    // Detect end of sub-agent group: current card is sub-agent,
    // and next card is different agent or end of list
    if (card.agentId && card.agentId !== 'main') {
      const next = orderedCards[i + 1]
      if (!next || next.agentId !== card.agentId) {
        const text = summaries[card.agentId]
        if (text) {
          items.push({
            type: 'summary',
            key: `summary_${card.agentId}_${i}`,
            agentId: card.agentId,
            summary: text,
          })
        }
      }
    }
  }
  return items
})
</script>

<style scoped>
.step-card-list { margin: 8px 0; }
</style>
