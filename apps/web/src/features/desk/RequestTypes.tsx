/**
 * 요청 폼 편집기.
 *
 * **매핑을 손으로 적게 하지 않는다.** 서버는 "모든 비예약 필드에 매핑이
 * 있어야 한다" 를 강제하는데, 그것을 자유 입력으로 두면 오타 하나로 저장이
 * 거절되고 사람은 왜인지 모른다. 그래서 이 프로젝트·유형에 실제로 있는
 * 커스텀 필드만 선택 상자에 올린다.
 *
 * `user`·`version` 종류는 후보에서 **뺀다.** 선택지가 내부 데이터(사람 목록,
 * 릴리스 목록)라 고객에게 펼쳐 보일 수 없고, 서버도 그 매핑을 거절한다
 * (`desk.field_kind_not_on_forms`). 여기서 올려 두면 저장 버튼이 이유 없이
 * 실패하는 것처럼 보인다.
 *
 * **이슈 유형은 만들 때만 정한다.** 갈아 끼우면 그 유형에 뜨는 커스텀 필드가
 * 달라져 매핑이 통째로 무의미해지고, 이미 만들어진 티켓과 새 티켓이 서로 다른
 * 워크플로우를 탄다. 편집 폼에 그것이 없고, 왜 없는지 적어 둔다.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { FormFieldSpec, Portal, RequestType } from '@ieum/api-client'

import { deskApi, fieldsApi, issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field, Select } from '@/shared/ui/primitives'

/** 서버의 `FORBIDDEN_FORM_KINDS` 와 같아야 한다. */
const NOT_ON_FORMS = ['user', 'version']

/** 서버의 `RESERVED_FORM_KEYS`. 이슈 자신의 칸으로 가므로 매핑이 없다. */
const RESERVED = ['summary', 'description']

interface Draft {
  name: string
  issue_type_id: string
  description: string
  fields: (FormFieldSpec & { mapsTo: string })[]
}

function emptyDraft(): Draft {
  return {
    name: '',
    issue_type_id: '',
    description: '',
    // 요약은 서버가 요구한다. 없으면 저장이 거절되므로 처음부터 넣어 둔다 —
    // "왜 저장이 안 되나" 를 겪게 할 이유가 없다.
    fields: [
      { key: 'summary', label: '', required: true, mapsTo: '' },
      { key: 'description', label: '', required: false, mapsTo: '' },
    ],
  }
}

