/**
 * 타입 정의가 없는 markdown-it 플러그인.
 *
 * `any` 로 두면 절대규칙 5(any 금지)에 걸리고, eslint 가 호출부마다
 * unsafe-argument 를 낸다. 여기서 실제 시그니처(PluginSimple)로 좁힌다.
 */
declare module 'markdown-it-task-lists' {
  import type { PluginWithOptions } from 'markdown-it'

  const plugin: PluginWithOptions<{ enabled?: boolean; label?: boolean }>
  export default plugin
}

declare module 'markdown-it-footnote' {
  import type { PluginSimple } from 'markdown-it'

  const plugin: PluginSimple
  export default plugin
}
