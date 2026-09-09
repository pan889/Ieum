/**
 * 이 이슈에 붙은 커밋·PR (A22, M6).
 *
 * **없으면 자리를 차지하지 않는다.** 저장소를 연동하지 않은 설치에서 이
 * 칸이 늘 비어 있으면, 사람은 이슈 상세를 스크롤할 때마다 빈 칸을 지나야
 * 한다. 대부분의 이슈에는 붙은 커밋이 없다.
 *
 * **"닫는다" 는 표시일 뿐이다.** `fixes ENG-12` 가 상태를 옮기지 않는 이유는
 * 서버에 적혀 있다(`vcs/refs.py`): 어느 전이로 옮길지는 프로젝트마다 다르고,
 * 커밋은 되돌려진다. 그래서 화면은 "닫는다고 적었다" 만 말한다.
 *
 * 주소는 코드 호스트로 나간다. `rel="noreferrer"` 를 붙이는 이유는 우리
 * 경로가 `Referer` 로 새어 나가지 않게 하려는 것이다 — 이슈 주소에는 키가
 * 들어 있고, 그건 그쪽이 알아야 할 것이 아니다.
 */

import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { ChangeLink } from '@ieum/api-client'

import { repositoriesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Card } from '@/shared/ui/primitives'

import { formatDateTime } from './format'

/**
 * 목록에 쓸 짧은 이름.
 *
 * 커밋 SHA 는 앞 일곱 자만 보여 준다 — 사람이 그 길이로 말한다. **PR 번호는
 * 자르지 않는다.** 둘에 같은 자르기를 걸면 `#12345678` 이 조용히 다른 PR 을
 * 가리키게 된다.
 */
export function shortRef(row: Pick<ChangeLink, 'kind' | 'external_ref'>): string {
  return row.kind === 'commit' ? row.external_ref.slice(0, 7) : `#${row.external_ref}`
}

export function Development({ issueId }: { issueId: string }) {
  const { t } = useTranslation(['issues'])
  const links = useQuery({
    queryKey: ['vcs', 'links', issueId],
    queryFn: () => repositoriesApi.links(issueId),
    staleTime: 30_000,
  })

  if (!links.isError && (links.data ?? []).length === 0) return null

  return (
    <Card className="flex flex-col gap-2" data-testid="issue-development">
      <h2 className="text-sm font-medium text-muted">{t('issues:development.title')}</h2>
      {links.isError ? <Alert>{describeError(links.error)}</Alert> : null}
      <ul className="flex flex-col gap-2 text-sm">
        {(links.data ?? []).map((row) => (
          <li key={row.id} className="flex flex-col gap-0.5">
            <div className="flex flex-wrap items-baseline gap-2">
              <Badge tone={row.kind === 'commit' ? 'neutral' : 'in_progress'}>
                {t(`issues:development.kind.${row.kind}`)}
              </Badge>
              <a
                href={row.url}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-xs text-accent hover:underline"
              >
                {shortRef(row)}
              </a>
              <span className="flex-1">{row.title}</span>
              {/* 닫는다고 적혀 있었다는 사실. 상태는 옮기지 않았다. */}
              {row.closing ? <Badge tone="done">{t('issues:development.closes')}</Badge> : null}
            </div>
            <p className="text-xs text-muted">
              {row.repository_name}
              {row.author === null ? '' : ` · ${row.author}`}
              {` · ${formatDateTime(row.happened_at)}`}
            </p>
          </li>
        ))}
      </ul>
    </Card>
  )
}
