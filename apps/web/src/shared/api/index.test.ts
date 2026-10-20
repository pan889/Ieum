/**
 * 세션을 잃었을 때의 뒷정리.
 *
 * 리프레시까지 실패하면 단계를 내리는 것만으로는 모자란다 — 캐시에 앞사람의
 * 데이터와 `me` 가 남아, 다음에 로그인한 사람의 앱이 앞사람으로 선다.
 * 로그아웃 단추는 처음부터 캐시를 버렸고, 이 길만 빠져 있었다.
 */

import { tokenStore } from '@ieum/api-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '@/features/auth/store'

import { apiClient } from './index'
import { queryClient } from './queryClient'

const unauthenticated = () =>
  new Response(
    JSON.stringify({
      error: { code: 'auth.unauthenticated', message: 'x', details: {}, trace_id: 't' },
    }),
    { status: 401, headers: { 'Content-Type': 'application/json' } },
  )

beforeEach(() => {
  // 리프레시 토큰이 없으니 리프레시는 바로 실패한다 — "세션을 잃었다" 의 길이다.
  tokenStore.clear()
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(unauthenticated())))
  queryClient.setQueryData(['issues', 'list'], ['앞사람의 이슈'])
})

afterEach(() => {
  vi.unstubAllGlobals()
  queryClient.clear()
  useAuthStore.setState({ stage: 'unknown', user: null })
})

describe('onSessionLost', () => {
  it('로그인해 있던 세션을 잃으면 단계를 내리고 캐시를 버린다', async () => {
    useAuthStore.setState({ stage: 'authenticated' })

    await expect(apiClient.get('/api/v1/issues')).rejects.toMatchObject({ status: 401 })

    expect(useAuthStore.getState().stage).toBe('anonymous')
    expect(queryClient.getQueryData(['issues', 'list'])).toBeUndefined()
  })

  it('2FA 를 기다리던 세션도 같다', async () => {
    useAuthStore.setState({ stage: 'mfa-required' })

    await expect(apiClient.get('/api/v1/auth/me')).rejects.toMatchObject({ status: 401 })

    expect(queryClient.getQueryData(['issues', 'list'])).toBeUndefined()
  })

  it('로그인 전의 첫 401 에는 캐시를 건드리지 않는다', async () => {
    // 부팅 때 `me` 가 받는 401 이다. 버릴 남의 데이터가 없고, 버리면 함께
    // 날아가던 조회가 소리 없이 취소된다.
    useAuthStore.setState({ stage: 'unknown' })

    await expect(apiClient.get('/api/v1/auth/me')).rejects.toMatchObject({ status: 401 })

    expect(useAuthStore.getState().stage).toBe('anonymous')
    expect(queryClient.getQueryData(['issues', 'list'])).toEqual(['앞사람의 이슈'])
  })
})
