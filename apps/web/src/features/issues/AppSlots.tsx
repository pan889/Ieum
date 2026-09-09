/**
 * 앱이 이 이슈에 놓은 것 (M6 "플러그인 훅").
 *
 * **앱의 코드를 실행하지 않는다.** 여기서 그리는 것은 두 가지뿐이다:
 *
 * - **링크** — 서버가 자리표(`{issue_key}` 등)를 채워 준 주소. 스킴은 등록할
 *   때 `https` 로 좁혀졌다. `<script>` 도, `<iframe>` 도, `dangerouslySet…`
 *   도 여기 없다.
 * - **패널** — 앱이 보낸 **마크다운**. 우리 파이프라인으로 그리므로 원시
 *   HTML 이 죽는다(`html: false`). 그 두 줄이 이 화면의 안전을 떠받친다.
 *
 * **누가 쓴 글인지 말한다.** 앱 이름을 함께 보여 주는 것이 이 컴포넌트의
 * 규칙이다 — 우리가 쓴 글과 앱이 쓴 글이 같은 자리에서 구별되지 않으면,
 * 앱 하나가 우리 목소리를 빌리게 된다.
 *
 * 놓인 것이 없으면 칸을 내지 않는다. 대부분의 이슈에는 앱이 없다.
 */

import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { Contribution } from '@ieum/api-client'

import { pluginsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Markdown } from '@/shared/markdown/Markdown'
import { Alert, Badge, Card } from '@/shared/ui/primitives'

export function AppSlots({ issueId }: { issueId: string }) {
  const { t } = useTranslation(['plugins'])

  const contributions = useQuery({
    queryKey: ['plugins', 'issue', issueId],
    queryFn: () => pluginsApi.forIssue(issueId),
    staleTime: 30_000,
  })

  const rows = contributions.data ?? []
  const links = rows.filter((row) => row.kind === 'link')
  const panels = rows.filter((row) => row.kind === 'panel')

  // 오류는 숨기지 않는다 — 못 읽은 것과 없는 것은 다르다. 다만 앱이 하나도
  // 없는 설치가 대부분이므로, 조용할 때는 자리를 차지하지 않는다.
  if (contributions.isError) {
    return <Alert>{describeError(contributions.error)}</Alert>
  }
  if (rows.length === 0) return null

  return (
    <div className="flex flex-col gap-3" data-testid="issue-app-slots">
      {panels.map((row) => (
        <AppPanel key={`${row.app_slug}:${row.label}`} row={row} />
      ))}
      {links.length > 0 ? (
        <Card className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">{t('plugins:issue.links')}</h2>
          <ul className="flex flex-wrap gap-3">
            {links.map((row) => (
              <li key={`${row.app_slug}:${row.label}`}>
                {/* 바깥으로 나가는 링크다. `noreferrer` 를 함께 두는 이유:
                    우리 이슈 주소가 Referer 로 새 나가면 그것만으로 이슈 키가
                    바깥에 알려진다. */}
                <a
                  className="text-sm text-accent underline"
                  href={row.url ?? '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {row.label}
                </a>
                <span className="ml-1.5 text-xs text-muted">{row.app_name}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  )
}

function AppPanel({ row }: { row: Contribution }) {
  const { t } = useTranslation(['plugins'])
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <h2 className="text-sm font-medium">{row.label}</h2>
        {/* **누가 쓴 글인지 말한다** (파일 머리 참조). */}
        <Badge tone="neutral">{t('plugins:issue.byApp', { app: row.app_name })}</Badge>
      </div>
      {/* 마크다운이다. 방언이 원시 HTML 을 끄므로 앱이 보낸 `<script>` 는
          글자로 남는다 — `Markdown.tsx` 머리에 그 이유가 있다. */}
      <Markdown source={row.body ?? ""} className="text-sm" />
    </Card>
  )
}
