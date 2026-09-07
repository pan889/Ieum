/**
 * 자동화 규칙 (feature-map C9).
 *
 * **조건을 JSON 으로 적게 하지 않는다.** 조건마다 항목·비교·값이 따로이고
 * 조치는 종류마다 다른 것을 필요로 한다. 자유 입력으로 두면 서버가 거절하는
 * 조합을 만들 수 있고, 관리자는 무엇이 틀렸는지 모른다 — SLA 목표·에스컬레이션
 * 에서 이미 겪은 판단이다.
 *
 * **조건은 전부 맞아야 한다(AND).** OR 을 넣지 않는 이유를 화면에도 적는다:
 * 규칙을 둘로 나누면 표현할 수 있고, 그 편이 목록에서 읽힌다.
 *
 * 조치가 지목한 사람·문구의 이름은 **규칙 옆에** 온다(`names`). 안에 넣으면
 * 저장 요청이 읽은 조치를 그대로 되돌려 보낼 수 없다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type {
  AutomationAction,
  AutomationCondition,
  AutomationField,
  AutomationOperator,
  AutomationRule,
  AutomationTargets,
  AutomationTrigger,
  Project,
} from '@ieum/api-client'

import { PersonPicker } from '@/features/settings/PersonPicker'
import { ProjectPicker } from '@/features/settings/ProjectPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { deskApi, projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field, Select } from '@/shared/ui/primitives'

const TRIGGERS: AutomationTrigger[] = [
  'desk.ticket.submitted',
  'issue.transitioned',
  'issue.commented',
]

/** 조건에서 볼 수 있는 것. 서버의 `automation.FIELDS` 와 같아야 한다. */
const FIELDS: AutomationField[] = [
  'priority',
  'channel',
  'request_type_id',
  'organization_id',
  'state_category',
  'summary',
  'is_internal',
  'to_state_category',
]

type Kind = 'int' | 'str' | 'uuid' | 'bool' | 'channel' | 'category'

/**
 * 항목이 가진 값의 **종류.** 서버의 `automation.FIELD_KINDS` 와 같아야 한다.
 *
 * 값 칸을 종류마다 다르게 그리려고 있다. 전부 글자 입력으로 두면
 * `is_internal` 에 `"true"` 를 적을 수 있고, 그 규칙은 저장되고 **한 번도 안
 * 걸린다** — 서버는 참·거짓과 글자를 같다고 보지 않는다. 서버도 거절하지만,
 * 거절당하고 나서 어디가 틀렸는지 찾게 두지 않는다.
 */
const FIELD_KINDS: Record<AutomationField, Kind> = {
  priority: 'int',
  channel: 'channel',
  request_type_id: 'uuid',
  organization_id: 'uuid',
  state_category: 'category',
  summary: 'str',
  is_internal: 'bool',
  to_state_category: 'category',
}

/**
 * 종류마다 고를 수 있는 비교. 서버가 받는 것의 **부분집합**이다.
 *
 * 글자 항목의 `in` 은 뺐다: 값을 쉼표로 나눠 적는 규칙을 관리자가 외워야
 * 하는데, 그 규칙은 화면에 적을 자리가 없다. 고를 수 있는 항목에서는
 * 여러 개 고르기로 낸다.
 */
const OPS_FOR_KIND: Record<Kind, AutomationOperator[]> = {
  int: ['eq', 'ne', 'gte', 'lte', 'in'],
  str: ['eq', 'ne', 'contains'],
  uuid: ['eq', 'ne', 'in'],
  bool: ['eq', 'ne'],
  channel: ['eq', 'ne', 'in'],
  category: ['eq', 'ne', 'in'],
}

const CHANNELS = ['portal', 'email', 'agent']
const CATEGORIES = ['todo', 'in_progress', 'done']
const PRIORITIES = [1, 2, 3, 4, 5]

const EMPTY = {
  name: '',
  trigger: 'desk.ticket.submitted' as AutomationTrigger,
  conditions: [] as AutomationCondition[],
  actions: [] as AutomationAction[],
}

type Form = typeof EMPTY

/** 새 조건·조치의 기본값. 대상을 고를 필요가 없는 것으로 시작한다. */
const NEW_CONDITION: AutomationCondition = { field: 'priority', op: 'eq', value: 3 }
const NEW_ACTION: AutomationAction = { kind: 'set_priority', priority: 5 }

