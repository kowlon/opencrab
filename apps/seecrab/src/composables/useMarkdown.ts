import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: true, typographer: true })

export function useMarkdown() {
  function render(content: string): string {
    return md.render(content)
  }
  return { render }
}
