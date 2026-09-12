/**
 * 반복 이슈 — 스케줄 목록과 편집 (A27, M5).
 *
 * **다음에 도는 시각을 목록에서 보여 준다.** 반복은 아무 일도 일어나지 않는
 * 방식으로 고장난다: 켠 줄 알았는데 안 돌고, 안 도는 것은 화면에 아무 표시도
 * 없다. 다음 시각이 지나 있으면 그것이 곧 "안 돌고 있다" 는 신호다.
 *
 * **스스로 꺼진 이유도 보여 준다.** 만든 사람의 계정이 정지되면 서버가 끄는데
 * (그 이름으로 계속 만들 수 없다), 이유가 없으면 사람은 로그를 봐야 한다.
 *
 * 시간대를 **스케줄이** 갖는다. 화면은 기본값으로 브라우저의 시간대를 채워
 * 주지만, 저장되는 것은 그때 고른 값이다 — 보는 사람이 바뀌어도 도는 시각은
 * 안 바뀐다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Cadence, Project, Recurrence, RecurrenceInput } from '@ieum/api-client'

import { formatDateTime } from '@/features/issues/format'
import { useIssueTypes, useUserNames, useUserSearch } from '@/features/issues/hooks'
import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { recurrencesApi } from '@/shared/api'
import { describeError, describeErrorCode } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field, Select } from '@/shared/ui/primitives'

/**
 * 번역 함수. i18next 의 `TFunction` 을 그대로 쓰지 않는 이유는 시험이다 —
 * 그 타입을 만족하는 가짜를 만들려면 오버로드 전체를 흉내내야 한다. 여기서
 * 필요한 것은 "키와 값을 주면 글자를 준다" 뿐이다.
 */
export type Translate = (key: string, options?: Record<string, unknown>) => string

const CADENCES: Cadence[] = ['daily', 'weekly', 'monthly']
/** 0=월 … 6=일. `datetime.weekday()` 와 같은 순서다 — 서버가 그렇게 센다. */
const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]

interface Draft {
  name: string
  summary: string
  description: string
  cadence: Cadence
  hour: string
  minute: string
  weekday: string
  day: string
  timezone: string
  priority: string
  dueInDays: string
  /** 빈 문자열은 "프로젝트 기본 유형" 이다 — 서버가 고른다. */
  typeId: string
  /** 빈 문자열은 "담당자 없음" 이다. */
  assigneeId: string
  /** 쉼표로 나눈다 (새 이슈 화면과 같다). */
  labels: string
}

function emptyDraft(): Draft {
  return {
    name: '',
    summary: '',
    description: '',
    cadence: 'weekly',
    hour: '9',
    minute: '0',
    weekday: '0',
    day: '1',
    // 브라우저의 시간대를 기본값으로 채운다. **저장되는 것은 이 값이다** —
    // 나중에 다른 시간대에서 열어도 도는 시각은 안 바뀐다.
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    priority: '3',
    dueInDays: '',
    typeId: '',
    assigneeId: '',
    labels: '',
  }
}

function draftOf(row: Recurrence): Draft {
  return {
    name: row.name,
    summary: row.summary,
    description: row.description ?? '',
    cadence: row.cadence as Cadence,
    hour: String(row.hour),
    minute: String(row.minute),
    weekday: String(row.weekday ?? 0),
    day: String(row.day ?? 1),
    timezone: row.timezone,
    priority: String(row.priority),
    dueInDays: row.due_in_days === null ? '' : String(row.due_in_days),
    typeId: row.type_id ?? '',
    assigneeId: row.assignee_id ?? '',
    labels: row.labels.join(', '),
  }
}

function toInput(draft: Draft, projectId: string): RecurrenceInput {
  return {
    project_id: projectId,
    name: draft.name,
    summary: draft.summary,
    description: draft.description.trim() === '' ? null : draft.description,
    priority: Number(draft.priority),
    due_in_days: draft.dueInDays.trim() === '' ? null : Number(draft.dueInDays),
    type_id: draft.typeId === '' ? null : draft.typeId,
    assignee_id: draft.assigneeId === '' ? null : draft.assigneeId,
    labels: draft.labels.split(',').map((label) => label.trim()).filter(Boolean),
    schedule: {
      cadence: draft.cadence,
      hour: Number(draft.hour),
      minute: Number(draft.minute),
      timezone: draft.timezone,
      // 서버는 주기에 맞는 것만 본다. 안 쓰는 값을 보내면 거절되지 않지만
      // 보내지 않는 편이 요청을 읽기 쉽다.
      weekday: draft.cadence === 'weekly' ? Number(draft.weekday) : null,
      day: draft.cadence === 'monthly' ? Number(draft.day) : null,
    },
  }
}

