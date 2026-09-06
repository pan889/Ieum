/** 커스텀 필드 위젯. 정의의 `kind` 로 입력 방식을 고른다. */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { FieldDefinition } from '@ieum/api-client'
import { Chip, Field, Select } from '@/shared/ui/primitives'

import {
  asInputText,
  asStringList,
  maxLengthOf,
  optionsOf,
  parseNumber,
  parseText,
  rangeOf,
  toggleOption,
  type FieldValue,
} from './customFields'
import { useUserSearch, useVersions } from './hooks'

export interface CustomFieldProps {
  definition: FieldDefinition
  value: FieldValue
  onChange: (value: FieldValue) => void
  /** user·version 선택지를 이 프로젝트 것으로 좁힌다. */
  projectId: string | null
  /** 상세 화면 사이드바처럼 좁은 자리에서 쓰는 조밀한 배치. */
  compact?: boolean
}

export function CustomField({
  definition,
  value,
  onChange,
  projectId,
  compact = false,
}: CustomFieldProps) {
  const common = {
    label: definition.name,
    required: definition.is_required,
    ...(definition.description ? { hint: definition.description } : {}),
  }
  const inputClass = compact ? 'py-1 text-xs' : undefined

  switch (definition.kind) {
    case 'number': {
      const { min, max } = rangeOf(definition)
      return (
        <Field
          {...common}
          type="number"
          className={inputClass}
          {...(min === undefined ? {} : { min })}
          {...(max === undefined ? {} : { max })}
          value={asInputText(value)}
          onChange={(e) => { onChange(parseNumber(e.target.value)) }}
        />
      )
    }

    case 'date':
      return (
        <Field
          {...common}
          type="date"
          className={inputClass}
          value={asInputText(value)}
          onChange={(e) => { onChange(parseText(e.target.value)) }}
        />
      )

    case 'url':
      return (
        <Field
          {...common}
          type="url"
          inputMode="url"
          placeholder="https://"
          className={inputClass}
          value={asInputText(value)}
          onChange={(e) => { onChange(parseText(e.target.value)) }}
        />
      )

    case 'bool':
      return <BoolField definition={definition} value={value === true} onChange={onChange} />

    case 'select':
      return (
        <Select
          {...common}
          className={inputClass}
          value={asInputText(value)}
          onChange={(e) => { onChange(e.target.value || null) }}
        >
          {/* 필수여도 빈 항목을 남긴다. 없으면 첫 선택지가 고른 적 없이
              골라진 것처럼 보인다. */}
          <option value="">—</option>
          {optionsOf(definition).map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
        </Select>
      )

    case 'multi_select':
      return <MultiSelectField definition={definition} value={asStringList(value)} onChange={onChange} />

    case 'user':
      return <UserField definition={definition} value={asInputText(value)} onChange={onChange} />

    case 'version':
      return (
        <VersionField
          definition={definition}
          value={asInputText(value)}
          onChange={onChange}
          projectId={projectId}
        />
      )

    default:
      // text 와, 아직 위젯이 없는 종류. 문자열로 받아 서버가 판정하게 둔다.
      return (
        <Field
          {...common}
          className={inputClass}
          maxLength={maxLengthOf(definition)}
          value={asInputText(value)}
          onChange={(e) => { onChange(parseText(e.target.value)) }}
        />
      )
  }
}

function BoolField({
  definition,
  value,
  onChange,
}: {
  definition: FieldDefinition
  value: boolean
  onChange: (value: FieldValue) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="flex items-center gap-2 text-sm font-medium text-fg">
        <input
          type="checkbox"
          className="size-4 rounded border-border accent-accent"
          checked={value}
          onChange={(e) => { onChange(e.target.checked) }}
        />
        {definition.name}
      </label>
      {definition.description ? (
        <p className="text-xs text-muted">{definition.description}</p>
      ) : null}
    </div>
  )
}

function MultiSelectField({
  definition,
  value,
  onChange,
}: {
  definition: FieldDefinition
  value: string[]
  onChange: (value: FieldValue) => void
}) {
  const options = optionsOf(definition)
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className="text-sm font-medium text-fg">{definition.name}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => (
          <Chip
            key={option}
            pressed={value.includes(option)}
            // 비어 있으면 null 로 보낸다. `[]` 을 저장하면 '값 없음'과
            // '빈 목록'이라는 구분 없는 두 상태가 생긴다.
            onClick={() => {
              const next = toggleOption(value, option)
              onChange(next.length > 0 ? next : null)
            }}
          >
            {option}
          </Chip>
        ))}
      </div>
      {definition.description ? (
        <p className="text-xs text-muted">{definition.description}</p>
      ) : null}
    </fieldset>
  )
}

function UserField({
  definition,
  value,
  onChange,
}: {
  definition: FieldDefinition
  value: string
  onChange: (value: FieldValue) => void
}) {
  const { t } = useTranslation(['issues'])
  const [query, setQuery] = useState('')
  const candidates = useUserSearch(query)

  // 저장된 사용자가 검색 결과에 없으면 선택이 조용히 풀린다. 그래서
  // 현재 값을 항상 선택지에 남겨 둔다.
  const items = candidates.data?.items ?? []
  const chosen = items.find((user) => user.id === value)

  return (
    <div className="flex flex-col gap-1.5">
      <Field
        label={definition.name}
        required={definition.is_required}
        {...(definition.description ? { hint: definition.description } : {})}
        placeholder={t('issues:detail.searchUsers')}
        value={query}
        onChange={(e) => { setQuery(e.target.value) }}
      />
      <Select
        aria-label={`${definition.name} — ${t('issues:detail.pick')}`}
        value={value}
        onChange={(e) => { onChange(e.target.value || null) }}
      >
        <option value="">{t('issues:detail.unassigned')}</option>
        {value && !chosen ? <option value={value}>{value}</option> : null}
        {items.map((user) => (
          <option key={user.id} value={user.id}>{user.display_name}</option>
        ))}
      </Select>
    </div>
  )
}

function VersionField({
  definition,
  value,
  onChange,
  projectId,
}: {
  definition: FieldDefinition
  value: string
  onChange: (value: FieldValue) => void
  projectId: string | null
}) {
  const versions = useVersions(projectId)
  const items = versions.data ?? []
  const known = items.some((version) => version.id === value)

  return (
    <Select
      label={definition.name}
      required={definition.is_required}
      {...(definition.description ? { hint: definition.description } : {})}
      value={value}
      onChange={(e) => { onChange(e.target.value || null) }}
    >
      <option value="">—</option>
      {/* 삭제된 버전을 가리키고 있어도 값이 조용히 사라지지 않게 남긴다. */}
      {value && !known ? <option value={value}>{value}</option> : null}
      {items.map((version) => (
        <option key={version.id} value={version.id}>{version.name}</option>
      ))}
    </Select>
  )
}
