/**
 * 구독 토글.
 *
 * 대상 종류만 다르고 하는 일은 같아서 문서·스페이스·이슈가 같이 쓴다.
 * 구독 상태를 낙관적으로 뒤집지 않는다 — 눌렀는데 실패하면 "구독 중" 이라고
 * 적힌 채 아무 알림도 안 오는 것이 제일 나쁘다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { WatchTarget } from '@ieum/api-client'
import { notificationsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Button } from '@/shared/ui/primitives'

export function useWatch(target: WatchTarget, id: string | null) {
  return useQuery({
    queryKey: ['watch', target, id],
    queryFn: () => notificationsApi.watches.status(target, id as string),
    enabled: id !== null,
  })
}

export function WatchButton({ target, id }: { target: WatchTarget; id: string }) {
  const { t } = useTranslation(['notifications'])
  const queryClient = useQueryClient()
  const status = useWatch(target, id)
  const watching = status.data?.watching ?? false

  const toggle = useMutation({
    mutationFn: () =>
      watching ? notificationsApi.watches.stop(target, id) : notificationsApi.watches.start(target, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watch', target, id] }),
  })

  return (
    <Button
      variant="ghost"
      className="text-xs"
      aria-pressed={watching}
      loading={toggle.isPending}
      title={toggle.isError ? describeError(toggle.error) : undefined}
      onClick={() => { toggle.mutate() }}
    >
      {watching ? t('notifications:watch.watching') : t('notifications:watch.start')}
    </Button>
  )
}
