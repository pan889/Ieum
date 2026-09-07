/**
 * 조직 보안 정책 (auth.md 3절).
 *
 * 지금은 2FA 강제 하나뿐이다. IdP 스위치와 로컬 로그인 비활성화가 이 자리로
 * 온다.
 *
 * 정책은 **다음 로그인부터** 문다. 켜는 순간 모두가 튕겨 나가면 관리자
 * 자신도 스위치를 되돌릴 수 없다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { SettingsNav } from '@/features/settings/SettingsNav'
import { securityApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card } from '@/shared/ui/primitives'

export function SecurityScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()

  const policy = useQuery({
    queryKey: ['security', 'policy'],
    queryFn: () => securityApi.get(),
  })

  const save = useMutation({
    mutationFn: (required: boolean) => securityApi.setRequireMfa(required),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['security', 'policy'] }) },
  })

  const required = policy.data?.require_mfa ?? false

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header>
        <h1 className="text-xl font-semibold">{t('admin:security.title')}</h1>
        <p className="mt-1 text-sm text-muted">{t('admin:security.description')}</p>
      </header>

      {policy.isError ? <Alert>{describeError(policy.error)}</Alert> : null}
      {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}

      <Card className="flex items-center gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium">{t('admin:security.requireMfa')}</p>
          <p className="text-sm text-muted">{t('admin:security.requireMfaHint')}</p>
        </div>
        <Button
          className="ml-auto shrink-0"
          variant={required ? 'secondary' : 'primary'}
          loading={save.isPending}
          disabled={policy.isPending}
          onClick={() => { save.mutate(!required) }}
        >
          {required ? t('admin:security.turnOff') : t('admin:security.turnOn')}
        </Button>
      </Card>

      {required ? (
        // 이미 열려 있는 세션은 그대로 산다. 그게 왜 그런지 말해 주지 않으면
        // "안 켜졌나" 로 읽힌다.
        <p className="text-sm text-muted">{t('admin:security.appliesNextSignIn')}</p>
      ) : null}
    </section>
  )
}