export function RequestTypes({
  portal,
  onChanged,
}: {
  portal: Portal
  onChanged: () => void
}) {
  const { t } = useTranslation(['desk', 'common'])
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<Draft>(emptyDraft)

  const types = useQuery({
    queryKey: ['portals', portal.id, 'request-types'],
    queryFn: () => deskApi.listRequestTypes(portal.id),
  })
  const issueTypes = useQuery({
    queryKey: ['issues', 'types', portal.project_id],
    queryFn: () => issuesApi.types(portal.project_id),
  })
  // 커스텀 필드 정의는 전역 목록을 받아 이 프로젝트·유형에 뜨는 것만 남긴다.
  // 서버가 이미 그렇게 걸러 주는 조회가 있지만 그건 `issue.view` 를 요구하고,
  // 이 화면의 권한은 `desk.portal.manage` 다.
  const definitions = useQuery({
    queryKey: ['fields'],
    queryFn: () => fieldsApi.list(),
  })

  const candidates = (definitions.data ?? []).filter((definition) => {
    if (NOT_ON_FORMS.includes(definition.kind)) return false
    if (definition.project_id && definition.project_id !== portal.project_id) return false
    if (
      definition.issue_type_id &&
      draft.issue_type_id &&
      definition.issue_type_id !== draft.issue_type_id
    ) {
      return false
    }
    return true
  })

  const create = useMutation({
    mutationFn: () =>
      deskApi.createRequestType(portal.id, {
        issue_type_id: draft.issue_type_id,
        name: draft.name,
        ...(draft.description ? { description: draft.description } : {}),
        form_schema: {
          fields: draft.fields.map((field) => ({
            key: field.key,
            label: field.label,
            required: field.required ?? false,
          })),
        },
        field_mapping: Object.fromEntries(
          draft.fields
            .filter((field) => !RESERVED.includes(field.key) && field.mapsTo)
            .map((field) => [field.key, field.mapsTo]),
        ),
      }),
    onSuccess: async () => {
      setAdding(false)
      setDraft(emptyDraft())
      await types.refetch()
      onChanged()
    },
  })

  const toggle = useMutation({
    mutationFn: (row: RequestType) =>
      deskApi.updateRequestType(row.id, { is_enabled: !row.is_enabled }),
    onSuccess: async () => {
      await types.refetch()
    },
  })

  const rows = types.data ?? []
  const firstIssueType = issueTypes.data?.[0]?.id ?? ''

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-3">
        <h2 className="text-sm font-semibold">{t('desk:requestTypes.title')}</h2>
        <p className="text-xs text-muted">{t('desk:requestTypes.description')}</p>
        <Button
          className="ml-auto shrink-0 text-xs"
          variant={adding ? 'ghost' : 'primary'}
          onClick={() => {
            setAdding(!adding)
            if (!adding) setDraft({ ...emptyDraft(), issue_type_id: firstIssueType })
          }}
        >
          {adding ? t('common:action.cancel') : t('desk:requestTypes.add')}
        </Button>
      </div>

      {types.isError ? <Alert>{describeError(types.error)}</Alert> : null}
      {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
      {toggle.isError ? <Alert>{describeError(toggle.error)}</Alert> : null}

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
                label={t('desk:requestTypes.name')}
                required
                value={draft.name}
                onChange={(event) => { setDraft({ ...draft, name: event.target.value }) }}
              />
              <Select
                label={t('desk:requestTypes.issueType')}
                hint={t('desk:requestTypes.issueTypeFixed')}
                required
                value={draft.issue_type_id}
                onChange={(event) => { setDraft({ ...draft, issue_type_id: event.target.value }) }}
              >
                <option value="">{t('common:action.choose')}</option>
                {(issueTypes.data ?? []).map((type) => (
                  <option key={type.id} value={type.id}>
                    {type.name}
                  </option>
                ))}
              </Select>
            </div>

            <fieldset className="flex flex-col gap-2">
              <legend className="text-xs font-medium text-muted">
                {t('desk:requestTypes.fields')}
              </legend>
              {draft.fields.map((field, index) => {
                const reserved = RESERVED.includes(field.key)
                return (
                  <div
                    key={`${field.key}-${String(index)}`}
                    className="flex flex-wrap items-end gap-2 rounded border border-border p-2"
                  >
                    <Field
                      label={t('desk:requestTypes.fieldKey')}
                      className="w-32"
                      required
                      readOnly={reserved}
                      value={field.key}
                      onChange={(event) => {
                        const next = [...draft.fields]
                        next[index] = { ...field, key: event.target.value }
                        setDraft({ ...draft, fields: next })
                      }}
                    />
                    <Field
                      label={t('desk:requestTypes.fieldLabel')}
                      required
                      value={field.label}
                      onChange={(event) => {
                        const next = [...draft.fields]
                        next[index] = { ...field, label: event.target.value }
                        setDraft({ ...draft, fields: next })
                      }}
                    />
                    {reserved ? (
                      <span className="pb-2 text-xs text-muted">
                        {t('desk:requestTypes.reserved')}
                      </span>
                    ) : (
                      <Select
                        label={t('desk:requestTypes.fieldMapping')}
                        hint={t('desk:requestTypes.fieldMappingHint')}
                        required
                        value={field.mapsTo}
                        onChange={(event) => {
                          const next = [...draft.fields]
                          next[index] = { ...field, mapsTo: event.target.value }
                          setDraft({ ...draft, fields: next })
                        }}
                      >
                        <option value="">{t('desk:requestTypes.unmapped')}</option>
                        {candidates.map((definition) => (
                          <option key={definition.key} value={definition.key}>
                            {definition.name} ({definition.kind})
                          </option>
                        ))}
                      </Select>
                    )}
                    <label className="flex items-center gap-1 pb-2 text-xs">
                      <input
                        type="checkbox"
                        // 요약은 서버가 언제나 필수로 다룬다. 여기서 끌 수
                        // 있게 두면 화면과 서버가 어긋난다.
                        disabled={field.key === 'summary'}
                        checked={field.key === 'summary' || field.required === true}
                        onChange={(event) => {
                          const next = [...draft.fields]
                          next[index] = { ...field, required: event.target.checked }
                          setDraft({ ...draft, fields: next })
                        }}
                      />
                      {t('desk:requestTypes.fieldRequired')}
                    </label>
                    {reserved ? null : (
                      <Button
                        type="button"
                        variant="ghost"
                        className="pb-2 text-xs"
                        onClick={() => {
                          setDraft({
                            ...draft,
                            fields: draft.fields.filter((_, i) => i !== index),
                          })
                        }}
                      >
                        {t('desk:requestTypes.removeField')}
                      </Button>
                    )}
                  </div>
                )
              })}
              <Button
                type="button"
                variant="ghost"
                className="self-start text-xs"
                onClick={() => {
                  setDraft({
                    ...draft,
                    fields: [
                      ...draft.fields,
                      { key: '', label: '', required: false, mapsTo: '' },
                    ],
                  })
                }}
              >
                {t('desk:requestTypes.addField')}
              </Button>
            </fieldset>

            <Button type="submit" className="self-start" disabled={create.isPending}>
              {t('common:action.create')}
            </Button>
          </form>
        </Card>
      ) : null}

      {types.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:requestTypes.empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-1">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-wrap items-baseline gap-2 text-sm">
            <span className="font-medium">{row.name}</span>
            <span className="text-xs text-muted">{row.issue_type_name}</span>
            {row.is_enabled ? null : (
              <Badge tone="danger">{t('desk:requestTypes.disabled')}</Badge>
            )}
            <span className="text-xs text-muted">
              {t('desk:requestTypes.ticketCount', { count: row.ticket_count })}
            </span>
            <Button
              className="ml-auto text-xs"
              variant="ghost"
              onClick={() => { toggle.mutate(row) }}
            >
              {row.is_enabled ? t('desk:requestTypes.disable') : t('desk:requestTypes.enable')}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}
