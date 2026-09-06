/**
 * 커스텀 필드 값 다루기. 렌더링은 `CustomField.tsx` 가 한다.
 *
 * 값은 서버가 받는 모양 그대로 들고 다닌다 (JSONB 왕복). DOM 은 문자열만
 * 주므로 종류별로 여기서 바꿔 준다 — 숫자 필드에 `"3"` 을 보내면 서버
 * 밸리데이터(`fields.py`)가 거절한다.
 */

import type { FieldDefinition } from '@ieum/api-client'

/** 서버가 받는 모양. `null` 은 '비움'이다. */
export type FieldValue = string | number | boolean | string[] | null

export const FIELD_KINDS = [
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

export type FieldKind = (typeof FIELD_KINDS)[number]

export function isKnownKind(kind: string): kind is FieldKind {
  return (FIELD_KINDS as readonly string[]).includes(kind)
}

/** select·multi_select 의 선택지. 정의가 망가져 있으면 빈 목록. */
export function optionsOf(definition: FieldDefinition): string[] {
  const raw = definition.config['options']
  return Array.isArray(raw) ? raw.map((option) => String(option)) : []
}

function configNumber(definition: FieldDefinition, key: string): number | undefined {
  const raw = definition.config[key]
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : undefined
}

/** number 필드의 min/max. 서버와 같은 키를 본다. */
export function rangeOf(definition: FieldDefinition): { min?: number; max?: number } {
  const min = configNumber(definition, 'min')
  const max = configNumber(definition, 'max')
  return { ...(min === undefined ? {} : { min }), ...(max === undefined ? {} : { max }) }
}

/** text 필드의 최대 길이. 서버 기본값과 맞춘다. */
export function maxLengthOf(definition: FieldDefinition): number {
  return configNumber(definition, 'max_length') ?? 4000
}

/**
 * 숫자 입력 파싱. 빈 칸은 '비움'이다.
 *
 * `0` 을 null 로 접으면 안 된다 — 0 은 값이다. 그래서 빈 문자열만 null 로
 * 본다. `<input type="number">` 는 파싱 불가한 입력도 빈 문자열로 준다.
 */
export function parseNumber(raw: string): number | null {
  const trimmed = raw.trim()
  if (trimmed === '') return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

/** 문자열 입력 파싱. 공백만 남으면 '비움'이다. */
export function parseText(raw: string): string | null {
  const trimmed = raw.trim()
  return trimmed === '' ? null : trimmed
}

/**
 * 다중 선택 토글. **고른 순서를 유지한다.**
 *
 * 서버도 순서를 보존하므로(`fields.py` `_multi_select`) 여기서 정렬해
 * 버리면 저장 직후 화면과 왕복 후 화면이 달라진다.
 */
export function toggleOption(current: string[], option: string): string[] {
  return current.includes(option)
    ? current.filter((chosen) => chosen !== option)
    : [...current, option]
}

/** 저장된 값을 다중 선택 상태로 읽는다. 깨진 값은 빈 목록으로 본다. */
export function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

/** 텍스트 입력 칸에 넣을 문자열. null·객체는 빈 칸이다. */
export function asInputText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  return ''
}

/**
 * 보낼 값만 골라낸다.
 *
 * 안 바뀐 필드까지 실어 보내면 서버가 매번 존재 검증(user·version)을 다시
 * 돌고, 값이 같아 이력에는 안 남지만 왕복만 무거워진다.
 */
export function changedFields(
  before: Record<string, unknown>,
  after: Record<string, FieldValue>,
): Record<string, FieldValue> {
  const patch: Record<string, FieldValue> = {}
  for (const [key, value] of Object.entries(after)) {
    if (!sameValue(before[key], value)) patch[key] = value
  }
  return patch
}

function sameValue(left: unknown, right: unknown): boolean {
  // 저장 전 '비움'은 null, 저장된 적 없는 키는 undefined 다. 같은 뜻이다.
  if (left === null || left === undefined) return right === null || right === undefined
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((item, index) => item === right[index])
  }
  return left === right
}

/**
 * 표시용 문자열. `resolve` 는 user·version 의 UUID 를 이름으로 바꾼다
 * (모르면 undefined 를 주면 되고, 그때는 UUID 를 그대로 보여준다).
 */
export function displayValue(
  kind: string,
  value: unknown,
  resolve?: (kind: string, id: string) => string | undefined,
): string {
  if (value === null || value === undefined || value === '') return ''
  if (kind === 'bool') return value === true ? '✓' : '—'
  if (kind === 'multi_select') return asStringList(value).join(', ')
  if ((kind === 'user' || kind === 'version') && typeof value === 'string') {
    return resolve?.(kind, value) ?? value
  }
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return JSON.stringify(value)
}
