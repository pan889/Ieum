import { useMemo } from 'react'

import { renderMarkdown } from './dialect'

/**
 * 마크다운 본문.
 *
 * `dangerouslySetInnerHTML` 을 쓴다. 안전한 이유는 방언이 원시 HTML 을 끄고
 * (`html: false`) 링크 스킴을 화이트리스트로 막기 때문이다 — 그 두 줄이
 * 무너지면 여기가 바로 XSS 가 된다. 새니타이저를 추가로 두지 않는 것도
 * 같은 이유다: 있으면 "그럼 html 을 켜도 되겠네" 로 이어진다.
 */
export function Markdown({ source, className }: { source: string; className?: string }) {
  const html = useMemo(() => renderMarkdown(source), [source])
  return (
    <div
      className={className ? `ieum-markdown ${className}` : 'ieum-markdown'}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
