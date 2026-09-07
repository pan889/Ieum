/**
 * 내 2단계 인증 (auth.md 3절).
 *
 * 인증기를 **여럿 두는 것이 정상이다** — 노트북과 폰과 보안 키. 하나만 다루면
 * 기기를 잃은 사람이 잠긴다.
 *
 * 패스키와 "이 기기에만 있는 키" 를 갈라 보여 준다. 잃었을 때 결과가 다르다:
 * 패스키는 계정에 남고, 기기 키는 기기와 함께 사라진다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { MfaCredential } from '@ieum/api-client'

import { createCredential, isSupported } from '@/features/auth/webauthn'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card } from '@/shared/ui/primitives'

function describeCredential(credential: MfaCredential, t: (key: string) => string): string {
  if (credential.kind !== 'webauthn') return t('auth:passkeys.kind.totp')
  return credential.webauthn_backed_up
    ? t('auth:passkeys.syncedHint')
    : t('auth:passkeys.deviceOnlyHint')
}

export function PasskeysScreen() {
  const { t, i18n } = useTranslation(['auth', 'common'])
  const queryClient = useQueryClient()

  const credentials = useQuery({
    queryKey: ['mfa', 'credentials'],
    queryFn: () => authApi.mfaCredentials(),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['mfa', 'credentials'] })

  const register = useMutation({
    mutationFn: async () => {
      const { options } = await authApi.startPasskeyRegistration()
      const response = await createCredential(options)
      // 사람이 취소했다. 오류가 아니므로 조용히 끝낸다 — 빨간 경고를 띄우면
      // 자기가 누른 취소 때문에 무언가 망가졌다고 읽는다.
      if (response === null) return null
      return authApi.finishPasskeyRegistration(response)
    },
    onSuccess: async () => { await refresh() },
  })

  const remove = useMutation({
    mutationFn: (id: string) => authApi.removeMfaCredential(id),
    onSuccess: async () => { await refresh() },
  })

  const rows = credentials.data ?? []
  const supported = isSupported()

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('auth:passkeys.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('auth:passkeys.description')}</p>
        </div>
        <Button
          className="ml-auto shrink-0"
          disabled={!supported}
          loading={register.isPending}
          onClick={() => { register.mutate() }}
        >
          {register.isPending ? t('auth:passkeys.adding') : t('auth:passkeys.add')}
        </Button>
      </header>

      {/* 쓸 수 없는 브라우저에서 버튼만 죽여 두면 왜 안 되는지 알 수 없다. */}
      {!supported ? <Alert>{t('auth:passkeys.unsupported')}</Alert> : null}
      {credentials.isError ? <Alert>{describeError(credentials.error)}</Alert> : null}
      {register.isError ? <Alert>{describeError(register.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {/* 마지막 하나를 떼면 다음 로그인에서 다시 등록하게 된다. 막지는
          않는다(등록할 길이 있으므로 잠기지 않는다) — 대신 말해 준다. */}
      {rows.length === 1 ? <Alert>{t('auth:passkeys.removeLast')}</Alert> : null}

      {rows.length === 0 && !credentials.isPending ? (
        <p className="text-sm text-muted">{t('auth:passkeys.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label={t('auth:passkeys.title')}>
          {rows.map((credential) => (
            <li key={credential.id}>
              <Card className="flex items-center gap-3">
                <div className="flex min-w-0 flex-col gap-0.5">
                  <p className="text-sm font-medium">
                    {credential.label ?? t(`auth:passkeys.kind.${credential.kind}`)}
                  </p>
                  <p className="text-xs text-muted">{describeCredential(credential, t)}</p>
                  <p className="text-xs text-muted">
                    {credential.last_used_at
                      ? t('auth:passkeys.lastUsed', {
                          when: new Date(credential.last_used_at).toLocaleString(i18n.language),
                        })
                      : t('auth:passkeys.neverUsed')}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  className="ml-auto shrink-0 text-xs"
                  // 이름이 없으면 종류로 부른다. 화면에 보이는 이름과 같아야
                  // 한다 — `webauthn` 같은 내부 값을 읽어 주면 안 된다.
                  aria-label={`${t('auth:passkeys.remove')} ${
                    credential.label ?? t(`auth:passkeys.kind.${credential.kind}`)
                  }`}
                  loading={remove.isPending}
                  onClick={() => { remove.mutate(credential.id) }}
                >
                  {t('auth:passkeys.remove')}
                </Button>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
