/**
 * 초대 수락.
 *
 * 아직 로그인할 수 없는 사람이 여는 화면이다. 그래서 앱 셸 밖에 있다 —
 * 라우터는 로그인한 뒤에만 올라온다(App.tsx).
 *
 * 토큰은 주소에 담겨 온다. 서버가 암호화해 만든 값이라 위조할 수 없고,
 * 여기서는 뜯어보지 않고 그대로 돌려준다.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { usersApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

import { AuthLayout } from './LoginScreen'

/** 주소에서 초대 토큰을 꺼낸다. 없으면 null. */
export function inviteToken(search: string): string | null {
  const value = new URLSearchParams(search).get('token')
  return value && value.trim() !== '' ? value : null
}

export function AcceptInviteScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const token = inviteToken(window.location.search)
  const [password, setPassword] = useState('')

  const accept = useMutation({
    mutationFn: () => usersApi.acceptInvite({ token: token as string, password }),
  })

  if (token === null) {
    return (
      <AuthLayout title={t('auth:invite.title')}>
        <Alert>{t('auth:invite.missingToken')}</Alert>
      </AuthLayout>
    )
  }

  if (accept.isSuccess) {
    return (
      <AuthLayout title={t('auth:invite.title')}>
        <p className="text-sm text-fg">{t('auth:invite.done')}</p>
        {/* 로그인 화면은 앱의 뿌리다. 라우터가 없으므로 주소로 옮긴다. */}
        <Button
          className="mt-4 w-full"
          onClick={() => { window.location.href = '/' }}
        >
          {t('auth:login.submit')}
        </Button>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title={t('auth:invite.title')} subtitle={t('auth:invite.subtitle')}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          accept.mutate()
        }}
      >
        {accept.isError ? <Alert>{describeError(accept.error)}</Alert> : null}

        <Field
          label={t('auth:invite.password')}
          type="password"
          name="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => { setPassword(e.target.value) }}
        />

        <Button type="submit" loading={accept.isPending}>
          {t('auth:invite.submit')}
        </Button>
      </form>
    </AuthLayout>
  )
}
