/**
 * 본문의 `attachment:<id>/<파일명>` 을 실제로 열리는 주소로 바꾼다.
 *
 * 저장은 스킴으로 한다 — 서명된 주소를 본문에 박으면 몇 분 뒤 죽고, 내보낸
 * `.md` 는 그 서버에 묶인다. 대신 **그릴 때** 서명된 주소를 받아 끼운다.
 *
 * `<img src>` 에 우리 API 경로를 박을 수는 없다. 액세스 토큰은 메모리에만
 * 있고 쿠키가 아니라서 브라우저가 이미지를 받으러 갈 때 Authorization 헤더를
 * 안 붙인다 — 401 이 돌아오고 그림이 안 뜬다. 서명된 주소는 그 자체가
 * 신분증이라 헤더가 필요 없다.
 */

import { useQueries } from '@tanstack/react-query'
import { useMemo } from 'react'

import { attachmentsApi } from '@/shared/api'

export const SCHEME = 'attachment:'

/** 렌더된 HTML 안의 `attachment:` 참조. UUID 만 인정한다. */
const REFERENCE = /attachment:([0-9a-fA-F-]{36})(\/[^"'\s)]*)?/g

/** 서명된 주소는 곧 만료된다. 그보다 짧게 잡아 다시 받는다. */
const FRESH_MS = 4 * 60 * 1000

/** 본문이 가리키는 첨부 id. 등장 순서대로, 중복 없이. */
export function attachmentIds(html: string): string[] {
  const found: string[] = []
  for (const match of html.matchAll(REFERENCE)) {
    const id = (match[1] as string).toLowerCase()
    if (!found.includes(id)) found.push(id)
  }
  return found
}

/** 받은 주소로 갈아 끼운다. 못 받은 것은 그대로 둔다 — 빈 그림보다 낫다. */
export function withAttachmentUrls(html: string, urls: ReadonlyMap<string, string>): string {
  if (urls.size === 0) return html
  return html.replace(REFERENCE, (whole, id: string) => urls.get(id.toLowerCase()) ?? whole)
}

/**
 * 본문 안의 첨부 주소를 받아 끼운 HTML.
 *
 * 첨부가 없으면 아무 요청도 안 나간다 — 대부분의 문서가 그렇다.
 */
export function useAttachmentUrls(html: string): string {
  const ids = useMemo(() => attachmentIds(html), [html])

  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['attachment', 'url', id],
      queryFn: () => attachmentsApi.downloadUrl(id),
      staleTime: FRESH_MS,
      // 지워진 첨부를 계속 다시 물어보지 않는다.
      retry: false,
    })),
  })

  const urls = useMemo(() => {
    const map = new Map<string, string>()
    ids.forEach((id, index) => {
      const url = results[index]?.data?.url
      if (url) map.set(id, url)
    })
    return map
  }, [ids, results])

  return useMemo(() => withAttachmentUrls(html, urls), [html, urls])
}
