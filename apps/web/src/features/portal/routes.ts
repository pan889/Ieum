/**
 * 포털 경로. 세 화면뿐이라 라우터 라이브러리를 하나 더 붙이지 않는다.
 *
 * 주소가 **공유 가능해야** 한다는 것이 이 파일의 이유다. 상태를 컴포넌트에만
 * 두면 폼 링크를 붙여 줄 수 없고, 뒤로 가기가 포털 밖으로 나간다.
 */

const PREFIX = '/portal/'
/** 마지막으로 본 창구. 뿌리로 들어온 고객을 어디로 보낼지 정하는 데 쓴다. */
const LAST_PORTAL_KEY = 'ieum.portal.last'

export type PortalRoute =
  | { kind: 'home'; slug: string }
  | { kind: 'form'; slug: string; requestTypeId: string }
  | { kind: 'ticket'; slug: string; issueId: string }

export function parsePortalPath(pathname: string): PortalRoute | null {
  if (!pathname.startsWith(PREFIX)) return null
  const [slug, section, id] = pathname.slice(PREFIX.length).split('/')
  if (!slug) return null
  if (section === 'new' && id) return { kind: 'form', slug, requestTypeId: id }
  if (section === 'requests' && id) return { kind: 'ticket', slug, issueId: id }
  return { kind: 'home', slug }
}

export function pathFor(route: PortalRoute): string {
  switch (route.kind) {
    case 'home':
      return `${PREFIX}${route.slug}`
    case 'form':
      return `${PREFIX}${route.slug}/new/${route.requestTypeId}`
    case 'ticket':
      return `${PREFIX}${route.slug}/requests/${route.issueId}`
  }
}

export function navigate(route: PortalRoute): void {
  history.pushState(null, '', pathFor(route))
}

/**
 * 브라우저 저장소는 던질 수 있다(사생활 보호 창, 저장 차단). 기억하지 못하는
 * 것은 불편일 뿐이므로 조용히 넘긴다 — 여기서 예외가 새면 화면이 죽는다.
 */
export function rememberPortal(slug: string): void {
  try {
    localStorage.setItem(LAST_PORTAL_KEY, slug)
  } catch {
    /* 기억하지 못해도 포털은 돈다 */
  }
}

export function lastPortal(): string | null {
  try {
    return localStorage.getItem(LAST_PORTAL_KEY)
  } catch {
    return null
  }
}
