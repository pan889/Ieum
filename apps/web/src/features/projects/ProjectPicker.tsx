/**
 * 프로젝트를 찾아 고른다. **목록을 통째로 드롭다운에 넣지 않는다.**
 *
 * `PersonPicker` 와 같은 이유이고, 같은 실수를 세 번 했다: 포털 화면의
 * 프로젝트 선택 상자를 `projectsApi.list({ limit: 100 })` 로 채웠고, 개발
 * DB 의 프로젝트가 백 개를 넘자 E2E 가 그 자리에서 멈췄다 — 방금 만든
 * 프로젝트가 옵션에 없었다. 세 번째는 커서를 끝까지 따라가되 상한(2000개)을
 * 둔 코드였다 — 개발 DB 가 2334개가 되자 **똑같이** 멈췄다. 끝까지 받는 것도
 * 상한이 있으면 결국 잘린 목록이다. `ux-principles.md` 4절이 그것을 금지하고
 * 있는데 세 번 어겼고, 세 번째 뒤에 그 절에서 "끝까지 받는다" 갈래를 뺐다.
 *
 * 여기서는 **고른 것을 계속 보여 줘야** 한다는 점이 사람 피커와 다르다.
 * 그래서 고르면 검색이 접히고 "지금 이것" 이 남는다.
 *
 * 데스크 화면들이 쓰던 것을 `features/settings` 에서 여기로 옮겼다. 이슈
 * 생성·목록 필터·보드가 같은 것을 쓰게 되면서 특정 기능의 소유물이 아니게
 * 됐다.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Project } from '@ieum/api-client'

import { projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

const LIMIT = 20

export function ProjectPicker({
  label,
  chosen,
  onPick,
  emptyLabel,
  unresolvedLabel,
  onClear,
}: {
  label: string
  chosen: Project | null
  onPick: (project: Project) => void
  /**
   * **아무것도 안 골랐을 때**의 이름. 목록 필터에서는 "전체 프로젝트" 다 —
   * 거기서는 안 고른 것이 빠뜨린 것이 아니라 하나의 뜻이기 때문이다.
   * 되돌리는 버튼도 이 이름으로 부른다.
   */
  emptyLabel?: string
  /**
   * 골라져 **있는데** 이름을 아직 못 읽었을 때 대신 쓸 글자(보통 키).
   *
   * 필터는 URL 에서 키만 받아 온다. 그 사이에 `emptyLabel`("전체 프로젝트")
   * 을 그리면 목록은 걸러져 있는데 화면만 아니라고 말하는 셈이다.
   */
  unresolvedLabel?: string | undefined
  /** 주면 `emptyLabel` 상태로 되돌리는 버튼을 낸다. 필터가 쓴다. */
  onClear?: (() => void) | undefined
}) {
  const { t } = useTranslation(['projects', 'common'])
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const trimmed = query.trim()

  // 처음 열 때 한 번은 후보를 보여 준다. 사람 피커와 다른 판단인데, 이유가
  // 있다: 프로젝트 이름은 사람 이름처럼 기억하고 있는 값이 아니고, 대개
  // 몇 개뿐이다. 잘려 있을 수 있다는 것을 **문구로 말한다** — 조용히
  // 자르는 것이 문제였고, 자른다고 말하는 것은 문제가 아니다.
  const projects = useQuery({
    queryKey: ['projects', 'pick', trimmed],
    queryFn: () => projectsApi.list({ limit: LIMIT, ...(trimmed ? { q: trimmed } : {}) }),
    enabled: open,
  })

  const matches = projects.data?.items ?? []
  const truncated = matches.length >= LIMIT
  const nothing = emptyLabel ?? t('projects:pick.none')

  if (!open) {
    return (
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-medium text-muted">{label}</span>
        <span className="text-sm">
          {chosen ? `${chosen.key} · ${chosen.name}` : (unresolvedLabel ?? nothing)}
        </span>
        <Button variant="ghost" className="text-xs" onClick={() => { setOpen(true) }}>
          {chosen ? t('projects:pick.change') : t('common:action.choose')}
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <Field
        label={label}
        hint={t('projects:pick.searchHint')}
        value={query}
        onChange={(event) => { setQuery(event.target.value) }}
      />
      {projects.isError ? <Alert>{describeError(projects.error)}</Alert> : null}

      {/* 결과 목록은 **자기 이름**을 갖는다. 입력 라벨을 그대로 쓰면
          접근성 트리에 같은 이름의 다른 것이 둘 생기고(입력과 목록), 눌러야
          할 것을 이름으로 고를 수 없다 — 실제로 E2E 가 그 둘을 함께 집었다. */}
      <ul className="flex flex-col items-start gap-1" aria-label={t('common:state.matches')}>
        {/* "전체" 로 되돌리는 길은 목록의 맨 위다. 필터를 좁혔다가 푸는 것은
            고르는 것만큼 자주 하는 일인데, 되돌릴 손잡이가 없으면 주소를
            직접 고치는 수밖에 없다. */}
        {onClear ? (
          <li>
            <Button
              variant="ghost"
              className="text-xs"
              onClick={() => {
                onClear()
                setOpen(false)
                setQuery('')
              }}
            >
              {nothing}
            </Button>
          </li>
        ) : null}
        {matches.map((project) => (
          <li key={project.id}>
            <Button
              variant="ghost"
              className="text-xs"
              onClick={() => {
                onPick(project)
                setOpen(false)
                setQuery('')
              }}
            >
              {project.key} · {project.name}
            </Button>
          </li>
        ))}
      </ul>

      {/* 잘렸으면 **잘렸다고 말한다.** 조용히 자르는 것이 앞서 세 번 문제가
          됐고, 사람은 "이게 전부" 라고 읽는다. */}
      {truncated ? (
        <p className="text-xs text-muted">{t('projects:pick.more')}</p>
      ) : null}
      {projects.isSuccess && matches.length === 0 ? (
        <p className="text-sm text-muted">{t('projects:list.empty')}</p>
      ) : null}
    </div>
  )
}