/**
 * 저장할 수 있는 규칙인가.
 *
 * **서버가 거절하는 것을 여기서 먼저 막는다.** 저장을 눌러 거절당하고 나서
 * 조건 다섯 줄 중 어디가 문제인지 찾게 두지 않는다.
 *
 * 조치가 하나는 있어야 한다: 조건만 맞춰 보고 아무 것도 안 하는 규칙은
 * 저장되면 안 된다.
 */
export function ruleReady(form: Form): boolean {
  if (form.name.trim() === '' || form.actions.length === 0) return false
  const actionsOk = form.actions.every((action) => {
    if (action.kind === 'set_priority') return action.priority !== undefined
    if (action.kind === 'assign') return action.user_id !== undefined
    return action.canned_response_id !== undefined
  })
  const conditionsOk = form.conditions.every((condition) => {
    // 여러 개 고르기에서 하나도 안 고르면 서버가 규칙 전체를 거절한다.
    if (Array.isArray(condition.value)) return condition.value.length > 0
    if (FIELD_KINDS[condition.field] === 'bool') return typeof condition.value === 'boolean'
    if (FIELD_KINDS[condition.field] === 'int') {
      return typeof condition.value === 'number' && Number.isInteger(condition.value)
    }
    // 고르는 항목에서 안 고른 것과 글자 항목에서 빈 것은 같은 모양이다.
    return String(condition.value).trim() !== ''
  })
  return actionsOk && conditionsOk
}

export function AutomationScreen() {
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
        <h1 className="text-xl font-semibold tracking-tight">{t('desk:automation.title')}</h1>
        <p className="text-sm text-muted">{t('desk:automation.description')}</p>
      </header>

      <ProjectPicker label={t('desk:automation.project')} chosen={project} onPick={setPicked} />
      {project ? <Rules projectId={project.id} /> : null}
    </section>
  )
}

