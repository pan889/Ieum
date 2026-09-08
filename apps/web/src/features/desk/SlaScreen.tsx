/**
 * SLA 정책과 업무 달력 (feature-map C4).
 *
 * **목표를 JSON 으로 적게 하지 않는다.** 서버는 "조건 없는 기본 목표가 하나
 * 있어야 한다"·"모르는 조건 키는 거절" 을 강제하는데, 자유 입력으로 두면
 * 관리자는 중괄호를 틀려서 거절당하고 이유를 모른다 — 요청 유형 폼에서 이미
 * 겪은 결함이다. 여기서는 줄마다 조건과 시간을 따로 받는다.
 *
 * **시간은 `4h`·`30m`·`2d` 로 적는다.** 초로 받으면 사람이 14400 을 계산해야
 * 하고, 그 계산은 틀린다.
 *
 * 달력은 설치 전체에서 공유하므로 프로젝트를 안 고른다. 정책은 프로젝트
 * 단위라 고른다 — 그 차이가 화면에도 보여야 한다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type {
  BusinessCalendar,
  Project,
  SlaEscalation,
  SlaGoal,
  SlaPolicy,
  WorkflowStateOption,
} from '@ieum/api-client'

import { PersonPicker } from '@/features/settings/PersonPicker'
import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { deskApi, projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field, Select, Textarea } from '@/shared/ui/primitives'

/**
 * `4h`·`30m`·`2d 4h` → 초. 못 읽으면 `null`.
 *
 * 초를 직접 받지 않는 이유: 사람이 "영업일 3일" 을 초로 계산해야 하고, 그
 * 계산이 틀리면 SLA 가 틀린다. 그리고 틀렸다는 것을 아무도 못 본다.
 */
export function parseDuration(raw: string): number | null {
  const text = raw.trim().toLowerCase()
  if (!text) return null
  const parts = text.match(/(\d+)\s*([dhm])/g)
  if (!parts) return null
  // 숫자와 단위만으로 이루어졌는지 본다 — `4h 그리고 조금` 을 4h 로 읽으면
  // 사람이 적은 뜻을 잃는다.
  if (text.replace(/(\d+)\s*([dhm])/g, '').trim() !== '') return null
  let seconds = 0
  for (const part of parts) {
    const value = Number(part.replace(/[^\d]/g, ''))
    if (part.includes('d')) seconds += value * 86400
    else if (part.includes('h')) seconds += value * 3600
    else seconds += value * 60
  }
  return seconds > 0 ? seconds : null
}

/** 초 → `2d 4h`. 저장된 목표를 다시 보여 줄 때. */
export function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return [days ? `${String(days)}d` : '', hours ? `${String(hours)}h` : '', minutes ? `${String(minutes)}m` : '']
    .filter(Boolean)
    .join(' ')
}

/**
 * `0 09:00-12:00 13:00-18:00` 줄들 → 서버가 받는 사전.
 *
 * 자유 JSON 이 아니라 줄 단위로 받는 이유는 목표와 같다. 그리고 **못 읽은
 * 줄은 조용히 버리지 않는다** — `null` 을 돌려주고 화면이 저장을 막는다.
 * 버리면 관리자가 적은 요일이 사라진 채로 저장된다.
 */
export function parseWorkingHours(raw: string): Record<string, [string, string][]> | null {
  const week: Record<string, [string, string][]> = {}
  for (const line of raw.split('\n')) {
    const text = line.trim()
    if (!text) continue
    const [day, ...spans] = text.split(/\s+/)
    if (day === undefined || !/^[0-6]$/.test(day) || spans.length === 0) return null
    const parsed: [string, string][] = []
    for (const span of spans) {
      const match = /^(\d{2}:\d{2})-(\d{2}:\d{2})$/.exec(span)
      if (!match) return null
      parsed.push([match[1] as string, match[2] as string])
    }
    week[day] = parsed
  }
  return Object.keys(week).length > 0 ? week : null
}

export function formatWorkingHours(week: Record<string, [string, string][]>): string {
  return Object.entries(week)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([day, spans]) => `${day} ${spans.map(([from, to]) => `${from}-${to}`).join(' ')}`)
    .join('\n')
}