export function RecurrencesScreen() {
  const { t } = useTranslation(['recurrences', 'common'])
  const queryClient = useQueryClient()
  const [project, setProject] = useState<Project | null>(null)
  const projectId = project?.id ?? null
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [editing, setEditing] = useState<string | null>(null)
  const [assigneeQuery, setAssigneeQuery] = useState('')

  const listKey = ['recurrences', 'list', projectId]
  const rows = useQuery({
    queryKey: listKey,
    queryFn: () => recurrencesApi.list(projectId as string),
    enabled: projectId !== null,
  })

  const types = useIssueTypes(projectId)
  const candidates = useUserSearch(assigneeQuery)
  // 목록의 담당자 이름. 고른 사람도 넣는다 — 후보 20명 밖의 사람을 고른
  // 채로 저장하고 나면 이름을 알 길이 여기뿐이다.
  const names = useUserNames([
    ...(rows.data ?? []).map((row) => row.assignee_id),
    draft.assigneeId === '' ? null : draft.assigneeId,
  ])

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['recurrences'] })

  const clearForm = () => {
    setDraft(emptyDraft())
    setEditing(null)
    setAssigneeQuery('')
  }

  const save = useMutation({
    mutationFn: () =>
      editing === null
        ? recurrencesApi.create(toInput(draft, projectId as string))
        : recurrencesApi.update(editing, toInput(draft, projectId as string)),
    onSuccess: async () => {
      await refresh()
      clearForm()
    },
  })

  // **켜고 끄기는 폼을 건드리지 않는다.** 한 줄을 고치는 중에 다른 줄을
  // 끄면 쓰던 것이 사라지는데, 그건 사람이 다시 안 쓰는 종류의 일이다.
  const toggle = useMutation({
    mutationFn: (row: Recurrence) => recurrencesApi.setEnabled(row.id, !row.is_enabled),
    onSuccess: refresh,
  })

  const remove = useMutation({
    mutationFn: (row: Recurrence) => recurrencesApi.remove(row.id),
    onSuccess: async (_result, row) => {
      await refresh()
      // 지운 줄을 고치던 중이었으면 그 폼은 갈 곳이 없다.
      if (editing === row.id) clearForm()
    },
  })

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t('recurrences:title')}</h1>
        <p className="text-sm text-muted">{t('recurrences:description')}</p>
      </header>

      <ProjectPicker label={t('recurrences:project')} chosen={project} onPick={setProject} />

      {projectId === null ? (
        <p className="text-sm text-muted">{t('recurrences:pickProject')}</p>
      ) : (
        <>
          <Card>
            <form
              className="flex flex-col gap-4"
              onSubmit={(event) => {
                event.preventDefault()
                save.mutate()
              }}
            >
              <h2 className="text-sm font-medium">
                {editing === null ? t('recurrences:newTitle') : t('recurrences:editTitle')}
              </h2>
              {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}

              <Field
                label={t('recurrences:name')}
                hint={t('recurrences:nameHint')}
                required
                value={draft.name}
                onChange={(e) => { set('name', e.target.value) }}
              />
              <Field
                label={t('recurrences:summary')}
                hint={t('recurrences:summaryHint')}
                required
                value={draft.summary}
                onChange={(e) => { set('summary', e.target.value) }}
              />

              <div className="flex flex-wrap gap-3">
                <Select
                  label={t('recurrences:cadence')}
                  value={draft.cadence}
                  onChange={(e) => { set('cadence', e.target.value as Cadence) }}
                >
                  {CADENCES.map((name) => (
                    <option key={name} value={name}>
                      {t(`recurrences:cadence.${name}`)}
                    </option>
                  ))}
                </Select>

                {draft.cadence === 'weekly' ? (
                  <Select
                    label={t('recurrences:weekday')}
                    value={draft.weekday}
                    onChange={(e) => { set('weekday', e.target.value) }}
                  >
                    {WEEKDAYS.map((index) => (
                      <option key={index} value={String(index)}>
                        {t(`recurrences:weekday.${String(index)}`)}
                      </option>
                    ))}
                  </Select>
                ) : null}

                {draft.cadence === 'monthly' ? (
                  <Field
                    label={t('recurrences:day')}
                    hint={t('recurrences:dayHint')}
                    type="number"
                    min={1}
                    max={31}
                    value={draft.day}
                    onChange={(e) => { set('day', e.target.value) }}
                  />
                ) : null}

                <Field
                  label={t('recurrences:hour')}
                  type="number"
                  min={0}
                  max={23}
                  value={draft.hour}
                  onChange={(e) => { set('hour', e.target.value) }}
                />
                <Field
                  label={t('recurrences:minute')}
                  type="number"
                  min={0}
                  max={59}
                  value={draft.minute}
                  onChange={(e) => { set('minute', e.target.value) }}
                />
              </div>

              <Field
                label={t('recurrences:timezone')}
                hint={t('recurrences:timezoneHint')}
                required
                value={draft.timezone}
                onChange={(e) => { set('timezone', e.target.value) }}
              />

              {/*
                만들 이슈의 틀. 유형을 비우면 **서버가 프로젝트 기본 유형**을
                고른다 — 목록이 아직 안 왔을 때 빈 값으로 저장되어도 이슈는
                제대로 만들어진다.
              */}
              {types.data && types.data.length > 0 ? (
                <Select
                  label={t('recurrences:type')}
                  value={draft.typeId}
                  onChange={(e) => { set('typeId', e.target.value) }}
                >
                  <option value="">{t('recurrences:defaultType')}</option>
                  {types.data.map((type) => (
                    <option key={type.id} value={type.id}>{type.name}</option>
                  ))}
                </Select>
              ) : null}

              {/*
                **담당자를 여기서 정한다.** 반복으로 만들어진 이슈에 주인이
                없으면 아무도 안 본다 — 그게 반복이 조용히 쓸모없어지는 길이다.
                후보는 스무 명까지라 검색 칸을 함께 둔다.
              */}
              <div className="flex flex-wrap items-end gap-3">
                <Select
                  label={t('recurrences:assignee')}
                  value={draft.assigneeId}
                  onChange={(e) => { set('assigneeId', e.target.value) }}
                >
                  <option value="">{t('recurrences:unassigned')}</option>
                  {/*
                    고른 사람이 후보 목록에 없을 수 있다(검색어를 바꿨거나,
                    스무 명 밖의 사람이거나). 그때 `<option>` 이 없으면
                    브라우저가 값을 버리고 **담당자가 조용히 지워진다.**
                  */}
                  {draft.assigneeId !== '' &&
                  !(candidates.data?.items ?? []).some((user) => user.id === draft.assigneeId) ? (
                    <option value={draft.assigneeId}>
                      {names.data?.get(draft.assigneeId) ?? '…'}
                    </option>
                  ) : null}
                  {(candidates.data?.items ?? []).map((user) => (
                    <option key={user.id} value={user.id}>{user.display_name}</option>
                  ))}
                </Select>
                <Field
                  label={t('recurrences:assigneeSearch')}
                  value={assigneeQuery}
                  onChange={(e) => { setAssigneeQuery(e.target.value) }}
                />
              </div>

              <Field
                label={t('recurrences:labels')}
                hint={t('recurrences:labelsHint')}
                value={draft.labels}
                onChange={(e) => { set('labels', e.target.value) }}
              />
              <Field
                label={t('recurrences:dueInDays')}
                hint={t('recurrences:dueInDaysHint')}
                type="number"
                min={0}
                value={draft.dueInDays}
                onChange={(e) => { set('dueInDays', e.target.value) }}
              />

              <div className="flex gap-2">
                <Button type="submit" loading={save.isPending}>
                  {editing === null ? t('common:action.create') : t('common:action.save')}
                </Button>
                {editing === null ? null : (
                  <Button
                    variant="ghost"
                    onClick={clearForm}
                  >
                    {t('common:action.cancel')}
                  </Button>
                )}
              </div>
            </form>
          </Card>

          {rows.isError ? <Alert>{describeError(rows.error)}</Alert> : null}
          {/*
            **켜고 끄기와 지우기도 실패한다.** 안 적어 두면 화면은 아무 말도
            없이 그대로다 — 껐다고 생각한 반복이 계속 이슈를 만든다. 권한이
            없거나(403) 남이 먼저 지웠거나(404) 하는 흔한 경우들이다.
          */}
          {toggle.isError ? <Alert>{describeError(toggle.error)}</Alert> : null}
          {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
          {rows.isSuccess && rows.data.length === 0 ? (
            <p className="text-sm text-muted">{t('recurrences:empty')}</p>
          ) : null}

          <ul className="flex flex-col gap-2" data-testid="recurrences">
            {(rows.data ?? []).map((row) => (
              <li key={row.id}>
                <Card className="flex flex-col gap-2">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-sm font-medium">{row.name}</span>
                    {row.is_enabled ? (
                      <Badge tone="in_progress">{t('recurrences:on')}</Badge>
                    ) : (
                      <Badge tone="neutral">{t('recurrences:off')}</Badge>
                    )}
                    <span className="text-xs text-muted">
                      {describeSchedule(row, t as Translate)}
                    </span>
                  </div>
                  <p className="text-sm">{row.summary}</p>
                  <p className="text-xs text-muted">
                    {row.assignee_id === null
                      ? t('recurrences:unassigned')
                      : (names.data?.get(row.assignee_id) ?? '…')}
                    {row.labels.length === 0 ? '' : ` · ${row.labels.join(', ')}`}
                  </p>
                  {/*
                    **다음 시각을 반드시 보여 준다.** 반복은 아무 일도 안
                    일어나는 방식으로 고장나고, 그건 이 줄에서만 보인다.
                  */}
                  <p className="text-xs text-muted">
                    {row.is_enabled
                      ? t('recurrences:nextRun', { when: formatDateTime(row.next_run_at) })
                      : t('recurrences:notScheduled')}
                    {row.last_run_at === null
                      ? ''
                      : ` · ${t('recurrences:lastRun', {
                          when: formatDateTime(row.last_run_at),
                        })}`}
                  </p>
                  {/*
                    **스스로 멈춘 이유를 보여 준다.** `last_error` 는 언제나
                    에러 코드이므로 `errors` 카탈로그로 번역한다 — 서버가
                    이슈를 만들려다 받은 거절이 그대로 여기 들어온다(접힌
                    프로젝트, 잃은 배정 권한, 정지된 계정).
                  */}
                  {row.last_error === null ? null : (
                    <Alert>
                      {t('recurrences:stoppedBecause', {
                        reason: describeErrorCode(row.last_error),
                      })}
                    </Alert>
                  )}
                  <div className="flex gap-2">
                    <Button
                      variant="ghost"
                      className="text-xs"
                      onClick={() => { setDraft(draftOf(row)); setEditing(row.id) }}
                    >
                      {t('common:action.edit')}
                    </Button>
                    <Button
                      variant="ghost"
                      className="text-xs"
                      loading={toggle.isPending}
                      onClick={() => { toggle.mutate(row) }}
                    >
                      {row.is_enabled ? t('recurrences:turnOff') : t('recurrences:turnOn')}
                    </Button>
                    <Button
                      variant="ghost"
                      className="text-xs"
                      loading={remove.isPending}
                      onClick={() => { remove.mutate(row) }}
                    >
                      {t('common:action.delete')}
                    </Button>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

/** "매주 월요일 09:00 (Asia/Seoul)". 시간대를 **함께** 적는다. */
export function describeSchedule(
  row: Pick<Recurrence, 'cadence' | 'hour' | 'minute' | 'weekday' | 'day' | 'timezone'>,
  t: Translate,
): string {
  const clock = `${String(row.hour).padStart(2, '0')}:${String(row.minute).padStart(2, '0')}`
  const when =
    row.cadence === 'weekly'
      ? t('recurrences:everyWeekday', {
          weekday: t(`recurrences:weekday.${String(row.weekday ?? 0)}`),
        })
      : row.cadence === 'monthly'
        ? t('recurrences:everyDayOfMonth', { day: row.day ?? 1 })
        : t('recurrences:everyDay')
  return `${when} ${clock} (${row.timezone})`
}
