import { ref, watch, nextTick, type Ref } from 'vue'

export function useAutoScroll(containerRef: Ref<HTMLElement | undefined>, trigger: Ref<any>) {
  const userScrolled = ref(false)

  function onScroll() {
    const el = containerRef.value
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50
    userScrolled.value = !atBottom
  }

  watch(trigger, async () => {
    if (userScrolled.value) return
    await nextTick()
    containerRef.value?.scrollTo({ top: containerRef.value.scrollHeight, behavior: 'smooth' })
  }, { deep: true })

  return { userScrolled, onScroll }
}
