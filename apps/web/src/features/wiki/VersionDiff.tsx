/**
 * 판 사이 라인 diff (wiki-markdown.md 9절).
 *
 * 렌더 결과가 아니라 마크다운 원문을 비교한다. 정본이 마크다운이므로 사람이
 * 실제로 고친 것과 diff 가 일치한다. 본문은 이미 정규화돼 있어 목록 마커나
 * 표 패딩 같은 서식 흔들림이 섞이지 않는다.
 */

import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Card } from '@/shared/ui/primitives'

export function VersionDiff({
  pageId,
  before,
  after,
}: {
  pageId: string
  before: number
  after: number
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const diff = useQuery({
    queryKey: ['wiki', 'diff', pageId, before, after],
    queryFn: () => wikiApi.pages.diff(pageId, before, after),
  })

  if (diff.isError) return <Alert>{describeError(diff.error)}</Alert>
  if (diff.isPending) return <p className="text-sm text-muted">{t('common:state.loading')}</p>

  return (
    <Card className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-2 text-xs text-muted">
        <span className="font-medium text-fg">
          {t('wiki:diff.between', { before, after })}
        </span>
        <span className="text-success">+{diff.data.added}</span>
        <span className="text-danger">−{diff.data.removed}</span>
        {diff.data.lines.length === 0 ? <span>{t('wiki:diff.same')}</span> : null}
        {diff.data.truncated ? <span>{t('wiki:diff.truncated')}</span> : null}
      </div>

      {diff.data.lines.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-xs">
            <tbody>
              {diff.data.lines.map((line, index) => (
                <tr key={index} className={rowClass(line.op)}>
                  <td className="w-10 select-none px-1 text-right align-top text-muted">
                    {line.old_number ?? ''}
                  </td>
                  <td className="w-10 select-none px-1 text-right align-top text-muted">
                    {line.new_number ?? ''}
                  </td>
                  <td className="w-4 select-none px-1 align-top text-muted">{marker(line.op)}</td>
                  {/* 빈 줄도 한 칸 차지해야 줄 번호가 맞아 보인다. */}
                  <td className="whitespace-pre-wrap break-words px-1 align-top">
                    {line.text || ' '}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  )
}

/** 색만으로 뜻을 전하지 않는다 — 앞의 기호가 늘 함께 간다 (ux-principles 5절). */
function marker(op: string): string {
  if (op === 'insert') return '+'
  if (op === 'delete') return '−'
  return ' '
}

function rowClass(op: string): string {
  if (op === 'insert') return 'bg-success/10'
  if (op === 'delete') return 'bg-danger/10'
  return ''
}
