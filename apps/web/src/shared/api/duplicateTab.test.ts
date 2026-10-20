/**
 * 복제된 탭의 클라이언트가 **물려받은 리프레시 토큰을 서버에 내밀지 않는가.**
 *
 * `claimTab` 이 옳게 갈라도, 클라이언트가 그 판단을 기다리지 않고 토큰을 먼저
 * 읽으면 소용이 없다 — 부팅 때 `me` 의 401 이 곧바로 리프레시를 깨운다. 여기서는
 * 진짜 `BroadcastChannel` 하나를 원본 탭으로 세워 두고, 이 파일의 클라이언트를
 * 복제된 탭으로 띄운다.
 */

import { ApiClient, tokenStore } from '@ieum/api-client'
import { afterEach, describe, expect, it, vi } from 'vitest'

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

const original = new BroadcastChannel('ieum.tabs')

afterEach(() => {
  vi.unstubAllGlobals()
  original.close()
})

describe('복제된 탭', () => {
  it('물려받은 토큰으로 리프레시하지 않고 로그인부터 다시 하게 한다', async () => {
    // 원본 탭이 `tab-original` 이라는 이름으로 살아 있다.
    original.onmessage = (event: MessageEvent<{ type: string; tab: string; from: string }>) => {
      const message = event.data
      if (message.type === 'claim' && message.tab === 'tab-original') {
        original.postMessage({ type: 'taken', tab: message.tab, to: message.from })
      }
    }
    // 복제가 베낀 sessionStorage 다.
    sessionStorage.setItem('ieum.tab', 'tab-original')
    sessionStorage.setItem('ieum.refresh', 'refresh-of-original')

    const fetch = vi.fn((input: RequestInfo | URL) =>
      Promise.resolve(
        requestUrl(input).endsWith('/auth/refresh')
          ? new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            })
          : new Response(
              JSON.stringify({
                error: { code: 'auth.unauthenticated', message: 'x', details: {}, trace_id: 't' },
              }),
              { status: 401, headers: { 'Content-Type': 'application/json' } },
            ),
      ),
    )
    vi.stubGlobal('fetch', fetch)
    const lost = vi.fn()

    const client = new ApiClient({ onSessionLost: lost })
    await expect(client.get('/api/v1/auth/me')).rejects.toMatchObject({ status: 401 })

    // 원본의 토큰을 내밀었으면 서버가 두 탭의 세션 계열을 함께 폐기했다.
    expect(fetch.mock.calls.map(([input]) => requestUrl(input))).toEqual(['/api/v1/auth/me'])
    expect(lost).toHaveBeenCalledOnce()
    expect(tokenStore.refresh).toBeNull()
    expect(sessionStorage.getItem('ieum.tab')).not.toBe('tab-original')
  })
})
