/**
 * 내 로그인 기기 (auth.md 1절).
 *
 * 세션은 서버가 들고 있으므로 끊으면 **즉시** 죽는다 — JWT 를 세션으로 쓰지
 * 않는 이유가 이것이다. 그 성질이 사용자에게 보이는 자리가 여기다.
 *
 * 하나만 끊는 길과 전부 끊는 길을 나란히 둔다. 전부 끊기만 있으면 기기 하나를
 * 잃었을 때 지금 쓰는 자리에서도 튕겨 나간다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { SessionInfo } from '@ieum/api-client'

import { formatDateTime, formatRelative } from '@/features/issues/format'
import { authApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { Alert, Button, Card } from '@/shared/ui/primitives'

export function SessionsScreen() {
  const { t } = useTranslation(['auth', 'common'])
  const queryClient = useQueryClient()

  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => authApi.sessions() })

  const revoke = useMutation({
    mutationFn: (id: string) => authApi.revokeSession(id),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['sessions'] }) },
  })

  const revokeAll = useMutation({
    mutationFn: () => authApi.revokeAllSessions(),
    // 지금 세션도 함께 죽는다. 그게 의도다 — 기기 분실 시의 첫 대응이라
    // 어느 기기가 남의 손에 있는지 모르는 상태에서 고르게 하면 안 된다.
    // 다음 요청이 401 을 받고 앱 셸이 로그인 화면으로 돌린다.
    onSettled: () => { void queryClient.invalidateQueries({ queryKey: ['sessions'] }) },
  })

  const rows = sessions.data ?? []

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('auth:sessions.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('auth:sessions.description')}</p>
        </div>
        <Button
          variant="secondary"
          className="ml-auto text-xs"
          loading={revokeAll.isPending}
          onClick={() => { revokeAll.mutate() }}
        >
          {t('auth:sessions.revokeAll')}
        </Button>
      </header>

      {sessions.isError ? <Alert>{describeError(sessions.error)}</Alert> : null}
      {revoke.isError ? <Alert>{describeError(revoke.error)}</Alert> : null}
      {revokeAll.isError ? <Alert>{describeError(revokeAll.error)}</Alert> : null}

      {/* 이름을 붙인다. 사이드바에도 목록이 있어 "목록" 만으로는 어느
          쪽인지 알 수 없다 — 보조 기술에도, 테스트에도 똑같이 그렇다. */}
      <ul className="flex flex-col gap-2" aria-label={t('auth:sessions.title')}>
        {rows.map((row) => (
          <li key={row.id}>
            <SessionCard
              session={row}
              busy={revoke.isPending}
              onRevoke={() => { revoke.mutate(row.id) }}
            />
          </li>
        ))}
      </ul>
    </section>
  )
}

function SessionCard({
  session,
  busy,
  onRevoke,
}: {
  session: SessionInfo
  busy: boolean
  onRevoke: () => void
}) {
  const { t } = useTranslation('auth')
  const device = session.user_agent ?? t('sessions.unknownDevice')

  return (
    <Card className="flex items-center gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="truncate text-sm font-medium">
          {device}
          {session.is_current ? (
            <span className="ml-2 rounded bg-surface-raised px-1.5 py-0.5 text-xs text-muted">
              {t('sessions.current')}
            </span>
          ) : null}
        </p>
        <p className="text-xs text-muted">
          {session.ip ?? t('sessions.unknownIp')} · {formatRelative(session.created_at)} ·{' '}
          {t('sessions.expires', { when: formatDateTime(session.expires_at) })}
        </p>
      </div>
      {/* 지금 쓰는 자리를 끊는 것은 로그아웃이다. 그 길은 앱 셸에 이미 있다. */}
      {session.is_current ? null : (
        // 보이는 글자는 짧게, 읽히는 이름은 기기까지. 버튼이 여럿 늘어서는
        // 화면이라 "끊기" 만으로는 어느 기기를 끊는지 소리로 알 수 없다.
        <Button
          variant="ghost"
          className="ml-auto shrink-0 text-xs"
          aria-label={t('sessions.revokeLabel', { device })}
          loading={busy}
          onClick={onRevoke}
        >
          {t('sessions.revoke')}
        </Button>
      )}
    </Card>
  )
}
