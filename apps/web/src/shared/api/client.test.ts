import { ApiError, ApiClient, tokenStore } from '@ieum/api-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/** fetch 를 가짜로 두고 클라이언트의 재시도·리프레시 동작만 본다. */
function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(requestUrl(input), init ?? {}),
  )
  vi.stubGlobal('fetch', spy)
  return spy
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const errorBody = (code: string, details: Record<string, unknown> = {}) => ({
  error: { code, message: 'server side message', details, trace_id: 'trace-1' },
})

describe('ApiClient', () => {
  beforeEach(() => {
    tokenStore.clear()
    tokenStore.set({ accessToken: 'access-1', refreshToken: 'refresh-1' })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    tokenStore.clear()
  })

  it('액세스 토큰을 Authorization 헤더에 붙인다', async () => {
    const spy = mockFetch(() => json(200, { ok: true }))
    const client = new ApiClient()
    await client.get('/api/v1/auth/me')

    const headers = spy.mock.calls[0]?.[1]?.headers as Record<string, string>
    expect(headers['Authorization']).toBe('Bearer access-1')
  })

  it('204 는 본문 없이 성공으로 처리한다', async () => {
    mockFetch(() => new Response(null, { status: 204 }))
    await expect(new ApiClient().post('/api/v1/auth/logout')).resolves.toBeUndefined()
  })

  it('에러 응답을 ApiError 로 정규화하고 code·details·trace_id 를 보존한다', async () => {
    mockFetch(() => json(422, errorBody('identity.password_too_short', { min_length: 12 })))

    await expect(new ApiClient().post('/api/v1/users/me/password')).rejects.toMatchObject({
      code: 'identity.password_too_short',
      status: 422,
      details: { min_length: 12 },
      traceId: 'trace-1',
    })
  })

  it('401 이면 리프레시 후 원 요청을 한 번 재시도한다', async () => {
    let refreshed = false
    const spy = mockFetch((url) => {
      if (url.endsWith('/auth/refresh')) {
        refreshed = true
        return json(200, { access_token: 'access-2', refresh_token: 'refresh-2' })
      }
      return refreshed ? json(200, { ok: true }) : json(401, errorBody('auth.unauthenticated'))
    })

    await expect(new ApiClient().get('/api/v1/auth/me')).resolves.toEqual({ ok: true })
    expect(spy).toHaveBeenCalledTimes(3) // 최초 401 → refresh → 재시도
    expect(tokenStore.access).toBe('access-2')
  })

  it('동시에 401 이 몰려도 리프레시는 한 번만 나간다', async () => {
    /**
     * 이게 깨지면 로테이션된 토큰을 서로 덮어쓰다가 서버가 재사용으로 판단해
     * 세션 계열을 통째로 폐기한다 — 탭을 여러 개 띄운 사용자가 갑자기
     * 로그아웃되는 증상으로 나타난다.
     */
    let refreshCount = 0
    let refreshed = false
    mockFetch(async (url) => {
      if (url.endsWith('/auth/refresh')) {
        refreshCount += 1
        await new Promise((r) => setTimeout(r, 10))
        refreshed = true
        return json(200, { access_token: 'access-2', refresh_token: 'refresh-2' })
      }
      return refreshed ? json(200, { ok: true }) : json(401, errorBody('auth.unauthenticated'))
    })

    const client = new ApiClient()
    await Promise.all([
      client.get('/api/v1/a'),
      client.get('/api/v1/b'),
      client.get('/api/v1/c'),
    ])

    expect(refreshCount).toBe(1)
  })

  it('리프레시까지 실패하면 토큰을 비우고 onSessionLost 를 부른다', async () => {
    mockFetch((url) =>
      url.endsWith('/auth/refresh')
        ? json(401, errorBody('auth.unauthenticated'))
        : json(401, errorBody('auth.unauthenticated')),
    )
    const onSessionLost = vi.fn()

    await expect(
      new ApiClient({ onSessionLost }).get('/api/v1/auth/me'),
    ).rejects.toBeInstanceOf(ApiError)
    expect(onSessionLost).toHaveBeenCalledOnce()
    expect(tokenStore.access).toBeNull()
  })

  it('로그인·리프레시(anonymous)는 401 이어도 리프레시를 시도하지 않는다', async () => {
    const spy = mockFetch(() => json(401, errorBody('auth.unauthenticated')))

    await expect(
      new ApiClient().post('/api/v1/auth/login', { email: 'a@b.c' }, { anonymous: true }),
    ).rejects.toBeInstanceOf(ApiError)
    expect(spy).toHaveBeenCalledOnce()
  })
})
