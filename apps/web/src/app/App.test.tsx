/**
 * IdP 가 돌려보낸 자리에서 **빠져나올 수 있는가.**
 *
 * `App` 은 단계보다 먼저 주소만 보고 콜백 화면을 고른다(`returning`). 그래서
 * 실패한 길에서 단계만 `anonymous` 로 내리고 주소를 콜백에 두면, 다시 렌더될
 * 때도 콜백 화면이 그려진다 — "로그인하는 중…" 에서 영원히 멈추고, 로그인
 * 단추를 눌러도 같은 자리로 돌아온다. 실제로 그랬다. 콜백 화면 혼자서는 이
 * 고장이 안 보이므로(단계는 맞게 바뀐다) **게이트와 함께** 본다.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '@/features/auth/store'

import { App } from './App'

// `vi.mock` 의 팩토리는 끌어올려지므로 위에서 만든 변수를 못 본다.
const { authApi } = vi.hoisted(() => ({
  authApi: {
    me: vi.fn(),
    completeSso: vi.fn(),
    completeSaml: vi.fn(),
    logout: vi.fn(),
  },
}))

vi.mock('@/shared/api', () => ({ authApi, deskApi: { myPortals: vi.fn() } }))
vi.mock('@/shared/i18n', () => ({ i18next: { language: 'en' }, setLocale: vi.fn() }))
// 문구가 아니라 **무엇을 말하는가**를 본다. 카탈로그를 고쳐도 안 깨진다.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
// 여기서 볼 것은 **어느 화면이 서는가**다. 각 화면의 속은 자기 시험이 본다.
vi.mock('./router', () => ({ router: {} }))
vi.mock('@tanstack/react-router', () => ({ RouterProvider: () => <p>app-shell</p> }))
vi.mock('@/features/auth/LoginScreen', () => ({
  LoginScreen: () => <p>login-screen</p>,
  AuthLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/features/auth/MfaScreen', () => ({ MfaScreen: () => <p>mfa-screen</p> }))
vi.mock('@/features/auth/MfaEnrollScreen', () => ({
  MfaEnrollScreen: () => <p>mfa-enroll-screen</p>,
}))
vi.mock('@/features/auth/AcceptInviteScreen', () => ({ AcceptInviteScreen: () => null }))
vi.mock('@/features/portal/PortalApp', () => ({ PortalApp: () => null }))
vi.mock('@/features/portal/SurveyScreen', () => ({ SurveyScreen: () => null }))

function arriveAt(url: string) {
  window.history.replaceState({}, '', url)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

const tokens = (over: { mfa_required?: boolean; mfa_enrollment_required?: boolean } = {}) => ({
  access_token: 'a',
  refresh_token: 'r',
  token_type: 'bearer',
  expires_in: 900,
  mfa_required: false,
  mfa_enrollment_required: false,
  ...over,
})

beforeEach(() => {
  useAuthStore.setState({ stage: 'unknown', user: null })
  authApi.me.mockRejectedValue(new Error('anonymous'))
})

afterEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/')
})

describe('App / SSO 콜백에서 나가기', () => {
  it('코드 없이 돌아오면 로그인 화면으로 간다', async () => {
    arriveAt('/auth/callback')

    expect(await screen.findByText('login-screen')).toBeInTheDocument()
    // 주소가 콜백에 남으면 다음 렌더가 다시 콜백 화면을 고른다.
    expect(window.location.pathname).toBe('/')
  })

  it('IdP 가 거절을 돌려보내면 그렇다고 말하고, 단추로 로그인 화면에 간다', async () => {
    arriveAt('/auth/callback?error=access_denied&error_description=call%20us&state=s')

    expect(await screen.findByText('auth:sso.failed')).toBeInTheDocument()
    // 주소창의 글은 누구나 지어 붙일 수 있다. 그대로 띄우지 않는다.
    expect(screen.queryByText(/call us/)).toBeNull()
    expect(window.location.search).toBe('')

    fireEvent.click(screen.getByRole('button', { name: 'auth:login.submit' }))

    expect(await screen.findByText('login-screen')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/')
    expect(authApi.completeSso).not.toHaveBeenCalled()
  })

  it('교환이 실패한 뒤 단추를 누르면 로그인 화면에 간다', async () => {
    authApi.completeSso.mockRejectedValue(new Error('exchange failed'))
    arriveAt('/auth/callback?code=c&state=s')

    const retry = await screen.findByRole('button', { name: 'auth:login.submit' })
    expect(authApi.completeSso).toHaveBeenCalledWith('c', 's')

    fireEvent.click(retry)

    expect(await screen.findByText('login-screen')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/')
  })

  it('SAML 교환이 실패해도 같은 길로 나간다', async () => {
    authApi.completeSaml.mockRejectedValue(new Error('exchange failed'))
    arriveAt('/auth/saml/callback?code=c')

    fireEvent.click(await screen.findByRole('button', { name: 'auth:login.submit' }))

    expect(await screen.findByText('login-screen')).toBeInTheDocument()
    expect(authApi.completeSaml).toHaveBeenCalledWith('c')
  })

  it('2FA 가 남았으면 콜백 화면이 아니라 2FA 화면이 선다', async () => {
    // 성공한 길도 같은 함정이 있다. 단계가 `mfa-required` 여도 주소가 콜백에
    // 남아 있으면 게이트는 콜백 화면을 고른다.
    authApi.completeSso.mockResolvedValue(tokens({ mfa_required: true }))
    arriveAt('/auth/callback?code=c&state=s')

    expect(await screen.findByText('mfa-screen')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/')
  })

  it('다 통과하면 앱이 선다', async () => {
    authApi.completeSso.mockResolvedValue(tokens())
    authApi.me.mockResolvedValue({ id: 'u1', is_customer: false, locale: 'en' })
    arriveAt('/auth/callback?code=c&state=s')

    expect(await screen.findByText('app-shell')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/')
  })
})
