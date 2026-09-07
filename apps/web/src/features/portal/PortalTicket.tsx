/**
 * 내 요청 하나. 고객이 보는 화면이므로 **내부 정보를 그리지 않는다** —
 * 담당자·우선순위는 여기 없다.
 *
 * **내부 노트는 이 화면에 올 수 없다.** 서버가 공개 코멘트만 내주고, 그
 * 계약에는 `include_internal` 매개변수가 아예 없다(C7). 화면에서 걸러내는
 * 방식이 아닌 것이 요점이다 — 실수 한 번이 고객에게 내부 노트를 보인다.
 *
 * 답은 **폼 키**로 온다. 커스텀 필드 키를 보여 주면 내부 스키마가 새고,
 * 폼 키가 바뀌면 화면이 답을 못 찾는다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { PortalInfo } from '@ieum/api-client'

import { useAuthStore } from '@/features/auth/store'
import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Textarea } from '@/shared/ui/primitives'

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

      <Conversation portal={portal} issueId={issueId} />

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

/**
 * 대화. 고객의 회신과 상담원의 **공개** 회신만 담긴다.
 *
 * 지은이 이름을 그리지 않는다. 내부 사람의 이름을 고객에게 알려 줄 이유가
 * 없고(상담원이 바뀌는 것도 고객의 관심사가 아니다), "나" 와 "지원팀" 만
 * 구별되면 대화는 읽힌다. `author_id` 가 내 id 인지로 가른다.
 */
function Conversation({ portal, issueId }: { portal: PortalInfo; issueId: string }) {
  const { t, i18n } = useTranslation(['desk', 'common'])
  const user = useAuthStore((s) => s.user)
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')

  const replies = useQuery({
    queryKey: ['portal', portal.slug, 'replies', issueId],
    queryFn: () => deskApi.replies(portal.slug, issueId),
  })

  const send = useMutation({
    mutationFn: () => deskApi.reply(portal.slug, issueId, draft),
    onSuccess: async () => {
      setDraft('')
      await queryClient.invalidateQueries({
        queryKey: ['portal', portal.slug, 'replies', issueId],
      })
    },
  })

  const times = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })

  return (
    <section className="flex flex-col gap-3 border-t border-border pt-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
        {t('desk:portal.conversation')}
      </h2>

      {replies.isError ? <Alert>{describeError(replies.error)}</Alert> : null}
      {replies.isSuccess && replies.data.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:portal.conversationEmpty')}</p>
      ) : null}

      <ul className="flex flex-col gap-3">
        {(replies.data ?? []).map((reply) => (
          <li key={reply.id} className="flex flex-col gap-1">
            <span className="text-xs text-muted">
              {reply.author_id && reply.author_id === user?.id
                ? t('desk:portal.fromMe')
                : t('desk:portal.fromSupport')}
              {' · '}
              {times.format(new Date(reply.created_at))}
            </span>
            {/* 마크다운으로 렌더하지 않는다. 고객이 적은 글이고, 상담원의
                회신도 고객이 읽을 글이다 — 서식을 살리면 붙여넣은 링크·표가
                의도와 다르게 그려진다. 줄바꿈만 살린다. */}
            <p className="whitespace-pre-wrap rounded-md border border-border bg-surface px-3 py-2 text-sm">
              {reply.body}
            </p>
          </li>
        ))}
      </ul>

      {user ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            send.mutate()
          }}
        >
          {send.isError ? <Alert>{describeError(send.error)}</Alert> : null}
          <Textarea
            label={t('desk:portal.replyLabel')}
            rows={4}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value)
            }}
          />
          <Button
            type="submit"
            className="self-start"
            disabled={draft.trim() === '' || send.isPending}
          >
            {send.isPending ? t('desk:form.sending') : t('desk:portal.replySubmit')}
          </Button>
        </form>
      ) : null}
    </section>
  )
}
