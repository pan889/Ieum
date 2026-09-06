import { useRouter } from '@tanstack/react-router'
import { useMemo } from 'react'

import { renderMarkdown } from './dialect'

/**
 * 이미 만들어 둔 마크다운 HTML.
 *
 * `dangerouslySetInnerHTML` 을 쓴다. 안전한 이유는 방언이 원시 HTML 을 끄고
 * (`html: false`) 링크 스킴을 화이트리스트로 막기 때문이다 — 그 두 줄이
 * 무너지면 여기가 바로 XSS 가 된다. 새니타이저를 추가로 두지 않는 것도
 * 같은 이유다: 있으면 "그럼 html 을 켜도 되겠네" 로 이어진다.
 *
 * HTML 을 밖에서 받는 형태가 따로 있는 이유는 `RichText` 때문이다. 한 문서를
 * 여러 조각으로 나눠 그리면서 제목 번호를 공유해야 해서, 렌더는 거기서 한 번에
 * 한다.
 */
export function MarkdownHtml({
  html,
  className,
}: {
  html: string
  className?: string | undefined
}) {
  const router = useRouter()

  return (
    <div
      className={className ? `ieum-markdown ${className}` : 'ieum-markdown'}
      dangerouslySetInnerHTML={{ __html: html }}
      // `issue:`·`page:` 링크는 렌더 시 앱 주소로 바뀐다. 그냥 두면 전체
      // 새로고침이 나므로 클릭을 가로채 라우터로 넘긴다. 위임으로 잡는다 —
      // innerHTML 로 그린 앵커에는 리스너를 하나씩 못 붙인다.
      onClick={(event) => {
        if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.button !== 0) return
        const anchor = (event.target as HTMLElement).closest('a[data-internal]')
        if (!(anchor instanceof HTMLAnchorElement)) return
        event.preventDefault()
        void router.navigate({ to: anchor.getAttribute('href') ?? '/' })
      }}
    />
  )
}

/** 마크다운 본문. 디렉티브까지 그리려면 `RichText` 를 쓴다. */
export function Markdown({
  source,
  className,
}: {
  source: string
  className?: string | undefined
}) {
  const html = useMemo(() => renderMarkdown(source), [source])
  return <MarkdownHtml html={html} className={className} />
}
