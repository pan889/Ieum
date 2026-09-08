import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Sprint } from '@ieum/api-client'

import { sprintsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Select } from '@/shared/ui/primitives'

interface Props {
  sprint: Sprint
  /** 옮겨 받을 수 있는 스프린트. 닫힌 것과 자기 자신은 빠져 있다. */
  targets: Sprint[]
  onDone: () => Promise<void> | void
  onCancel: () => void
}

/**
 * 스프린트를 닫는다.
 *
 * **어디로 보낼지 고르기 전에는 닫히지 않는다.** 서버가 거절하는 것과 같은
 * 이유다: 기본값을 두면 "다음에 하기로 했던 것" 이 조용히 백로그로 간다.
 * 거절하는 쪽만 있고 고르게 하는 쪽이 없으면, 사람은 에러만 보고 만다.
 */
export function CloseSprintForm({ sprint, targets, onDone, onCancel }: Props) {
  const { t } = useTranslation(['sprints', 'common'])
  //: `''` 는 "아직 안 골랐다" — `'backlog'` 와 다르다.
  const [where, setWhere] = useState('')

  const close = useMutation({
    mutationFn: () =>
      sprintsApi.close(
        sprint.id,
        where === 'backlog' ? { to_backlog: true } : { move_to: where },
      ),
    onSuccess: onDone,
  })

  const remaining = sprint.remaining_issues

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-medium">{t('sprints:close.title', { name: sprint.name })}</h2>
      <p className="text-sm text-muted">
        {remaining === 0
          ? t('sprints:close.none')
          : t('sprints:close.remaining', { count: remaining })}
      </p>
      {close.isError ? <Alert>{describeError(close.error)}</Alert> : null}
      <Select
        label={t('sprints:close.pickOne')}
        value={where}
        onChange={(event) => { setWhere(event.target.value) }}
      >
        <option value="">—</option>
        <option value="backlog">{t('sprints:close.toBacklog')}</option>
        {targets.map((row) => (
          <option key={row.id} value={row.id}>
            {t('sprints:close.moveTo')} — {row.name}
          </option>
        ))}
      </Select>
      {targets.length === 0 ? (
        <p className="text-xs text-muted">{t('sprints:close.noTarget')}</p>
      ) : null}
      <div className="flex gap-2">
        <Button loading={close.isPending} disabled={where === ''} onClick={() => { close.mutate() }}>
          {t('sprints:close.submit')}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          {t('sprints:close.cancel')}
        </Button>
      </div>
    </Card>
  )
}
