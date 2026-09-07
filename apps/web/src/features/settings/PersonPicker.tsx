/**
 * 사람을 찾아 고른다.
 *
 * 목록을 통째로 내려 드롭다운에 넣지 **않는다.** 처음에는 그랬다 — 100명까지
 * 받아 `<select>` 를 채웠다. 그러면 사람이 101명인 조직에서 마지막 사람은
 * 그룹에도 역할에도 넣을 수 없다. 손잡이는 있고 대상만 없는 상태이고, 화면은
 * 아무 말도 하지 않는다. 개발 DB 의 사용자가 103명이 되면서 E2E 가 그 자리에서
 * 멈춰 드러났다.
 *
 * 그래서 검색으로 바꿨다. 서버는 이름·이메일로 이미 찾아 준다(`GET /users?q=`).
 * 한 글자도 안 쳤을 때 아무것도 보여주지 않는 것은 일부러다 — 첫 20명을 보여
 * 주면 "이게 전부인가" 로 읽힌다.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { usersApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

/** 한 번에 보여 줄 후보 수. 더 좁히는 것은 사람이 더 치는 쪽이 빠르다. */
const LIMIT = 20

export function PersonPicker({
  label,
  exclude,
  busy = false,
  onPick,
}: {
  label: string
  /** 이미 들어 있는 사람. 후보에서 빼면 두 번 넣는 실수를 막는다. */
  exclude?: ReadonlySet<string>
  busy?: boolean
  onPick: (userId: string) => void
}) {
  const { t } = useTranslation(['admin', 'common'])
  const [query, setQuery] = useState('')
  const trimmed = query.trim()

  const people = useQuery({
    queryKey: ['admin', 'users', 'search', trimmed],
    queryFn: () => usersApi.list({ q: trimmed, limit: LIMIT }),
    enabled: trimmed.length > 0,
  })

  const matches = (people.data?.items ?? []).filter((user) => !exclude?.has(user.id))

  return (
    <div className="flex flex-col gap-2">
      <Field
        label={label}
        hint={t('admin:people.searchHint')}
        value={query}
        onChange={(event) => { setQuery(event.target.value) }}
      />

      {people.isError ? <Alert>{describeError(people.error)}</Alert> : null}

      {trimmed.length > 0 ? (
        <ul className="flex flex-col items-start gap-1" aria-label={t('common:state.matches')}>
          {matches.map((user) => (
            <li key={user.id}>
              <Button
                variant="ghost"
                className="text-xs"
                loading={busy}
                onClick={() => {
                  onPick(user.id)
                  // 고른 뒤 비운다. 남겨 두면 같은 사람을 두 번 누르기 쉽고,
                  // 방금 넣은 사람이 후보에서 빠져 목록만 비어 보인다.
                  setQuery('')
                }}
              >
                {user.display_name} ({user.email})
              </Button>
            </li>
          ))}
        </ul>
      ) : null}

      {trimmed.length > 0 && !people.isPending && matches.length === 0 ? (
        <p className="text-sm text-muted">{t('admin:people.noMatches')}</p>
      ) : null}
    </div>
  )
}
