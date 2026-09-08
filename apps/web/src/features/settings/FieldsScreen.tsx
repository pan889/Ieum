/**
 * 커스텀 필드 정의.
 *
 * 여기가 `python -m ieum.cli seed-fields` 를 대신하는 자리다 — 정의를 넣는
 * 화면이 없어서 데모 스크립트가 유일한 길이었다.
 *
 * **키와 종류는 만들 때만 정한다.** 키는 IQL 식별자이자 값의 주소이고, 종류는
 * 값의 해석을 정한다 — 나중에 바꾸면 이미 저장된 값이 어긋난다. 그래서 편집
 * 폼에 그 둘이 없고, 왜 없는지 적어 둔다.
 *
 * 지우면 **값도 함께 사라진다.** 몇 개가 사라지는지 누르기 전에 보여 준다.
 *
 * `FIELD_MANAGE` 는 step-up 대상이다. 2FA 없는 관리자는 거절을 본다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { FieldDefinitionAdmin } from '@ieum/api-client'

import { SettingsNav } from '@/features/settings/SettingsNav'
import { fieldsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Checkbox, Field, Select } from '@/shared/ui/primitives'

/** data-model.md 의 커스텀 필드 9종. 서버의 `FIELD_KINDS` 와 같은 순서다. */
const KINDS = [
  'text',
  'number',
  'date',
  'select',
  'multi_select',
  'user',
  'version',
  'bool',
  'url',
] as const

type Kind = (typeof KINDS)[number]

/** 선택지를 요구하는 종류. 빈 목록이면 채울 수 없는 필드가 된다. */
const NEEDS_OPTIONS: readonly Kind[] = ['select', 'multi_select']

const EMPTY = { key: '', name: '', kind: 'text' as Kind, options: '', is_required: false }

/** 쉼표로 적은 선택지를 목록으로. 빈 항목은 버린다. */
function optionList(value: string): string[] {
  return value
    .split(',')
    .map((option) => option.trim())
    .filter(Boolean)
}

export function FieldsScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY)

  const fields = useQuery({ queryKey: ['fields'], queryFn: () => fieldsApi.list() })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['fields'] })

  const create = useMutation({
    mutationFn: () =>
      fieldsApi.create({
        key: form.key.trim().toLowerCase(),
        name: form.name,
        kind: form.kind,
        is_required: form.is_required,
        config: NEEDS_OPTIONS.includes(form.kind) ? { options: optionList(form.options) } : {},
      }),
    onSuccess: async () => {
      setAdding(false)
      setForm(EMPTY)
      await refresh()
    },
  })

  const rows = fields.data ?? []

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('admin:fields.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('admin:fields.description')}</p>
        </div>
        <Button
          className="ml-auto shrink-0 text-xs"
          variant={adding ? 'ghost' : 'primary'}
          onClick={() => { setAdding(!adding) }}
        >
          {adding ? t('common:action.cancel') : t('admin:fields.add')}
        </Button>
      </header>

      {fields.isError ? <Alert>{describeError(fields.error)}</Alert> : null}
      {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}

      {adding ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              create.mutate()
            }}
          >
            <div className="flex flex-wrap items-end gap-3">
              <Field
                label={t('admin:fields.key')}
                hint={t('admin:fields.keyHint')}
                required
                value={form.key}
                onChange={(event) => { setForm({ ...form, key: event.target.value }) }}
              />
              <Field
                label={t('admin:fields.name')}
                required
                value={form.name}
                onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
              />
              <Select
                label={t('admin:fields.kind')}
                value={form.kind}
                onChange={(event) => {
                  setForm({ ...form, kind: event.target.value as Kind })
                }}
              >
                {KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {t(`admin:fields.kind.${kind}`)}
                  </option>
                ))}
              </Select>
            </div>

            {NEEDS_OPTIONS.includes(form.kind) ? (
              <Field
                label={t('admin:fields.options')}
                hint={t('admin:fields.optionsHint')}
                required
                value={form.options}
                onChange={(event) => { setForm({ ...form, options: event.target.value }) }}
              />
            ) : null}

            <Checkbox
              label={t('admin:fields.required')}
              checked={form.is_required}
              onChange={(event) => {
                setForm({ ...form, is_required: event.target.checked })
              }}
            />

            <Button type="submit" className="self-start" loading={create.isPending}>
              {t('admin:fields.save')}
            </Button>
          </form>
        </Card>
      ) : null}

      <ul className="flex flex-col gap-2" aria-label={t('admin:fields.title')}>
        {rows.map((definition) => (
          <li key={definition.id}>
            <FieldCard definition={definition} />
          </li>
        ))}
      </ul>

      {!fields.isPending && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('admin:fields.empty')}</p>
      ) : null}
    </section>
  )
}

