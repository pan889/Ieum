/**
 * 이 이슈를 언급한 문서 (overview.md 모듈 의존 그래프).
 *
 * 위키 API 를 부른다. 이슈 모듈이 답하려면 위키를 알아야 하고, 그러면 서버
 * 쪽 의존 그래프에 고리가 생긴다 — 화면도 그 경계를 그대로 따른다.
 */

import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Card } from '@/shared/ui/primitives'

export function LinkedDocs({ issueId }: { issueId: string }) {
  const { t } = useTranslation(['issues'])
  const docs = useQuery({
    queryKey: ['wiki', 'mentioning', issueId],
    queryFn: () => wikiApi.pages.mentioning(issueId),
    staleTime: 30_000,
  })

  // 없으면 자리를 차지하지 않는다. 대부분의 이슈에는 링크한 문서가 없다.
  if (!docs.isError && (docs.data ?? []).length === 0) return null

  return (
    <Card className="flex flex-col gap-2">
      <h2 className="text-sm font-medium text-muted">{t('issues:linkedDocs.title')}</h2>
      {docs.isError ? <Alert>{describeError(docs.error)}</Alert> : null}
      <ul className="flex flex-col gap-1 text-sm">
        {(docs.data ?? []).map((doc) => (
          <li key={doc.id}>
            <Link
              to="/wiki/$spaceKey/$"
              params={{ spaceKey: doc.space_key, _splat: doc.path }}
              className="text-accent hover:underline"
            >
              <span className="mr-2 font-mono text-xs text-muted">{doc.space_key}</span>
              {doc.title}
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  )
}
