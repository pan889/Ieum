/**
 * 다른 도구에서 옮겨 오기.
 *
 * 두 단계다. **미리 보기가 먼저 서는 이유**는 이관이 되돌리기 번거로운
 * 일이기 때문이다 — 무엇이 들어오고 무엇이 안 들어오는지 보고 나서 누른다.
 *
 * 두 부름은 **같은 몸통**을 받는다. 화면이 보여 준 그대로를 실어야 하고,
 * 다른 것을 실으면 사람이 본 것과 들어간 것이 갈린다.
 */

import type { ApiClient } from './client'

/**
 * 소스의 낱말 하나를 우리 것에 이은 결과.
 *
 * `how` 를 화면이 반드시 구별해 그린다 — **짐작한 것(`rank`)과 이름이 맞은
 * 것(`name`)을 같게 보여 주면** 사람이 확인해야 할 자리를 지나친다.
 */
export interface ImportMatch {
  source: string
  target_id: string
  target_name: string
  how: 'name' | 'override' | 'rank' | 'unmatched'
}

/** 사람. 메일로만 잇는다. */
export interface ImportPerson {
  source_id: string
  name: string
  email: string
  user_id: string
  /** 빈 값이면 이었다. `no_email` 과 `unknown_email` 은 고치는 방법이 다르다. */
  reason: '' | 'no_email' | 'unknown_email'
}

/** 사람이 짝을 고를 때 보는 후보. `qualifier` 는 같은 이름을 가르는 꼬리다. */
export interface ImportChoice {
  id: string
  name: string
  qualifier: string
}

export interface ImportCounted {
  issues: number
  comments: number
  people: number
  /** 이미 옮겨 온 것. 다시 돌리면 이만큼은 안 늘어난다. */
  already_here: number
}

export interface ImportPreview {
  source_kind: string
  source_url: string
  project_key: string
  project_name: string
  taken_at: string
  adapter: string
  counted: ImportCounted
  /** 거짓이면 `blocking` 에 이유가 있다. 그때 적재 단추를 잠근다. */
  can_load: boolean
  blocking: string[]

  types: ImportMatch[]
  statuses: ImportMatch[]
  priorities: ImportMatch[]
  people: ImportPerson[]

  dangling_parents: string[]
  dangling_relations: string[]
  unknown_relation_kinds: string[]

  type_choices: ImportChoice[]
  status_choices: ImportChoice[]
}

export interface ImportLoaded {
  issues_created: number
  /** 이미 있어서 건너뛴 것. **다시 돌리면 이 값이 전부여야 한다.** */
  issues_skipped: number
  comments_created: number
  parents_linked: number
  relations_linked: number
  /** 못 옮긴 것. 화면이 접어 두면 안 된다 — 여기서 안 보면 아무 데도 안 남는다. */
  unmoved: string[]
}

/** 사람이 지어 준 짝. 소스의 낱말 → 우리 id(우선순위는 `'1'`~`'5'`). */
export interface ImportOverrides {
  types?: Record<string, string>
  statuses?: Record<string, string>
  priorities?: Record<string, string>
}

const IMPORTS = '/api/v1/imports'

function body(projectId: string, file: File, overrides?: ImportOverrides): FormData {
  const form = new FormData()
  form.append('project_id', projectId)
  form.append('file', file)
  // 빈 짝은 아예 안 보낸다. 서버가 빈 문자열을 "못 읽은 것" 과 구별해야
  // 하는 자리를 만들지 않는다.
  if (overrides && Object.keys(overrides).length > 0) {
    form.append('overrides', JSON.stringify(overrides))
  }
  return form
}

export function createImportsApi(client: ApiClient) {
  return {
    /** 무엇이 들어올지 본다. **아무것도 저장하지 않는다.** */
    preview: (projectId: string, file: File, overrides?: ImportOverrides) =>
      client.postForm<ImportPreview>(`${IMPORTS}/preview`, body(projectId, file, overrides)),

    /** 싣는다. **두 번 쳐도 안 늘어난다.** */
    load: (projectId: string, file: File, overrides?: ImportOverrides) =>
      client.postForm<ImportLoaded>(`${IMPORTS}/load`, body(projectId, file, overrides)),
  }
}

export type ImportsApi = ReturnType<typeof createImportsApi>