/** `priority>=5 1h` 줄들 → 목표 배열. 못 읽으면 `null`. */
export function parseGoals(raw: string): SlaGoal[] | null {
  const goals: SlaGoal[] = []
  for (const line of raw.split('\n')) {
    const text = line.trim()
    if (!text) continue
    const match = /^(?:priority>=(\d+)\s+)?(.+)$/.exec(text)
    if (!match) return null
    const seconds = parseDuration(match[2] as string)
    if (seconds === null) return null
    goals.push(
      match[1] === undefined
        ? { seconds }
        : { seconds, priority_min: Number(match[1]) },
    )
  }
  return goals.length > 0 ? goals : null
}

export function formatGoals(goals: SlaGoal[]): string {
  return goals
    .map((goal) =>
      goal.priority_min === undefined
        ? formatDuration(goal.seconds)
        : `priority>=${String(goal.priority_min)} ${formatDuration(goal.seconds)}`,
    )
    .join('\n')
}

const EMPTY_CALENDAR = { name: '', timezone: 'Asia/Seoul', hours: '', holidays: '' }
/**
 * 저장된 상태 id 를 사람이 읽는 이름으로. 목록 줄이 쓴다.
 *
 * 이름을 못 찾으면 **id 를 그대로 보여 준다.** 조용히 빼면 "3개 걸었는데 2개만
 * 보인다" 가 되고, 어느 것이 사라졌는지 알 수 없다 — 목록이 거짓말을 하는
 * 것보다 읽기 어려운 것이 낫다.
 */
export function pauseNames(ids: string[], options: WorkflowStateOption[] | undefined): string {
  const byId = new Map((options ?? []).map((state) => [state.id, state.name]))
  return ids.map((id) => byId.get(id) ?? id).join(', ')
}

const EMPTY_POLICY = {
  name: '',
  metric: 'first_response',
  calendarId: '',
  goals: '',
  pauseStateIds: [] as string[],
  escalations: [] as SlaEscalation[],
}

/** 새 규칙의 기본값. 대상을 고를 필요가 없는 조치로 시작한다. */
const NEW_RULE: SlaEscalation = { at_percent: 100, action: 'raise_priority', priority: 5 }

/**
 * 저장할 수 있는 규칙들인가.
 *
 * **부를 사람을 안 고른 `notify` 를 막는다.** 서버도 거절하지만, 여기서
 * 막으면 무엇이 빠졌는지 그 자리에서 보인다 — 저장을 눌러 거절당하고 나서
 * 다섯 줄 중 어느 줄이 문제인지 찾게 두지 않는다.
 */
export function escalationsReady(rules: SlaEscalation[]): boolean {
  return rules.every(
    (rule) =>
      rule.at_percent >= 1 &&
      rule.at_percent <= 500 &&
      (rule.action === 'notify' ? rule.user_id !== undefined : rule.priority !== undefined),
  )
}

export function SlaScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const [picked, setPicked] = useState<Project | null>(null)

  const first = useQuery({
    queryKey: ['projects', 'first'],
    queryFn: () => projectsApi.list({ limit: 1 }),
  })
  const project = picked ?? first.data?.items[0] ?? null

  return (
    <section className="flex flex-col gap-5">
      <SettingsNav />
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t('desk:sla.policies')}</h1>
        <p className="text-sm text-muted">{t('desk:sla.policiesDescription')}</p>
      </header>

      {/* 달력이 먼저다 — 정책에 달력이 필요하다. 순서가 그것을 말한다. */}
      <Calendars />

      <ProjectPicker label={t('desk:sla.calendar')} chosen={project} onPick={setPicked} />
      {project ? <Policies projectId={project.id} /> : null}
    </section>
  )
}

