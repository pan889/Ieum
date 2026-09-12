/**
 * 붙여넣기의 순수 부분.
 *
 * 사람이 주소창에서 복사한 링크는 `http://our-host/issues/ENG-1` 이다. 그걸
 * 그대로 저장하면 호스트가 바뀌는 순간 전부 죽고, 내보낸 `.md` 는 그 서버가
 * 있어야만 읽힌다. 우리 주소는 스킴으로 바꿔 둔다 (wiki-markdown 5절).
 */

/**
 * 앱 경로 → 내부 URI. 우리 주소가 아니면 null.
 *
 * `origin` 은 **우리 주소**다. 넘기지 않으면 지금 열려 있는 창의 것을 쓴다
 * (시험에서 DOM 없이 부를 수 있게 인자로 열어 둔다).
 */
export function internalUri(raw: string, origin: string = currentOrigin()): string | null {
  const text = raw.trim()
  let path: string
  try {
    const url = new URL(text)
    // **호스트를 본다.** 경로만 보면 남의 트래커 링크가 우리 것으로 바뀐다:
    // `https://redmine.partner.example/issues/ENG-1` 을 붙여넣으면 경로가
    // `/issues/ENG-1` 이라 `issue:ENG-1` 로 저장되고, 그건 **우리 쪽 ENG-1**
    // 을 가리킨다. 붙여넣은 사람은 저쪽을 가리켰고, 링크는 멀쩡해 보이며,
    // 원래 주소는 이미 없다.
    if (url.origin !== origin) return null
    path = url.pathname
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

function currentOrigin(): string {
  // 창이 없으면(서버 렌더·시험) 어떤 절대 주소도 우리 것이 아니다.
  return typeof window === 'undefined' ? '\u0000없음' : window.location.origin
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

/**
 * 첨부 URI. 렌더할 때 presigned URL 로 바뀐다.
 *
 * **파일 이름은 그대로 넣을 수 없다.** 이 글자는 마크다운 링크 목적지 안에
 * 들어가는데, 공백이나 괄호가 나오면 목적지가 그 자리에서 끝난다 —
 * `[그림](attachment:<id>/보고서 최종.png)` 은 `…/보고서` 까지만 링크이고
 * 나머지는 그냥 글자로 남는다. 붙여넣은 사람은 그림이 들어간 줄 안다.
 *
 * 그래서 **깨뜨리는 글자만** 감싼다. 전부 감싸면(`encodeURIComponent`) 한글
 * 이름이 `%EB%B3%B4…` 가 되어 원문을 사람이 못 읽는다. 서버 쪽 짝은
 * `wiki/portable.py` 의 `encode_target` 이고, 되돌리는 것은 `unquote` 다.
 */
export function attachmentUri(attachmentId: string, filename: string): string {
  return `attachment:${attachmentId}/${encodeTarget(filename)}`
}

//: 링크 목적지 안에서 자리를 끝내 버리는 글자들. `portable.py` 와 같은 표다.
const UNSAFE_IN_TARGET: Record<string, string> = {
  ' ': '%20',
  '(': '%28',
  ')': '%29',
  '<': '%3C',
  '>': '%3E',
}

function encodeTarget(name: string): string {
  // `%` 를 먼저 바꾼다. 안 그러면 파일명에 있던 `%20` 이 되돌릴 때 공백이
  // 되어 **다른 파일**을 가리킨다.
  let out = name.replace(/%/g, '%25')
  for (const [char, escaped] of Object.entries(UNSAFE_IN_TARGET)) {
    out = out.split(char).join(escaped)
  }
  return out
}

/** 링크로 만들 만한 글자인가. 공백이 있으면 주소가 아니다. */
export function looksLikeUrl(text: string): boolean {
  const trimmed = text.trim()
  if (trimmed === '' || /\s/.test(trimmed)) return false
  return /^(https?:\/\/|mailto:)/i.test(trimmed) || trimmed.startsWith('/')
}
