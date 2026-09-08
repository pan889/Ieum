/**
 * 포털 폼의 입력 하나. **내부 위젯을 재사용하지 않는다.**
 *
 * `features/issues/CustomField` 는 `user`·`version` 종류에서 사람 목록과
 * 릴리스 목록을 불러온다. 고객에게 그 목록을 보여 주는 것은 내부 데이터를
 * 내주는 일이고, 애초에 고객이 담당자나 수정 버전을 고르는 모델이 아니다.
 * 그래서 서버가 그 두 종류의 매핑을 거절하고(`desk.field_kind_not_on_forms`),
 * 여기서는 그 종류를 아예 그리지 않는다 — 둘 중 하나만 있으면 어긋난다.
 */

import { useTranslation } from 'react-i18next'

import type { PortalFormField } from '@ieum/api-client'

import { Checkbox, Chip, Field, Select, Textarea } from '@/shared/ui/primitives'

export type AnswerValue = string | number | boolean | string[] | null

function optionsOf(field: PortalFormField): string[] {
  const raw = field.config['options']
  return Array.isArray(raw) ? raw.filter((o): o is string => typeof o === 'string') : []
}

function asText(value: AnswerValue): string {
  if (value === null) return ''
  if (typeof value === 'boolean') return value ? 'true' : ''
  if (Array.isArray(value)) return value.join(', ')
  return String(value)
}

function asList(value: AnswerValue): string[] {
  return Array.isArray(value) ? value : []
}

export function PortalField({
  field,
  value,
  onChange,
}: {
  field: PortalFormField
  value: AnswerValue
  onChange: (value: AnswerValue) => void
}) {
  const { t } = useTranslation(['common'])
  // 라벨에 `*` 를 붙인다. `Field` 는 `required` 를 입력 요소에만 넘기므로
  // 브라우저와 스크린 리더는 알지만 **눈으로 보는 사람은 모른다.** 고객이
  // 채우는 폼에서 그 차이가 특히 크다 — 무엇이 남았는지 모른 채 버튼이
  // 눌리지 않는다.
  const common = {
    label: field.required ? `${field.label} *` : field.label,
    required: field.required,
    ...(field.help ? { hint: field.help } : {}),
  }

  switch (field.kind) {
    case 'markdown':
      return (
        <Textarea
          {...common}
          rows={8}
          value={asText(value)}
          onChange={(event) => {
            onChange(event.target.value || null)
          }}
        />
      )

    case 'number':
      return (
        <Field
          {...common}
          type="number"
          value={asText(value)}
          onChange={(event) => {
            const parsed = Number(event.target.value)
            onChange(event.target.value === '' || Number.isNaN(parsed) ? null : parsed)
          }}
        />
      )

    case 'date':
      return (
        <Field
          {...common}
          type="date"
          value={asText(value)}
          onChange={(event) => {
            onChange(event.target.value || null)
          }}
        />
      )

    case 'url':
      return (
        <Field
          {...common}
          type="url"
          value={asText(value)}
          onChange={(event) => {
            onChange(event.target.value || null)
          }}
        />
      )

    case 'bool':
      return (
        // `common` 을 그대로 넘기지 않는다. 거기엔 `required` 와 라벨의 `*`
        // 가 들어 있는데, 체크박스에서 `required` 는 "켜야 보낼 수 있다" 는
        // 뜻이다. 서버도 화면의 미기입 검사도 참·거짓 항목에서는 **꺼짐도
        // 답**으로 친다(`value is None` 만 빈 것이다). 그대로 넘기면 끄고
        // 보내려는 사람이 브라우저에게 막힌다.
        <Checkbox
          label={field.label}
          {...(field.help ? { hint: field.help } : {})}
          checked={value === true}
          onChange={(event) => {
            onChange(event.target.checked)
          }}
        />
      )

    case 'select':
      return (
        <Select
          {...common}
          value={asText(value)}
          onChange={(event) => {
            onChange(event.target.value || null)
          }}
        >
          {/* 필수여도 빈 항목을 둔다. 없으면 첫 선택지가 이미 고른 것처럼
              보이고, 고객은 자기가 고르지 않은 값을 보낸다. */}
          <option value="">{t('common:action.choose')}</option>
          {optionsOf(field).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </Select>
      )

    case 'multi_select': {
      const chosen = asList(value)
      return (
        <fieldset className="flex flex-col gap-1">
          <legend className="text-xs font-medium text-muted">
            {field.label}
            {field.required ? ' *' : ''}
          </legend>
          {field.help ? <p className="text-xs text-muted">{field.help}</p> : null}
          <div className="flex flex-wrap gap-1">
            {optionsOf(field).map((option) => (
              <Chip
                key={option}
                pressed={chosen.includes(option)}
                onClick={() => {
                  const next = chosen.includes(option)
                    ? chosen.filter((o) => o !== option)
                    : [...chosen, option]
                  onChange(next.length > 0 ? next : null)
                }}
              >
                {option}
              </Chip>
            ))}
          </div>
        </fieldset>
      )
    }

    default:
      // `text` 와, 모르는 종류. 모르는 종류를 **빠뜨리지 않고** 글자 입력으로
      // 그린다 — 아무것도 안 그리면 필수 항목이 화면에 없는데 제출이 거절된다.
      return (
        <Field
          {...common}
          value={asText(value)}
          onChange={(event) => {
            onChange(event.target.value || null)
          }}
        />
      )
  }
}