function Calendars() {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<BusinessCalendar | null>(null)
  const [form, setForm] = useState(EMPTY_CALENDAR)

  const calendars = useQuery({ queryKey: ['calendars'], queryFn: () => deskApi.listCalendars() })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['calendars'] })

  const hours = parseWorkingHours(form.hours)
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name,
        timezone: form.timezone,
        working_hours: hours ?? {},
        holidays: form.holidays.split('\n').map((line) => line.trim()).filter(Boolean),
      }
      return editing
        ? deskApi.updateCalendar(editing.id, body)
        : deskApi.createCalendar(body)
    },
    onSuccess: async () => {
      setAdding(false)
      setEditing(null)
      setForm(EMPTY_CALENDAR)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => deskApi.deleteCalendar(id),
    onSuccess: () => refresh(),
  })

  const rows = calendars.data ?? []

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-medium">{t('desk:sla.calendars')}</h2>
          <p className="text-xs text-muted">{t('desk:sla.calendarsDescription')}</p>
        </div>
        <Button
          type="button"
          variant="ghost"
          className="text-xs"
          onClick={() => {
            setEditing(null)
            setForm(EMPTY_CALENDAR)
            setAdding(true)
          }}
        >
          {t('desk:sla.addCalendar')}
        </Button>
      </div>

      {calendars.isError ? <Alert>{describeError(calendars.error)}</Alert> : null}
      {calendars.isSuccess && rows.length === 0 && !adding ? (
        <p className="text-sm text-muted">{t('desk:sla.noCalendars')}</p>
      ) : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      <ul className="flex flex-col divide-y divide-border">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <span className="font-medium">{row.name}</span>
            <span className="text-xs text-muted">{row.timezone}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted">
              {formatWorkingHours(row.working_hours).split('\n').join(' · ')}
            </span>
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => {
                setAdding(false)
                setEditing(row)
                setForm({
                  name: row.name,
                  timezone: row.timezone,
                  hours: formatWorkingHours(row.working_hours),
                  holidays: row.holidays.join('\n'),
                })
              }}
            >
              {t('common:action.edit')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => { remove.mutate(row.id) }}
            >
              {t('common:action.delete')}
            </Button>
          </li>
        ))}
      </ul>

      {adding || editing ? (
        <form
          className="flex flex-col gap-2 border-t border-border pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
          <Field
            label={t('desk:sla.name')}
            value={form.name}
            onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
          />
          <Field
            label={t('desk:sla.timezone')}
            value={form.timezone}
            onChange={(event) => { setForm({ ...form, timezone: event.target.value }) }}
          />
          {/* **손으로 마크업하지 않는다.** 처음에는 `<label>` 안에 라벨과
              힌트를 나란히 넣었고, 그러면 접근성 이름이
              "Working hours One line per weekday…" 가 된다 — 힌트가 이름의
              일부로 읽힌다. `Textarea` 는 힌트를 `aria-describedby` 로
              붙이므로 이름이 깨끗하다. E2E 여섯 개가 이름으로 못 찾아 드러났다. */}
          <Textarea
            label={t('desk:sla.workingHours')}
            hint={t('desk:sla.workingHoursHint')}
            className="font-mono"
            value={form.hours}
            onChange={(event) => { setForm({ ...form, hours: event.target.value }) }}
          />
          <Textarea
            label={t('desk:sla.holidays')}
            hint={t('desk:sla.holidaysHint')}
            className="font-mono"
            value={form.holidays}
            onChange={(event) => { setForm({ ...form, holidays: event.target.value }) }}
          />
          <div className="flex items-center gap-2">
            {/* **읽을 수 없는 업무 시간으로는 저장을 못 하게 한다.** 서버도
                거절하지만, 여기서 막으면 무엇이 문제인지 그 자리에서 보인다. */}
            <Button
              type="submit"
              loading={save.isPending}
              disabled={form.name.trim() === '' || hours === null}
            >
              {t('common:action.save')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setAdding(false)
                setEditing(null)
              }}
            >
              {t('common:action.cancel')}
            </Button>
            {form.hours.trim() !== '' && hours === null ? (
              <span className="text-xs text-danger">{t('desk:sla.workingHoursHint')}</span>
            ) : null}
          </div>
        </form>
      ) : null}
    </Card>
  )
}

/**
 * 에스컬레이션 규칙 편집기 (C5).
 *
 * **JSON 이 아니라 줄마다 손잡이다.** 조치가 둘이고 각각 다른 것을 필요로
 * 한다(누구에게 / 몇으로). 자유 입력으로 두면 서버가 거절하는 조합을 만들 수
 * 있고, 관리자는 무엇이 틀렸는지 모른다.
 *
 * `notify` 의 대상은 **고른다.** UUID 를 적게 하면 어디서도 그 값을 알 수
 * 없다 — 멈춤 상태와 같은 판단이다.
 */
