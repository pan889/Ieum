/**
 * 붙여넣기의 순수 부분.
 *
 * 사람이 주소창에서 복사한 링크는 `http://our-host/issues/ENG-1` 이다. 그걸
 * 그대로 저장하면 호스트가 바뀌는 순간 전부 죽고, 내보낸 `.md` 는 그 서버가
 * 있어야만 읽힌다. 우리 주소는 스킴으로 바꿔 둔다 (wiki-markdown 5절).
 */

/** 앱 경로 → 내부 URI. 우리 주소가 아니면 null. */
export function internalUri(raw: string): string | null {
  const text = raw.trim()
  let path: string
  try {
    path = new URL(text).pathname
  } catch {
    // 절대 경로만 본다. `issues/ENG-1` 같은 상대 경로는 어디 기준인지 모른다.
    if (!text.startsWith('/')) return null
    path = text.split('?')[0] as string
  }

  const issue = /^\/issues\/([A-Za-z][A-Za-z0-9_]*-\d+)$/.exec(path)
  if (issue) return `issue:${(issue[1] as string).toUpperCase()}`

  const page = /^\/wiki\/([^/]+\/.+)$/.exec(path)
  if (page) {
    // 주소는 조각마다 인코딩돼 있다. 저장은 사람이 읽는 형태로 한다.
    const decoded = (page[1] as string)
      .split('/')
      .map((part) => {
        try {
          return decodeURIComponent(part)
        } catch {
          return part
        }
      })
      .join('/')
    return `page:${decoded}`
  }
  return null
}

/**
 * 내부 URI 를 사람이 읽는 이름으로.
 *
 * 주소를 그대로 링크 글자로 두면 문서에 `http://localhost:5173/issues/DEV-1`
 * 이 박힌다. 붙여넣은 사람이 뜻한 것은 그 이슈지 그 주소가 아니다.
 */
export function internalLabel(uri: string): string {
  if (uri.startsWith('issue:')) return uri.slice('issue:'.length)
  if (uri.startsWith('page:')) return uri.slice('page:'.length)
  return uri
}

/** 첨부 URI. 렌더할 때 presigned URL 로 바뀐다. */
export function attachmentUri(attachmentId: string, filename: string): string {
  return `attachment:${attachmentId}/${filename}`
}

/** 링크로 만들 만한 글자인가. 공백이 있으면 주소가 아니다. */
export function looksLikeUrl(text: string): boolean {
  const trimmed = text.trim()
  if (trimmed === '' || /\s/.test(trimmed)) return false
  return /^(https?:\/\/|mailto:)/i.test(trimmed) || trimmed.startsWith('/')
}
