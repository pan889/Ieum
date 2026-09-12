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

export function WatchButton({
  target,
  id,
  known,
}: {
  target: WatchTarget
  id: string
  /**
   * 이미 아는 답. **목록이 한 번에 물어 온 결과를 내려 준다.**
   *
   * 이 값이 있으면 이 단추는 스스로 묻지 않는다 — 행마다 묻는 것이 한
   * 페이지에 스무 번이 되는 자리를 막는다(`ProjectPicker` 에 세 번 데고
   * 적어 둔 것과 같은 무름이다).
   *
   * 눌러서 바뀐 뒤에는 `['watch']` 를 통째로 무효화하므로, 목록의 질의가
   * 다시 돌아 새 값이 이 자리로 내려온다. 여기서 낙관적으로 뒤집지 않는
   * 이유는 아래와 같다.
   */
  known?: boolean
}) {
  const { t } = useTranslation(['notifications'])
  const queryClient = useQueryClient()
  // 목록이 답을 줬으면 그 답을 쓴다. 안 줬을 때만 내가 묻는다.
  const status = useWatch(target, known === undefined ? id : null)
  const watching = known ?? status.data?.watching ?? false

  const toggle = useMutation({
    mutationFn: () =>
      watching ? notificationsApi.watches.stop(target, id) : notificationsApi.watches.start(target, id),
    // **접두사로 통째로 무효화한다.** 개별 질의(`['watch', target, id]`)와
    // 목록 질의(`['watch', target, 'many', ...]`)가 둘 다 이 밑에 있고,
    // 목록에서 누른 경우에는 뒤쪽이 다시 돌아야 단추가 바뀐다.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watch'] }),
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
