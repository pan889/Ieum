import { useMutation } from '@tanstack/react-query'
import type { BulkEditResult } from '@ieum/api-client'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'

import { priorityLabel } from './format'

export function BulkBar({
  selected,
  onClear,
  onApplied,
}: {
  selected: string[]
  onClear: () => void
  /**
   * 결과를 위로 올린다. 여기서 들고 있으면 선택이 풀리는 순간 이 컴포넌트가
   * 사라지면서 **실패 목록까지 같이 사라진다** — 100건 중 3건이 실패해도
   * 사용자는 못 본다.
   */
  onApplied: (result: BulkEditResult) => void
}) {
  const { t } = useTranslation(['issues', 'common'])
  const [priority, setPriority] = useState('')
  const [addLabels, setAddLabels] = useState('')
  const [removeLabels, setRemoveLabels] = useState('')

  const split = (raw: string) =>
    raw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)

  const apply = useMutation({
    mutationFn: () =>
      issuesApi.bulkEdit({
        issue_ids: selected,
        ...(priority ? { changes: { priority: Number(priority) } } : {}),
        ...(split(addLabels).length > 0 ? { add_labels: split(addLabels) } : {}),
        ...(split(removeLabels).length > 0 ? { remove_labels: split(removeLabels) } : {}),
      }),
    onSuccess: (result) => {
      setPriority('')
      setAddLabels('')
      setRemoveLabels('')
      onApplied(result)
    },
  })

  const nothingToDo =
    priority === '' && split(addLabels).length === 0 && split(removeLabels).length === 0

  return (
    <Card className="flex flex-col gap-3 py-3">
      <div className="flex flex-wrap items-end gap-3">
        <span className="text-sm font-medium">
          {t('issues:bulk.selected', { count: selected.length })}
        </span>

        <Select
          label={t('issues:bulk.priority')}
          className="py-1 text-xs"
          value={priority}
          onChange={(e) => { setPriority(e.target.value); }}
        >
          <option value="">—</option>
          {[1, 2, 3, 4, 5].map((p) => (
            <option key={p} value={p}>{priorityLabel(p)}</option>
          ))}
        </Select>

        <div className="w-40">
          <Field
            label={t('issues:bulk.addLabels')}
            className="py-1 text-xs"
            value={addLabels}
            onChange={(e) => { setAddLabels(e.target.value); }}
          />
        </div>
        <div className="w-40">
          <Field
            label={t('issues:bulk.removeLabels')}
            className="py-1 text-xs"
            value={removeLabels}
            onChange={(e) => { setRemoveLabels(e.target.value); }}
          />
        </div>

        <Button loading={apply.isPending} disabled={nothingToDo} onClick={() => { apply.mutate(); }}>
          {t('issues:bulk.apply')}
        </Button>
        <Button variant="ghost" onClick={onClear}>
          {t('issues:bulk.clear')}
        </Button>
      </div>

      {apply.isError ? <Alert>{describeError(apply.error)}</Alert> : null}

    </Card>
  )
}