function FieldCard({ definition }: { definition: FieldDefinitionAdmin }) {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [name, setName] = useState(definition.name)
  const [options, setOptions] = useState(
    Array.isArray(definition.config['options'])
      ? (definition.config['options'] as unknown[]).map(String).join(', ')
      : '',
  )

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['fields'] })
  const needsOptions = NEEDS_OPTIONS.includes(definition.kind as Kind)

  const save = useMutation({
    mutationFn: () =>
      fieldsApi.update(definition.id, {
        name,
        ...(needsOptions ? { config: { options: optionList(options) } } : {}),
      }),
    onSuccess: async () => {
      setEditing(false)
      await refresh()
    },
  })

  const toggleRequired = useMutation({
    mutationFn: () => fieldsApi.update(definition.id, { is_required: !definition.is_required }),
    onSuccess: () => { void refresh() },
  })

  const remove = useMutation({
    mutationFn: () => fieldsApi.remove(definition.id),
    onSuccess: () => { void refresh() },
  })

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="truncate text-sm font-medium">
            {definition.name}
            <span className="ml-2 align-middle">
              <Badge>{t(`admin:fields.kind.${definition.kind}`)}</Badge>
            </span>
            {definition.is_required ? (
              <span className="ml-1 align-middle">
                <Badge tone="info">{t('admin:fields.requiredBadge')}</Badge>
              </span>
            ) : null}
          </p>
          <p className="truncate text-xs text-muted">
            {/* 키를 보여 준다. IQL 에 그대로 쓰는 값이라 화면에 없으면
                질의를 쓸 수 없다. */}
            <code>{definition.key}</code> ·{' '}
            {definition.project_id === null && definition.issue_type_id === null
              ? t('admin:fields.everywhere')
              : t('admin:fields.limited')}{' '}
            · {t('admin:fields.valueCount', { count: definition.value_count })}
          </p>
        </div>

        <div className="ml-auto flex shrink-0 flex-wrap gap-1">
          <Button
            variant="ghost"
            className="text-xs"
            aria-label={t('admin:fields.requiredToggleLabel', { name: definition.name })}
            loading={toggleRequired.isPending}
            onClick={() => { toggleRequired.mutate() }}
          >
            {definition.is_required ? t('admin:fields.makeOptional') : t('admin:fields.makeRequired')}
          </Button>
          <Button
            variant="ghost"
            className="text-xs"
            aria-label={t('admin:fields.editLabel', { name: definition.name })}
            aria-expanded={editing}
            onClick={() => { setEditing(!editing) }}
          >
            {t('admin:fields.edit')}
          </Button>
          {confirming ? (
            <>
              <Button
                variant="secondary"
                className="text-xs"
                aria-label={t('admin:fields.confirmDeleteLabel', { name: definition.name })}
                loading={remove.isPending}
                onClick={() => { remove.mutate() }}
              >
                {t('common:action.confirm')}
              </Button>
              <Button variant="ghost" className="text-xs" onClick={() => { setConfirming(false) }}>
                {t('common:action.cancel')}
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              className="text-xs"
              aria-label={t('admin:fields.deleteLabel', { name: definition.name })}
              onClick={() => { setConfirming(true) }}
            >
              {t('common:action.delete')}
            </Button>
          )}
        </div>
      </div>

      {/* 값이 함께 사라진다. 개수를 보여 준다 — "필드만 숨는다" 고 읽으면
          그게 사고다. */}
      {confirming ? (
        <p className="text-sm text-muted">
          {t('admin:fields.deleteWarning', { count: definition.value_count })}
        </p>
      ) : null}

      {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
      {toggleRequired.isError ? <Alert>{describeError(toggleRequired.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {editing ? (
        <form
          className="flex flex-col gap-3 border-t border-border pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <Field
            label={t('admin:fields.name')}
            required
            value={name}
            onChange={(event) => { setName(event.target.value) }}
          />
          {needsOptions ? (
            <Field
              label={t('admin:fields.options')}
              hint={t('admin:fields.optionsHint')}
              required
              value={options}
              onChange={(event) => { setOptions(event.target.value) }}
            />
          ) : null}
          {/* 키와 종류가 왜 없는지 적는다. */}
          <p className="text-sm text-muted">{t('admin:fields.immutableHint')}</p>
          <Button type="submit" className="self-start" loading={save.isPending}>
            {t('common:action.save')}
          </Button>
        </form>
      ) : null}
    </Card>
  )
}
