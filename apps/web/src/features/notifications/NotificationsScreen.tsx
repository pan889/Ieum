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

import { formatDateTime } from '@/features/issues/format'
import { notificationsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card } from '@/shared/ui/primitives'

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
      <div className="flex items-baseline gap-3">
        <h1 className="text-xl font-semibold">{t('notifications:list.title')}</h1>
        {unread > 0 ? (
          <span className="text-sm text-muted">
            {t('notifications:list.unreadCount', { count: unread })}
          </span>
        ) : null}
        <div className="ml-auto flex gap-2">
          <Button
            variant="ghost"
            className="text-xs"
            aria-pressed={unreadOnly}
            onClick={() => { setUnreadOnly((v) => !v) }}
          >
            {t('notifications:list.unreadOnly')}
          </Button>
          <Button
            variant="secondary"
            className="text-xs"
            disabled={unread === 0}
            loading={markRead.isPending}
            onClick={() => { markRead.mutate(undefined) }}
          >
            {t('notifications:action.markAllRead')}
          </Button>
        </div>
      </div>

      {list.isError ? <Alert>{describeError(list.error)}</Alert> : null}
      {markRead.isError ? <Alert>{describeError(markRead.error)}</Alert> : null}

      {items.length === 0 && !list.isPending ? (
        <p className="text-sm text-muted">{t('notifications:list.empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-2">
        {items.map((row) => (
          <li key={row.id}>
            <Card
              className={clsx(
                'flex items-baseline gap-3',
                row.read_at === null ? 'border-accent' : null,
              )}
            >
              <div className="flex min-w-0 flex-col gap-0.5">
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
                <span className="text-xs text-muted">{formatDateTime(row.created_at)}</span>
              </div>
              {row.read_at === null ? (
                <Button
                  variant="ghost"
                  className="ml-auto shrink-0 text-xs"
                  onClick={() => { markRead.mutate([row.id]) }}
                >
                  {t('notifications:action.markRead')}
                </Button>
              ) : null}
            </Card>
          </li>
        ))}
      </ul>
    </div>
  )
}
