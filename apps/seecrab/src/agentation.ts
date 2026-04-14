type AgentationGlobal = {
  mount?: (container: HTMLElement) => void
}

declare global {
  interface Window {
    Agentation?: AgentationGlobal
  }
}

// Optional integration: mount only when a runtime global is provided.
const maybeAgentation = window.Agentation
if (maybeAgentation?.mount) {
  const container = document.createElement('div')
  container.id = 'agentation-root'
  document.body.appendChild(container)
  maybeAgentation.mount(container)
}
