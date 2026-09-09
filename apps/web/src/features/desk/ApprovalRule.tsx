/**
 * 요청 유형의 승인 규칙 편집 (C12).
 *
 * **줄에서 바로 고친다.** 요청 유형에는 편집 폼이 없다(유형을 갈아 끼우면
 * 매핑이 무의미해지므로 일부러 없다). 그래서 지식베이스 연결과 같은 방식으로
 * 이 한 칸만 따로 낸다.
 *
 * 승인자를 **드롭다운으로 고르지 않는다.** 사람 목록을 통째로 받아 채우면
 * 101명인 조직에서 마지막 사람은 승인자가 될 수 없다 — 이 저장소에서 세 번
 * 겪은 실수다(`PersonPicker` 주석). 검색으로 고른다.
 *
 * **여기를 고쳐도 이미 기다리는 요청은 바뀌지 않는다.** 승인자는 요청이
 * 들어오는 순간에 찍히고, 설정 변경이 지난 요청의 문을 열면 승인을 기다리던
 * 티켓이 아무 결정 없이 통과한다. 그 사실을 화면이 말해 준다.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { ApprovalMode, RequestType } from '@ieum/api-client'

import { useUserNames } from '@/features/issues/hooks'
import { PersonPicker } from '@/features/settings/PersonPicker'
import { deskApi, groupsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Chip, Select } from '@/shared/ui/primitives'

const MODES: ApprovalMode[] = ['one', 'all']

export function ApprovalRule({
  requestType,
  onChanged,
}: {
  requestType: RequestType
  onChanged: () => void
}) {
  const { t } = useTranslation(['desk', 'common'])
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<ApprovalMode>(requestType.approval?.mode ?? 'one')
  const [userIds, setUserIds] = useState<string[]>(requestType.approval?.user_ids ?? [])
  const [groupIds, setGroupIds] = useState<string[]>(requestType.approval?.group_ids ?? [])

  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => groupsApi.list(),
    enabled: open,
  })
  // 고른 사람의 이름. 저장된 규칙에는 id 만 있어서 이름을 따로 물어야 한다 —
  // 목록을 통째로 받지 않고 id 로 좁혀 묻는 훅이 이미 있다.
  const names = useUserNames(userIds)

  const save = useMutation({
    mutationFn: () =>
      deskApi.updateRequestType(requestType.id, {
        approval: { mode, user_ids: userIds, group_ids: groupIds },
      }),
    onSuccess: () => {
      setOpen(false)
      onChanged()
    },
  })

  const turnOff = useMutation({
    // **빈 값이 아니라 "끈다" 를 보낸다.** 서버에서 `null` 은 "안 건드린다" 다.
    mutationFn: () => deskApi.updateRequestType(requestType.id, { clear_approval: true }),
    onSuccess: () => {
      setOpen(false)
      setUserIds([])
      setGroupIds([])
      onChanged()
    },
  })

  const chosen = new Set(userIds)

  if (!open) {
    return (
      <span className="flex items-baseline gap-1 text-xs text-muted">
        {t('desk:approval.rule')}
        {requestType.approval ? (
          <Badge tone="todo">{t(`desk:approval.mode.${requestType.approval.mode}`)}</Badge>
        ) : (
          <span>{t('desk:approval.ruleNone')}</span>
        )}
        <Button variant="ghost" className="text-xs" onClick={() => { setOpen(true) }}>
          {requestType.approval ? t('common:action.edit') : t('desk:approval.ruleOn')}
        </Button>
      </span>
    )
  }

  return (
    <div className="flex w-full flex-col gap-2 rounded border border-border p-2">
      <p className="text-xs text-muted">{t('desk:approval.ruleHint')}</p>
      {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
      {turnOff.isError ? <Alert>{describeError(turnOff.error)}</Alert> : null}

      <Select
        label={t('desk:approval.rule')}
        value={mode}
        onChange={(event) => { setMode(event.target.value as ApprovalMode) }}
      >
        {MODES.map((value) => (
          <option key={value} value={value}>{t(`desk:approval.mode.${value}`)}</option>
        ))}
      </Select>

      <div className="flex flex-wrap gap-1.5">
        {userIds.map((id) => (
          <Chip
            key={id}
            pressed
            aria-label={t('desk:approval.removeApprover')}
            onClick={() => { setUserIds((current) => current.filter((value) => value !== id)) }}
          >
            {names.data?.get(id) ?? '…'} ×
          </Chip>
        ))}
      </div>
      {/* 고객 계정은 승인할 수 없다. 서버가 거절하므로 먼저 말해 준다. */}
      <p className="text-xs text-muted">{t('desk:approval.ruleCustomerWarning')}</p>
      <PersonPicker
        label={t('desk:automation.pickPerson')}
        exclude={chosen}
        onPick={(userId) => { setUserIds((current) => [...current, userId]) }}
      />

      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-muted">{t('desk:approval.ruleGroups')}</span>
        <select
          multiple
          className="rounded border border-border bg-surface px-1 py-0.5 text-xs"
          value={groupIds}
          onChange={(event) => {
            setGroupIds(
              Array.from(event.target.selectedOptions).map((option) => option.value),
            )
          }}
        >
          {(groups.data ?? []).map((group) => (
            <option key={group.id} value={group.id}>{group.name}</option>
          ))}
        </select>
        {groups.isSuccess && groups.data.length === 0 ? (
          <span className="text-muted">{t('desk:approval.ruleNoGroups')}</span>
        ) : null}
      </label>

      <div className="flex gap-2">
        <Button
          className="text-xs"
          loading={save.isPending}
          disabled={userIds.length === 0 && groupIds.length === 0}
          onClick={() => { save.mutate() }}
        >
          {t('common:action.save')}
        </Button>
        {requestType.approval ? (
          <Button
            variant="secondary"
            className="text-xs"
            loading={turnOff.isPending}
            onClick={() => { turnOff.mutate() }}
          >
            {t('desk:approval.ruleOff')}
          </Button>
        ) : null}
        <Button variant="ghost" className="text-xs" onClick={() => { setOpen(false) }}>
          {t('common:action.cancel')}
        </Button>
      </div>
    </div>
  )
}
