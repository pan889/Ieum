/**
 * 컬럼 IQL 에서 "이 컬럼에 들어가려면 어떤 상태여야 하는가" 를 읽어낸다.
 *
 * 컬럼은 임의의 IQL 이므로 일반적으로는 알 수 없다. 그래서 **아는 모양만**
 * 인식하고 나머지는 null 을 돌려준다 — 호출부는 그때 사용자에게 전이를
 * 고르게 한다. 추측해서 자동으로 옮기면 엉뚱한 상태로 보내 놓고 성공한
 * 것처럼 보인다.
 */
export type ColumnTarget =
  | { kind: 'category'; values: string[] }
  | { kind: 'status'; values: string[] }
  | null

const EQ = /^(statusCategory|status)\s*=\s*(.+)$/i
const IN = /^(statusCategory|status)\s+IN\s*\((.+)\)$/i

function unquote(raw: string): string | null {
  const value = raw.trim()
  if (value.length === 0) return null
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    // 이스케이프가 들어 있으면 우리 몫이 아니다. 모른다고 답한다.
    if (value.slice(1, -1).includes('\\')) return null
    return value.slice(1, -1)
  }
  // 따옴표 없는 bareword. 괄호·쉼표가 섞이면 우리가 다룰 모양이 아니다.
  return /^[\w-]+$/.test(value) ? value : null
}

function kindOf(field: string): 'category' | 'status' {
  return field.toLowerCase() === 'statuscategory' ? 'category' : 'status'
}

export function columnTarget(iql: string): ColumnTarget {
  const trimmed = iql.trim()
  if (trimmed === '') return null

  const inMatch = IN.exec(trimmed)
  if (inMatch) {
    const values = (inMatch[2] as string).split(',').map(unquote)
    if (values.some((v) => v === null)) return null
    return { kind: kindOf(inMatch[1] as string), values: values as string[] }
  }

  const eqMatch = EQ.exec(trimmed)
  if (eqMatch) {
    const value = unquote(eqMatch[2] as string)
    return value === null ? null : { kind: kindOf(eqMatch[1] as string), values: [value] }
  }

  return null
}

export interface StateInfo {
  id: string
  name: string
  category: string
}

/** 이 상태가 그 컬럼에 들어가는가. target 이 null 이면 판단하지 않는다. */
export function stateFitsColumn(target: ColumnTarget, state: StateInfo | undefined): boolean {
  if (target === null || state === undefined) return false
  return target.kind === 'category'
    ? target.values.includes(state.category)
    : target.values.includes(state.name)
}