function Rules({ projectId }: { projectId: string }) {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<AutomationRule | null>(null)
  const [form, setForm] = useState<Form>(EMPTY)

  const rules = useQuery({
    queryKey: ['automation', projectId],
    queryFn: () => deskApi.listAutomationRules(projectId),
  })
  const canned = useQuery({
    queryKey: ['canned', projectId],
    queryFn: () => deskApi.listCannedResponses(projectId),
  })
  // 조건이 고를 수 있는 것들. **자동화 권한으로 연다** — 요청 유형·고객 조직
  // 목록의 권한을 마저 요구하면 고를 수는 없고 적으면 되는 자리가 된다.
  const targets = useQuery({
    queryKey: ['automation', projectId, 'targets'],
    queryFn: () => deskApi.automationTargets(projectId),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['automation', projectId] })

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name,
        trigger: { event: form.trigger },
        conditions: form.conditions,
        actions: form.actions,
      }
      return editing
        ? deskApi.updateAutomationRule(editing.id, body)
        : deskApi.createAutomationRule({ project_id: projectId, ...body })
    },
    onSuccess: async () => {
      setAdding(false)
      setEditing(null)
      setForm(EMPTY)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => deskApi.deleteAutomationRule(id),
    onSuccess: () => refresh(),
  })
  const toggle = useMutation({
    mutationFn: (row: AutomationRule) =>
      deskApi.updateAutomationRule(row.id, { is_enabled: !row.is_enabled }),
    onSuccess: () => refresh(),
  })

  const rows = rules.data ?? []
  const open = adding || editing !== null

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{t('desk:automation.rules')}</h2>
        <Button
          type="button"
          variant="ghost"
          className="text-xs"
          onClick={() => {
            setEditing(null)
            setForm({ ...EMPTY, actions: [NEW_ACTION] })
            setAdding(true)
          }}
        >
          {t('desk:automation.addRule')}
        </Button>
      </div>

      {rules.isError ? <Alert>{describeError(rules.error)}</Alert> : null}
      {rules.isSuccess && rows.length === 0 && !open ? (
        <p className="text-sm text-muted">{t('desk:automation.noRules')}</p>
      ) : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
      {toggle.isError ? <Alert>{describeError(toggle.error)}</Alert> : null}

      <ul className="flex flex-col divide-y divide-border">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span className="font-medium">{row.name}</span>
            <Badge tone="neutral">{t(`desk:automation.trigger.${row.trigger.event}`)}</Badge>
            {/* 조건 수와 조치를 목록에서 바로 보여 준다. 폼을 열어 봐야 알면
                무엇을 하는 규칙인지 목록에서 읽을 수 없다. */}
            <span className="text-xs text-muted">
              {t('desk:automation.conditionCount', { count: row.conditions.length })}
            </span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted">
              {row.actions.map((action) => describeAction(action, row.names, t)).join(' · ')}
            </span>
            {row.is_enabled ? null : <Badge tone="neutral">{t('desk:automation.paused')}</Badge>}
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => { toggle.mutate(row) }}
            >
              {row.is_enabled ? t('desk:automation.pause') : t('desk:automation.resume')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => {
                setAdding(false)
                setEditing(row)
                setForm({
                  name: row.name,
                  trigger: row.trigger.event,
                  conditions: row.conditions.map((c) => ({ ...c })),
                  actions: row.actions.map((a) => ({ ...a })),
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

      {open ? (
        <form
          className="flex flex-col gap-3 border-t border-border pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}

          <Field
            label={t('desk:automation.name')}
            value={form.name}
            onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
          />
          <Select
            label={t('desk:automation.triggerLabel')}
            hint={t('desk:automation.triggerHint')}
            value={form.trigger}
            onChange={(event) => {
              setForm({ ...form, trigger: event.target.value as AutomationTrigger })
            }}
          >
            {TRIGGERS.map((event) => (
              <option key={event} value={event}>
                {t(`desk:automation.trigger.${event}`)}
              </option>
            ))}
          </Select>

          <Conditions
            rows={form.conditions}
            targets={targets.data}
            onChange={(next) => { setForm({ ...form, conditions: next }) }}
          />
          <Actions
            rows={form.actions}
            names={editing?.names ?? {}}
            canned={canned.data ?? []}
            onChange={(next) => { setForm({ ...form, actions: next }) }}
          />

          <div className="flex items-center gap-2">
            <Button type="submit" loading={save.isPending} disabled={!ruleReady(form)}>
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
          </div>
        </form>
      ) : null}
    </Card>
  )
}

/** 목록 줄에 조치를 한 줄로. 이름은 규칙이 함께 들고 온다. */
function describeAction(
  action: AutomationAction,
  names: Record<string, string>,
  t: TFunction<['desk', 'common']>,
): string {
  if (action.kind === 'set_priority') {
    return t('desk:automation.action.setPriorityTo', { priority: action.priority })
  }
  if (action.kind === 'assign') {
    return t('desk:automation.action.assignTo', {
      who: action.user_id ? (names[action.user_id] ?? action.user_id) : '',
    })
  }
  const label = action.canned_response_id
    ? (names[action.canned_response_id] ?? action.canned_response_id)
    : ''
  return action.kind === 'add_note'
    ? t('desk:automation.action.noteWith', { canned: label })
    : t('desk:automation.action.replyWith', { canned: label })
}

/**
 * 조건 줄들. 값 칸은 **항목의 종류마다 다르게** 그린다.
 *
 * 전부 글자 입력으로 두면 `is_internal` 에 `"true"` 를, `state_category` 에
 * `"inprogress"` 를 적을 수 있다. 서버가 거절하지만, 고를 수 있는 것을
 * 보여 주는 편이 거절보다 먼저 온다 — 멈춤 상태 고르기와 같은 판단이다.
 */
function Conditions({
  rows,
  targets,
  onChange,
}: {
  rows: AutomationCondition[]
  targets: AutomationTargets | undefined
  onChange: (next: AutomationCondition[]) => void
}) {
  const { t } = useTranslation(['desk', 'issues', 'common'])
  const replace = (index: number, row: AutomationCondition) => {
    onChange(rows.map((current, at) => (at === index ? row : current)))
  }

  /** 고를 수 있는 것들. `null` 이면 글자로 적는다. */
  const optionsFor = (field: AutomationField): { value: string; label: string }[] | null => {
    switch (FIELD_KINDS[field]) {
      case 'int':
        return PRIORITIES.map((value) => ({ value: String(value), label: String(value) }))
      case 'bool':
        return [
          { value: 'true', label: t('common:yes') },
          { value: 'false', label: t('common:no') },
        ]
      case 'channel':
        return CHANNELS.map((value) => ({
          value,
          label: t(`desk:agent.channel.${value}`),
        }))
      case 'category':
        return CATEGORIES.map((value) => ({
          value,
          label: t(`issues:category.${value}`),
        }))
      case 'uuid': {
        const list =
          field === 'request_type_id' ? targets?.request_types : targets?.organizations
        return (list ?? []).map((row) => ({ value: row.id, label: row.name }))
      }
      default:
        return null
    }
  }

  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-xs font-medium text-fg">{t('desk:automation.conditions')}</legend>
      {/* **AND 라고 적어 둔다.** OR 을 찾다 못 찾은 사람이 "안 되는 것" 과
          "일부러 없는 것" 을 구별할 수 있어야 한다. */}
      <p className="text-xs text-muted">{t('desk:automation.conditionsHint')}</p>

      <ul className="flex flex-col gap-1">
        {rows.map((row, index) => {
          const options = optionsFor(row.field)
          const many = row.op === 'in'
          const chosen = Array.isArray(row.value) ? row.value.map(String) : [String(row.value)]
          return (
            <li key={`${row.field}-${String(index)}`} className="flex flex-wrap items-end gap-2">
              <Select
                label={t('desk:automation.field')}
                value={row.field}
                onChange={(event) => {
                  // **항목을 바꾸면 비교와 값을 새로 맞춘다.** 남겨 두면
                  // `priority>=4` 에서 `is_internal>=4` 가 되고, 서버가
                  // 거절하거나(지금) 조용히 안 맞는다(예전).
                  replace(index, freshCondition(event.target.value as AutomationField))
                }}
              >
                {FIELDS.map((field) => (
                  <option key={field} value={field}>
                    {t(`desk:automation.field.${field}`)}
                  </option>
                ))}
              </Select>
              <Select
                label={t('desk:automation.operator')}
                value={row.op}
                onChange={(event) => {
                  const op = event.target.value as AutomationOperator
                  replace(index, { ...row, op, value: castValue(row.field, op, row.value) })
                }}
              >
                {OPS_FOR_KIND[FIELD_KINDS[row.field]].map((op) => (
                  <option key={op} value={op}>
                    {t(`desk:automation.operator.${op}`)}
                  </option>
                ))}
              </Select>

              {options === null ? (
                <Field
                  label={t('desk:automation.value')}
                  className="w-40"
                  value={String(row.value)}
                  onChange={(event) => { replace(index, { ...row, value: event.target.value }) }}
                />
              ) : (
                <Select
                  label={t('desk:automation.value')}
                  {...(many ? { multiple: true } : {})}
                  value={many ? chosen : (chosen[0] ?? '')}
                  onChange={(event) => {
                    const picked = many
                      ? [...event.target.selectedOptions].map((option) => option.value)
                      : [event.target.value]
                    replace(index, { ...row, value: readValues(row.field, row.op, picked) })
                  }}
                >
                  {/* 여러 개 고르기에는 빈 선택지를 두지 않는다 — 빈 값을
                      함께 고르면 서버가 UUID 가 아니라며 거절한다. */}
                  {many ? null : <option value="">{t('desk:automation.chooseValue')}</option>}
                  {options.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              )}

              <Button
                type="button"
                variant="ghost"
                className="text-xs"
                onClick={() => { onChange(rows.filter((_, at) => at !== index)) }}
              >
                {t('common:action.delete')}
              </Button>
            </li>
          )
        })}
      </ul>

      <div>
        <Button
          type="button"
          variant="ghost"
          className="text-xs"
          onClick={() => { onChange([...rows, NEW_CONDITION]) }}
        >
          {t('desk:automation.addCondition')}
        </Button>
      </div>
    </fieldset>
  )
}

/**
 * 항목을 바꿨을 때의 새 조건. 종류에 맞는 비교와 값으로 시작한다.
 *
 * 고르는 항목(`uuid`)은 **비워 둔다.** 첫째를 미리 고르면, 관리자가 값을
 * 안 건드린 규칙이 우연히 첫째를 겨냥한 채 저장된다. 비어 있으면 저장이
 * 막히고, 무엇을 골라야 하는지가 화면에 남는다.
 */
export function freshCondition(field: AutomationField): AutomationCondition {
  const kind = FIELD_KINDS[field]
  const op = OPS_FOR_KIND[kind][0] ?? 'eq'
  if (kind === 'int') return { field, op, value: 3 }
  if (kind === 'bool') return { field, op, value: true }
  if (kind === 'channel') return { field, op, value: 'portal' }
  if (kind === 'category') return { field, op, value: 'todo' }
  return { field, op, value: '' }
}

/** 비교를 바꿨을 때의 값. 종류가 안 맞으면 서버가 거절한다. */
function castValue(
  field: AutomationField,
  op: AutomationOperator,
  value: AutomationCondition['value'],
): AutomationCondition['value'] {
  const first = Array.isArray(value) ? value[0] : value
  if (op === 'in') return readValues(field, op, [String(first ?? '')])
  const one = readValues(field, op, [String(first ?? '')])
  return Array.isArray(one) ? (one[0] ?? '') : one
}

/**
 * 고른 글자를 서버가 받는 종류로. `select` 는 언제나 문자열을 준다.
 *
 * 우선순위를 `"4"` 로 보내면 서버가 정수를 요구하며 거절하고, `is_internal`
 * 을 `"true"` 로 보내면 예전에는 저장되고 한 번도 안 걸렸다.
 */
function readValues(
  field: AutomationField,
  op: AutomationOperator,
  picked: string[],
): AutomationCondition['value'] {
  const kind = FIELD_KINDS[field]
  const one = (raw: string): string | number | boolean => {
    if (kind === 'int') return Number(raw)
    if (kind === 'bool') return raw === 'true'
    return raw
  }
  if (op !== 'in') return one(picked[0] ?? '')
  // 빈 것은 버린다. 하나라도 UUID 가 아니면 서버가 규칙 전체를 거절한다.
  const list = picked.filter((raw) => raw !== '').map(one)
  return list as (string | number)[]
}


function Actions({
  rows,
  names,
  canned,
  onChange,
}: {
  rows: AutomationAction[]
  names: Record<string, string>
  canned: { id: string; name: string }[]
  onChange: (next: AutomationAction[]) => void
}) {
  const { t } = useTranslation(['desk', 'common'])
  const [picking, setPicking] = useState<number | null>(null)
  const replace = (index: number, row: AutomationAction) => {
    onChange(rows.map((current, at) => (at === index ? row : current)))
  }

  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-xs font-medium text-fg">{t('desk:automation.actions')}</legend>
      <p className="text-xs text-muted">{t('desk:automation.actionsHint')}</p>

      <ul className="flex flex-col gap-1">
        {rows.map((row, index) => (
          <li key={`${row.kind}-${String(index)}`} className="flex flex-wrap items-end gap-2">
            <Select
              label={t('desk:automation.action')}
              value={row.kind}
              onChange={(event) => {
                // 종류를 바꾸면 **다른 종류의 값을 버린다.** 남겨 두면 서버가
                // 모르는 항목으로 거절한다.
                const kind = event.target.value as AutomationAction['kind']
                replace(index, kind === 'set_priority' ? { kind, priority: 5 } : { kind })
              }}
            >
              <option value="set_priority">{t('desk:automation.action.set_priority')}</option>
              <option value="assign">{t('desk:automation.action.assign')}</option>
              <option value="reply_with_canned">
                {t('desk:automation.action.reply_with_canned')}
              </option>
              <option value="add_note">{t('desk:automation.action.add_note')}</option>
            </Select>

            {row.kind === 'set_priority' ? (
              <Select
                label={t('desk:automation.toPriority')}
                value={String(row.priority ?? 5)}
                onChange={(event) => {
                  replace(index, { ...row, priority: Number(event.target.value) })
                }}
              >
                {[1, 2, 3, 4, 5].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
            ) : row.kind === 'assign' ? (
              <div className="flex flex-col gap-1">
                <span className="text-xs text-muted">
                  {t('desk:automation.assignee')}:{' '}
                  {row.user_id ? (names[row.user_id] ?? row.user_id) : '—'}
                </span>
                {picking === index ? (
                  <PersonPicker
                    label={t('desk:automation.assignee')}
                    onPick={(userId) => {
                      replace(index, { ...row, user_id: userId })
                      setPicking(null)
                    }}
                  />
                ) : (
                  <Button
                    type="button"
                    variant="ghost"
                    className="text-xs"
                    onClick={() => { setPicking(index) }}
                  >
                    {t('desk:automation.pickPerson')}
                  </Button>
                )}
              </div>
            ) : (
              <Select
                label={t('desk:automation.canned')}
                value={row.canned_response_id ?? ''}
                onChange={(event) => {
                  replace(index, { ...row, canned_response_id: event.target.value })
                }}
              >
                <option value="">{t('desk:automation.chooseCanned')}</option>
                {canned.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}

            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => { onChange(rows.filter((_, at) => at !== index)) }}
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
          onClick={() => { onChange([...rows, NEW_ACTION]) }}
        >
          {t('desk:automation.addAction')}
        </Button>
      </div>
    </fieldset>
  )
}
