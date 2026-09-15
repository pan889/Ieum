/**
 * 알림함.
 *
 * 문구는 서버가 **수신자 언어로 렌더해서** 저장한다(i18n.md 3절). 화면이 다시
 * 번역하지 않는다 — 나중에 언어를 바꿔도 과거 알림은 그때 말로 남는 게,
 * 뒤늦게 말이 바뀌는 것보다 낫다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import clsx from 'clsx'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { EMAIL_MODES, type EmailMode } from '@ieum/api-client'
import { formatDateTime } from '@/features/issues/format'
import { notificationsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, EmptyState, PageHeader, Select } from '@/shared/ui/primitives'

export function useUnreadCount() {
  return useQuery({
    queryKey: ['notifications', 'unread'],
    queryFn: () => notificationsApi.list({ limit: 1, unreadOnly: true }),
    select: (page) => page.unread_count,
    // 배지는 다른 사람이 일으킨 일을 보여주므로 스스로 갱신돼야 한다.
    refetchInterval: 60_000,
  })
}

export function NotificationsScreen() {
  const { t } = useTranslation(['notifications', 'common'])
  const queryClient = useQueryClient()
  const [unreadOnly, setUnreadOnly] = useState(false)

  const list = useQuery({
    queryKey: ['notifications', 'list', unreadOnly],
    queryFn: () => notificationsApi.list({ unreadOnly, limit: 50 }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['notifications'] })
  }

  const markRead = useMutation({
    mutationFn: (ids?: string[]) => notificationsApi.markRead(ids),
    onSuccess: refresh,
  })

  const items = list.data?.items ?? []
  const unread = list.data?.unread_count ?? 0

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <PageHeader
        title={t('notifications:list.title')}
        description={
          unread > 0 ? t('notifications:list.unreadCount', { count: unread }) : undefined
        }
        actions={
          <>
            <Button
              variant="ghost"
              aria-pressed={unreadOnly}
              onClick={() => { setUnreadOnly((v) => !v) }}
            >
              {t('notifications:list.unreadOnly')}
            </Button>
            <Button
              variant="secondary"
              disabled={unread === 0}
              loading={markRead.isPending}
              onClick={() => { markRead.mutate(undefined) }}
            >
              {t('notifications:action.markAllRead')}
            </Button>
          </>
        }
      />

      {list.isError ? <Alert>{describeError(list.error)}</Alert> : null}
      {markRead.isError ? <Alert>{describeError(markRead.error)}</Alert> : null}

      <Preferences />

      {items.length === 0 && !list.isPending ? (
        <EmptyState title={t('notifications:list.empty')} />
      ) : (
        /*
          알림 하나에 카드 하나였다. 한 줄 반짜리 글에 88픽셀을 쓰면 한
          화면에 여덟 개가 들어가고, 서른한 개를 훑는 데 네 번 스크롤한다.
          한 상자 안에서 줄로 나누고, **안 읽은 것은 왼쪽 띠**로 말한다 —
          테두리 전체를 강조색으로 두르면 줄마다 상자가 번쩍인다.
        */
        <ul className="divide-y divide-border overflow-hidden rounded-card border border-border bg-surface shadow-raised">
          {items.map((row) => (
            <li
              key={row.id}
              className={clsx(
                'flex items-center gap-3 border-l-2 px-3 py-2 hover:bg-surface-raised',
                row.read_at === null ? 'border-l-accent' : 'border-l-transparent',
              )}
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                {row.link ? (
                  // 링크는 앱 내부 경로다. 눌렀으면 읽은 것으로 본다.
                  <Link
                    to={row.link}
                    className="truncate text-sm text-fg hover:underline"
                    onClick={() => { markRead.mutate([row.id]) }}
                  >
                    {row.title}
                  </Link>
                ) : (
                  <span className="truncate text-sm text-fg">{row.title}</span>
                )}
                <span className="text-xs tabular-nums text-subtle">
                  {formatDateTime(row.created_at)}
                </span>
              </div>
              {row.read_at === null ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="shrink-0"
                  onClick={() => { markRead.mutate([row.id]) }}
                >
                  {t('notifications:action.markRead')}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * 수신 설정.
 *
 * 메일을 켬/끔 하나로 두면 알림마다 한 통씩 오고, 사람들은 통째로 끈다.
 * 끈 사람에게는 아무것도 못 알린다. 그래서 가운데 칸("하루에 한 번")이 있다.
 */
function Preferences() {
  const { t } = useTranslation(['notifications'])
  const queryClient = useQueryClient()

  const preferences = useQuery({
    queryKey: ['notifications', 'preferences'],
    queryFn: () => notificationsApi.preferences(),
  })

  const save = useMutation({
    mutationFn: (mode: EmailMode) => notificationsApi.updatePreferences({ email_mode: mode }),
    onSuccess: (next) => {
      queryClient.setQueryData(['notifications', 'preferences'], next)
    },
  })

  const mode = preferences.data?.email_mode
  if (mode === undefined) return null

  return (
    <Card className="flex flex-col gap-1.5">
      <div className="flex items-center gap-3">
        <Select
          label={t('notifications:preferences.emailMode')}
          value={mode}
          disabled={save.isPending}
          onChange={(event) => { save.mutate(event.target.value as EmailMode) }}
        >
          {EMAIL_MODES.map((value) => (
            <option key={value} value={value}>
              {t(`notifications:preferences.email.${value}`)}
            </option>
          ))}
        </Select>
        {save.isSuccess ? (
          <span className="text-xs text-muted">{t('notifications:preferences.saved')}</span>
        ) : null}
      </div>
      <p className="text-xs text-muted">{t('notifications:preferences.emailHint')}</p>
      {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
    </Card>
  )
}