function Escalations({
  rules,
  userNames,
  onChange,
}: {
  rules: SlaEscalation[]
  /** 저장된 규칙이 지목한 사람의 이름. 서버가 규칙 옆에 함께 준다. */
  userNames: Record<string, string>
  onChange: (next: SlaEscalation[]) => void
}) {
  const { t } = useTranslation(['desk', 'common'])
  const [adding, setAdding] = useState<number | null>(null)

  const replace = (index: number, rule: SlaEscalation) => {
    onChange(rules.map((row, at) => (at === index ? rule : row)))
  }

  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-xs font-medium text-fg">{t('desk:sla.escalations')}</legend>
      <p className="text-xs text-muted">{t('desk:sla.escalationsHint')}</p>

      <ul className="flex flex-col gap-1">
        {rules.map((rule, index) => (
          // 규칙에는 안정된 id 가 없다(서버가 배열을 그대로 저장한다). 조건과
          // 조치가 곧 이름이므로 그것으로 키를 만든다 — 서버도 같은 이름으로
          // 실행 여부를 기억한다.
          <li key={`${String(rule.at_percent)}:${rule.action}`} className="flex flex-wrap items-end gap-2">
            <Field
              label={t('desk:sla.atPercent')}
              type="number"
              min={1}
              max={500}
              className="w-24"
              value={String(rule.at_percent)}
              onChange={(event) => {
                replace(index, { ...rule, at_percent: Number(event.target.value) })
              }}
            />
            <Select
              label={t('desk:sla.action')}
              value={rule.action}
              onChange={(event) => {
                // 조치를 바꾸면 **다른 조치의 값을 버린다.** 남겨 두면 서버가
                // "notify 는 priority 를 쓰지 않는다" 로 거절한다.
                const action = event.target.value as SlaEscalation['action']
                if (action === 'raise_priority') {
                  replace(index, {
                    at_percent: rule.at_percent,
                    action,
                    priority: rule.priority ?? 5,
                  })
                  return
                }
                // **`user_id` 를 넣지 않는다** — 값이 없으면 키 자체가 없어야
                // 한다. `undefined` 를 담아 보내면 서버는 "notify 에는
                // user_id 가 필요하다" 가 아니라 그 키를 받은 것으로 읽는다.
                replace(
                  index,
                  rule.user_id === undefined
                    ? { at_percent: rule.at_percent, action }
                    : { at_percent: rule.at_percent, action, user_id: rule.user_id },
                )
              }}
            >
              <option value="notify">{t('desk:sla.action.notify')}</option>
              <option value="raise_priority">{t('desk:sla.action.raisePriority')}</option>
            </Select>

            {rule.action === 'raise_priority' ? (
              <Select
                label={t('desk:sla.toPriority')}
                value={String(rule.priority ?? 5)}
                onChange={(event) => {
                  replace(index, { ...rule, priority: Number(event.target.value) })
                }}
              >
                {[1, 2, 3, 4, 5].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
            ) : (
              <div className="flex flex-col gap-1">
                <span className="text-xs text-muted">
                  {t('desk:sla.notifyTarget')}:{' '}
                  {rule.user_id ? (userNames[rule.user_id] ?? rule.user_id) : '—'}
                </span>
                {adding === index ? (
                  <PersonPicker
                    label={t('desk:sla.notifyTarget')}
                    onPick={(userId) => {
                      replace(index, { ...rule, user_id: userId })
                      setAdding(null)
                    }}
                  />
                ) : (
                  <Button
                    type="button"
                    variant="ghost"
                    className="text-xs"
                    onClick={() => { setAdding(index) }}
                  >
                    {t('desk:sla.pickPerson')}
                  </Button>
                )}
              </div>
            )}

            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => { onChange(rules.filter((_, at) => at !== index)) }}
            >
              {t('common:action.delete')}
            </Button>
          </li>
        ))}
      </ul>

      <div>
        <Button
          type="button"
          variant="ghost"
          className="text-xs"
          onClick={() => { onChange([...rules, NEW_RULE]) }}
        >
          {t('desk:sla.addEscalation')}
        </Button>
      </div>
    </fieldset>
  )
}

