/**
 * 내 요청 하나. 고객이 보는 화면이므로 **내부 정보를 그리지 않는다** —
 * 담당자·우선순위·내부 노트는 여기 없다. 대화(고객 회신)는 C7 에서 붙인다.
 *
 * 답은 **폼 키**로 온다. 커스텀 필드 키를 보여 주면 내부 스키마가 새고,
 * 폼 키가 바뀌면 화면이 답을 못 찾는다.
 */

import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { PortalInfo } from '@ieum/api-client'

import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card } from '@/shared/ui/primitives'

import { StateBadge } from './PortalHome'
import type { PortalRoute } from './routes'

/**
 * 답 하나를 글자로. 서버는 JSONB 를 그대로 돌려주므로 무엇이든 올 수 있다.
 *
 * **객체를 `String()` 에 넣지 않는다** — `[object Object]` 가 화면에 뜬다.
 * 고객이 적은 값이 있는데 그걸 보여 주지 못하는 것보다, JSON 으로라도
 * 보여 주는 편이 정직하다.
 */
function display(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map(display).join(', ')
  if (typeof value === 'boolean') return value ? '✓' : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  return ''
}

export function PortalTicket({
  portal,
  issueId,
  onNavigate,
}: {
  portal: PortalInfo
  issueId: string
  onNavigate: (route: PortalRoute) => void
}) {
  const { t, i18n } = useTranslation(['desk', 'common'])
  const ticket = useQuery({
    queryKey: ['portal', portal.slug, 'ticket', issueId],
    queryFn: () => deskApi.myTicket(portal.slug, issueId),
    retry: false,
  })

  if (ticket.isError) return <Alert>{describeError(ticket.error)}</Alert>
  if (!ticket.data) return <p className="text-sm text-muted">{t('common:state.loading')}</p>

  const dates = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
  const answers = ticket.data.answers.filter((answer) => display(answer.value) !== '')

  return (
    <article className="flex flex-col gap-4">
      <header className="flex flex-col gap-2">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium text-muted">{ticket.data.key}</span>
          <StateBadge
            name={ticket.data.state_name}
            category={ticket.data.state_category}
          />
        </div>
        <h1 className="text-xl font-semibold">{ticket.data.summary}</h1>
        <p className="text-xs text-muted">
          {t('desk:portal.requestedOn', {
            date: dates.format(new Date(ticket.data.created_at)),
          })}
          {ticket.data.request_type_name ? ` · ${ticket.data.request_type_name}` : ''}
        </p>
      </header>

      {ticket.data.description ? (
        // 마크다운으로 렌더하지 않는다. 고객이 적은 글이고, 여기서 서식을
        // 살리면 붙여넣은 링크·표가 의도와 다르게 그려진다. 줄바꿈만 살린다.
        <Card className="whitespace-pre-wrap text-sm">{ticket.data.description}</Card>
      ) : null}

      {answers.length > 0 ? (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          {answers.map((answer) => (
            <div key={answer.key} className="col-span-2 grid grid-cols-subgrid">
              {/* 라벨은 관리자가 입력한 데이터라 번역하지 않는다. */}
              <dt className="text-muted">{answer.label}</dt>
              <dd>{display(answer.value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <Button
        className="self-start text-xs"
        variant="ghost"
        onClick={() => {
          onNavigate({ kind: 'home', slug: portal.slug })
        }}
      >
        {t('desk:portal.myRequests')}
      </Button>
    </article>
  )
}