function Policies({ projectId }: { projectId: string }) {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<SlaPolicy | null>(null)
  const [form, setForm] = useState(EMPTY_POLICY)

  const calendars = useQuery({ queryKey: ['calendars'], queryFn: () => deskApi.listCalendars() })
  const policies = useQuery({
    queryKey: ['sla', projectId],
    queryFn: () => deskApi.listSlaPolicies(projectId),
  })
  // 멈춤 상태는 **고르는 것**이다. 서버가 모르는 상태를 거절하므로 손으로
  // UUID 를 적게 두면 관리자는 거절만 당하고 무엇을 고를 수 있는지 모른다.
  const states = useQuery({
    queryKey: ['sla', projectId, 'states'],
    queryFn: () => deskApi.listPauseStateOptions(projectId),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['sla', projectId] })

  const goals = parseGoals(form.goals)
  const save = useMutation({
    mutationFn: () =>
      editing
        ? deskApi.updateSlaPolicy(editing.id, {
            name: form.name,
            calendar_id: form.calendarId,
            goals: goals ?? [],
            pause_state_ids: form.pauseStateIds,
            escalations: form.escalations,
          })
        : deskApi.createSlaPolicy({
            project_id: projectId,
            name: form.name,
            metric: form.metric,
            calendar_id: form.calendarId,
            goals: goals ?? [],
            pause_state_ids: form.pauseStateIds,
            escalations: form.escalations,
          }),
    onSuccess: async () => {
      setAdding(false)
      setEditing(null)
      setForm(EMPTY_POLICY)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => deskApi.deleteSlaPolicy(id),
    onSuccess: () => refresh(),
  })

  const rows = policies.data ?? []
  const available = calendars.data ?? []

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{t('desk:sla.policies')}</h2>
        <Button
          type="button"
          variant="ghost"
          className="text-xs"
          disabled={available.length === 0}
          onClick={() => {
            setEditing(null)
            setForm({ ...EMPTY_POLICY, calendarId: available[0]?.id ?? '' })
            setAdding(true)
          }}
        >
          {t('desk:sla.addPolicy')}
        </Button>
      </div>

      {policies.isError ? <Alert>{describeError(policies.error)}</Alert> : null}
      {policies.isSuccess && rows.length === 0 && !adding ? (
        <p className="text-sm text-muted">{t('desk:sla.noPolicies')}</p>
      ) : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      <ul className="flex flex-col divide-y divide-border">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <span className="font-medium">{row.name}</span>
            <Badge tone="neutral">{t(`desk:sla.metric.${row.metric}`)}</Badge>
            {/* 어느 달력으로 재는지 목록에서 바로 보인다 — 서버가 이름을
                함께 준다. */}
            <span className="text-xs text-muted">{row.calendar_name}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted">
              {formatGoals(row.goals).split('\n').join(' · ')}
            </span>
            {/* **저장한 뒤에도 보여야 한다.** 목록에 안 보이면 멈춤을 걸어
                뒀는지 폼을 열어 봐야 알 수 있고, 안 걸린 정책과 구별되지
                않는다. */}
            {row.pause_state_ids.length > 0 ? (
              <Badge tone="neutral">
                {t('desk:sla.pauseStates')}: {pauseNames(row.pause_state_ids, states.data)}
              </Badge>
            ) : null}
            {/* 규칙이 걸려 있는지 목록에서 보인다. 안 보이면 폼을 열어 봐야
                알고, 안 걸린 정책과 구별되지 않는다. */}
            {row.escalations.length > 0 ? (
              <Badge tone="neutral">
                {t('desk:sla.escalationCount', { count: row.escalations.length })}
              </Badge>
            ) : null}
            {row.is_enabled ? null : <Badge tone="neutral">{t('desk:sla.enabled')}: —</Badge>}
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => {
                setAdding(false)
                setEditing(row)
                setForm({
                  name: row.name,
                  metric: row.metric,
                  calendarId: row.calendar_id,
                  goals: formatGoals(row.goals),
                  pauseStateIds: [...row.pause_state_ids],
                  escalations: row.escalations.map((rule) => ({ ...rule })),
                })
              }}
            >
              {t('common:action.edit')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => { remove.mutate(row.id) }}
            >
              {t('common:action.delete')}
            </Button>
          </li>
        ))}
      </ul>

      {adding || editing ? (
        <form
          className="flex flex-col gap-2 border-t border-border pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
          <Field
            label={t('desk:sla.name')}
            value={form.name}
            onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
          />
          {/* **고칠 때는 지표를 바꿀 수 없다.** 이미 걸린 시계가 다른 것을
              재게 되므로 서버가 그 필드를 아예 받지 않는다 — 화면도 손잡이를
              그리지 않고 이유를 말한다. */}
          {editing ? (
            <p className="text-xs text-muted">
              {t('desk:sla.metricLabel')}: {t(`desk:sla.metric.${editing.metric}`)} —{' '}
              {t('desk:sla.metricImmutable')}
            </p>
          ) : (
            <Select
              label={t('desk:sla.metricLabel')}
              value={form.metric}
              onChange={(event) => { setForm({ ...form, metric: event.target.value }) }}
            >
              <option value="first_response">{t('desk:sla.metric.first_response')}</option>
              <option value="resolution">{t('desk:sla.metric.resolution')}</option>
            </Select>
          )}
          <Select
            label={t('desk:sla.calendar')}
            value={form.calendarId}
            onChange={(event) => { setForm({ ...form, calendarId: event.target.value }) }}
          >
            {available.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name} ({row.timezone})
              </option>
            ))}
          </Select>
          <Textarea
            label={t('desk:sla.goals')}
            hint={t('desk:sla.goalsHint')}
            className="font-mono"
            value={form.goals}
            onChange={(event) => { setForm({ ...form, goals: event.target.value }) }}
          />
          {/* **fieldset·legend 로 묶는다.** 체크박스마다 이름은 있어도 묶음의
              이름이 없으면 "이 체크박스들이 무엇인지" 를 읽을 수 없다.
              라벨과 힌트를 한 `<label>` 에 넣으면 접근성 이름이 둘을 이어
              붙인 문장이 된다 — 달력 폼에서 이미 겪었다.

              여기서는 `Checkbox` 프리미티브를 쓰지 않는다. 항목마다 붙는
              워크플로우 이름은 힌트가 아니라 **이름의 일부**이고(그래서
              접근성 이름에 들어가야 맞다), 프리미티브의 `label` 은 문자열
              하나라 흐리게 쓰는 둘째 단을 그릴 수 없다. */}
          <fieldset className="flex flex-col gap-1">
            <legend className="text-xs font-medium text-fg">{t('desk:sla.pauseStates')}</legend>
            <p className="text-xs text-muted">{t('desk:sla.pauseStatesHint')}</p>
            {states.isError ? <Alert>{describeError(states.error)}</Alert> : null}
            {states.isSuccess && states.data.length === 0 ? (
              <p className="text-xs text-muted">{t('desk:sla.noStates')}</p>
            ) : null}
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {(states.data ?? []).map((state) => (
                <label key={state.id} className="flex items-center gap-1.5 text-sm text-fg">
                  <input
                    type="checkbox"
                    checked={form.pauseStateIds.includes(state.id)}
                    onChange={(event) => {
                      setForm({
                        ...form,
                        pauseStateIds: event.target.checked
                          ? [...form.pauseStateIds, state.id]
                          : form.pauseStateIds.filter((id) => id !== state.id),
                      })
                    }}
                  />
                  {state.name}
                  {/* 같은 이름의 상태가 워크플로우마다 따로 있다. 이름만
                      보여 주면 똑같은 줄이 둘 뜨고 어느 쪽인지 알 수 없다. */}
                  <span className="text-xs text-muted">{state.workflow_name}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <Escalations
            rules={form.escalations}
            userNames={editing?.escalation_user_names ?? {}}
            onChange={(next) => { setForm({ ...form, escalations: next }) }}
          />
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              loading={save.isPending}
              disabled={
                form.name.trim() === '' ||
                goals === null ||
                form.calendarId === '' ||
                !escalationsReady(form.escalations)
              }
            >
              {t('common:action.save')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setAdding(false)
                setEditing(null)
              }}
            >
              {t('common:action.cancel')}
            </Button>
            {form.goals.trim() !== '' && goals === null ? (
              <span className="text-xs text-danger">{t('desk:sla.goalsHint')}</span>
            ) : null}
          </div>
        </form>
      ) : null}
    </Card>
  )
}
