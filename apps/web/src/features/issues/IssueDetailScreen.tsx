import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { AgentTicket, SlaStanding } from '@ieum/api-client'

import { deskApi, issuesApi } from '@/shared/api'
import { describeError, fieldOfError } from '@/shared/api/errors'
import { RichText } from '@/shared/markdown/RichText'
import { MarkdownEditor } from '@/shared/markdown/MarkdownEditor'
import { Alert, Badge, Button, Card, Field, Select } from '@/shared/ui/primitives'

import { Attachments } from './Attachments'
import { LinkedDocs } from './LinkedDocs'
import { CustomField } from './CustomField'
import { changedFields, type FieldValue } from './customFields'
import { Relations } from './Relations'
import { TimeTracking } from './TimeTracking'
import { categoryTone, formatDate, formatDateTime, priorityLabel } from './format'
import { useFieldDefinitions, useUserNames, useUserSearch } from './hooks'

export function IssueDetailScreen() {
  const { issueKey } = useParams({ from: '/issues/$issueKey' })
  const { t } = useTranslation(['issues', 'common'])
  const queryClient = useQueryClient()

  const issue = useQuery({
    queryKey: ['issues', 'detail', issueKey],
    queryFn: () => issuesApi.getByKey(issueKey),
  })

  /**
   * 이 이슈가 티켓인가. **`ticket: null` 이 "티켓이 아니다" 라는 답이다.**
   *
   * 이슈 응답에 데스크 정보를 섞지 않는다 — `issues` 가 `desk` 를 알게 되고
   * 그건 의존 그래프에 없는 화살표다(ADR-0010). 화면이 조회를 하나 더 한다.
   *
   * 처음에는 서버가 404 로 답했다. 그게 "없으면 404" 라는 규칙에는 맞았지만,
   * **평범한 이슈를 열 때마다 콘솔에 404 가 찍혔다** — 이슈 상세는 이 제품에서
   * 가장 많이 열리는 화면이다. 콘솔이 매번 붉으면 사람은 그것을 안 보게 되고,
   * 그러면 진짜 오류가 가장 오래 살아남는다. E2E 의 `consoleErrors` 픽스처가
   * 이 판단을 이미 갖고 있어서 이슈 스펙 넷이 붉어져 드러났다.
   *
   * **이 훅은 아래 조기 반환보다 위에 있어야 한다.** 훅은 조건 없이 같은
   * 순서로 불려야 하고, 조기 반환 뒤에 두면 로딩 중 렌더에서는 안 불려
   * 렌더마다 훅 수가 달라진다.
   */
  const issueId = issue.data?.id
  const ticket = useQuery({
    queryKey: ['desk', 'ticket', issueId ?? ''],
    queryFn: () => deskApi.agentTicket(issueId ?? ''),
    enabled: Boolean(issueId),
  })
  const desk = ticket.data?.ticket ?? null

  if (issue.isPending) {
    return <p className="text-sm text-muted">{t('common:state.loading')}</p>
  }
  if (issue.isError) {
    return <Alert>{describeError(issue.error)}</Alert>
  }

  const data = issue.data
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['issues'] })
  }

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-5">
      <header className="flex flex-wrap items-baseline gap-3">
        <Link to="/issues" className="font-mono text-xs text-accent hover:underline">
          {data.key}
        </Link>
        <h1 className="flex-1 text-xl font-semibold">{data.summary}</h1>
        {data.archived_at ? <Badge>{t('issues:detail.archived')}</Badge> : null}
        <Badge tone={categoryTone(data.state_category)}>{data.state_name}</Badge>
      </header>

      <div className="grid gap-5 lg:grid-cols-[1fr_18rem]">
        <div className="flex flex-col gap-5">
          <SummaryAndDescription issue={data} onSaved={invalidate} />
          <Transitions issue={data} onMoved={invalidate} />
          <Relations issueId={data.id} issueKey={data.key} />
          <TimeTracking issueId={data.id} version={data.version} />
          <Attachments ownerType="issue" ownerId={data.id} />
          <LinkedDocs issueId={data.id} />
          <Comments issueId={data.id} projectId={data.project_id} isTicket={desk !== null} />
          <History issueId={data.id} />
        </div>
        <div className="flex flex-col gap-5">
          {/* 티켓이면 데스크 정보를 사이드바 맨 위에 둔다. 상담원이 먼저
              알아야 하는 것은 "누가, 어느 창구로" 다. */}
          {desk ? <TicketFacts ticket={desk} /> : null}
          <Details issue={data} onSaved={invalidate} />
        </div>
      </div>
    </section>
  )
}

type Issue = Awaited<ReturnType<typeof issuesApi.getByKey>>

function SummaryAndDescription({ issue, onSaved }: { issue: Issue; onSaved: () => void }) {
  const { t } = useTranslation(['issues', 'common'])
  const [editing, setEditing] = useState(false)
  const [summary, setSummary] = useState(issue.summary)
  const [description, setDescription] = useState(issue.description ?? '')

  const save = useMutation({
    mutationFn: () =>
      // version 을 같이 보낸다. 남이 먼저 고쳤으면 409 로 막힌다.
      issuesApi.change(issue.id, { summary, description: description || null }, issue.version),
    onSuccess: () => { setEditing(false); onSaved() },
  })

  if (!editing) {
    return (
      <Card className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted">{t('issues:detail.description')}</h2>
          <Button variant="ghost" onClick={() => {
            setSummary(issue.summary)
            setDescription(issue.description ?? '')
            setEditing(true)
          }}>
            {t('issues:detail.edit')}
          </Button>
        </div>
        {issue.description ? (
          <RichText source={issue.description} className="text-sm" />
        ) : (
          <p className="text-sm text-muted">{t('issues:detail.descriptionEmpty')}</p>
        )}
      </Card>
    )
  }

  return (
    <Card>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => { event.preventDefault(); save.mutate() }}
      >
        {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
        <Field
          label={t('issues:create.summary')}
          required
          value={summary}
          onChange={(e) => { setSummary(e.target.value); }}
        />
        <MarkdownEditor
          label={t('issues:detail.description')}
          value={description}
          onChange={setDescription}
          attachTo={{ ownerType: 'issue', ownerId: issue.id }}
        />
        <div className="flex gap-2">
          <Button type="submit" loading={save.isPending}>{t('issues:detail.save')}</Button>
          <Button type="button" variant="ghost" onClick={() => { setEditing(false); }}>
            {t('issues:detail.cancel')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function Transitions({ issue, onMoved }: { issue: Issue; onMoved: () => void }) {
  const { t } = useTranslation(['issues'])
  const transitions = useQuery({
    queryKey: ['issues', 'transitions', issue.id, issue.version],
    queryFn: () => issuesApi.transitions(issue.id),
  })
  const move = useMutation({
    mutationFn: (transitionId: string) =>
      issuesApi.transition(issue.id, transitionId, issue.version),
    onSuccess: onMoved,
  })

  if (transitions.isPending || transitions.isError) return null

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-sm font-medium text-muted">{t('issues:transition.title')}</h2>
      {move.isError ? <Alert>{describeError(move.error)}</Alert> : null}
      {transitions.data.length === 0 ? (
        <p className="text-sm text-muted">{t('issues:transition.none')}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {transitions.data.map((transition) => {
            const blocked = transition.blocked_by.length > 0
            return (
              <Button
                key={transition.id}
                variant="secondary"
                disabled={blocked}
                loading={move.isPending && move.variables === transition.id}
                title={
                  blocked
                    ? t('issues:transition.blocked', {
                        conditions: transition.blocked_by.join(', '),
                      })
                    : undefined
                }
                onClick={() => { move.mutate(transition.id); }}
              >
                {transition.name}
              </Button>
            )
          })}
        </div>
      )}
    </Card>
  )
}

function Details({ issue, onSaved }: { issue: Issue; onSaved: () => void }) {
  const { t } = useTranslation(['issues'])
  const names = useUserNames([issue.assignee_id, issue.reporter_id])
  const [assigning, setAssigning] = useState(false)
  const [search, setSearch] = useState('')
  const candidates = useUserSearch(search)

  const assign = useMutation({
    mutationFn: (userId: string | null) =>
      issuesApi.change(issue.id, { assignee_id: userId }, issue.version),
    onSuccess: () => { setAssigning(false); onSaved() },
  })

  const setPriority = useMutation({
    mutationFn: (priority: number) => issuesApi.change(issue.id, { priority }, issue.version),
    onSuccess: onSaved,
  })

  const archive = useMutation({
    mutationFn: () => issuesApi.archive(issue.id),
    onSuccess: onSaved,
  })

  return (
    <Card className="flex h-fit flex-col gap-4 p-4">
      <h2 className="text-sm font-medium text-muted">{t('issues:detail.details')}</h2>

      <Row label={t('issues:detail.type')}>{issue.type_name}</Row>

      <Row label={t('issues:detail.assignee')}>
        {assigning ? (
          <div className="flex flex-col gap-2">
            <Field
              label={t('issues:detail.assignee')}
              className="text-xs"
              value={search}
              onChange={(e) => { setSearch(e.target.value); }}
            />
            <ul className="max-h-40 overflow-y-auto text-sm">
              <li>
                <button
                  type="button"
                  className="w-full px-1 py-1 text-left text-muted hover:text-fg"
                  onClick={() => { assign.mutate(null); }}
                >
                  {t('issues:detail.unassigned')}
                </button>
              </li>
              {candidates.data?.items.map((user) => (
                <li key={user.id}>
                  <button
                    type="button"
                    className="w-full px-1 py-1 text-left hover:text-accent"
                    onClick={() => { assign.mutate(user.id); }}
                  >
                    {user.display_name}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <button
            type="button"
            className="text-left hover:text-accent"
            onClick={() => { setAssigning(true); }}
          >
            {issue.assignee_id
              ? (names.data?.get(issue.assignee_id) ?? '…')
              : t('issues:detail.unassigned')}
          </button>
        )}
      </Row>

      <Row label={t('issues:detail.reporter')}>
        {names.data?.get(issue.reporter_id) ?? '…'}
      </Row>

      <Row label={t('issues:detail.priority')}>
        <Select
          className="w-full py-1 text-xs"
          value={String(issue.priority)}
          onChange={(e) => { setPriority.mutate(Number(e.target.value)); }}
        >
          {[1, 2, 3, 4, 5].map((p) => (
            <option key={p} value={p}>{priorityLabel(p)}</option>
          ))}
        </Select>
      </Row>

      <Row label={t('issues:detail.due')}>
        {formatDate(issue.due_date) || t('issues:detail.none')}
      </Row>
      <Row label={t('issues:detail.progress')}>{`${String(issue.progress)}%`}</Row>
      <Row label={t('issues:detail.labels')}>
        {issue.labels.length > 0 ? issue.labels.join(', ') : t('issues:detail.none')}
      </Row>
      <Row label={t('issues:detail.created')}>{formatDateTime(issue.created_at)}</Row>
      <Row label={t('issues:detail.updated')}>{formatDateTime(issue.updated_at)}</Row>

      <CustomFields issue={issue} onSaved={onSaved} />

      {assign.isError ? <Alert>{describeError(assign.error)}</Alert> : null}
      {setPriority.isError ? <Alert>{describeError(setPriority.error)}</Alert> : null}

      {!issue.archived_at ? (
        <Button
          variant="ghost"
          className="justify-start"
          loading={archive.isPending}
          onClick={() => { archive.mutate(); }}
        >
          {t('issues:detail.archive')}
        </Button>
      ) : null}
    </Card>
  )
}

/**
 * 커스텀 필드 편집 패널.
 *
 * 저장은 한 번에 모아서 한다. 필드마다 즉시 PATCH 하면 값 하나 고칠 때마다
 * 이슈 버전이 올라가고, 옆 사람이 편집 중이면 버전 충돌로 튕긴다.
 */
function CustomFields({ issue, onSaved }: { issue: Issue; onSaved: () => void }) {
  const { t } = useTranslation(['issues'])
  const definitions = useFieldDefinitions(issue.project_id, issue.type_id)
  //: 사용자가 건드린 필드만. 나머지는 저장된 값을 그대로 보여준다.
  const [draft, setDraft] = useState<Record<string, FieldValue>>({})

  const save = useMutation({
    mutationFn: () =>
      issuesApi.update(
        issue.id,
        { custom_fields: changedFields(issue.custom_fields, draft) },
        issue.version,
      ),
    onSuccess: () => { setDraft({}); onSaved() },
  })

  const rows = definitions.data ?? []
  if (rows.length === 0) return null

  const patch = changedFields(issue.custom_fields, draft)
  const dirty = Object.keys(patch).length > 0

  return (
    <div className="flex flex-col gap-3 border-t border-border pt-3">
      <h3 className="text-xs font-medium text-muted">{t('issues:detail.customFields')}</h3>
      {rows.map((definition) => (
        <CustomField
          key={definition.id}
          definition={definition}
          projectId={issue.project_id}
          compact
          value={
            definition.key in draft
              ? (draft[definition.key] ?? null)
              : ((issue.custom_fields[definition.key] ?? null) as FieldValue)
          }
          onChange={(value) => {
            setDraft((prev) => ({ ...prev, [definition.key]: value }))
          }}
        />
      ))}
      {/* 필드가 여러 개면 값 오류 문구만으로는 어디를 고쳐야 할지 모른다.
          서버가 알려준 필드 이름을 앞에 붙인다. */}
      {save.isError ? (
        <Alert>
          {[rows.find((d) => d.key === fieldOfError(save.error))?.name, describeError(save.error)]
            .filter(Boolean)
            .join(': ')}
        </Alert>
      ) : null}
      {dirty ? (
        <Button
          className="self-start"
          loading={save.isPending}
          onClick={() => { save.mutate() }}
        >
          {t('issues:detail.saveFields')}
        </Button>
      ) : null}
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6rem_1fr] items-start gap-2 text-sm">
      <span className="text-xs text-muted">{label}</span>
      <div className="min-w-0 break-words">{children}</div>
    </div>
  )
}

/**
 * 남은 업무 시간을 사람이 읽는 말로.
 *
 * **서버가 준 초를 그대로 센다.** 브라우저가 목표 시각에서 카운트다운하면
 * 업무 시간이 빠져서, 금요일 저녁에 남은 4시간이 토요일 아침에 0 이 된다.
 * 그래서 여기서는 초를 글자로 바꾸는 일만 한다.
 */
function humanize(seconds: number): string {
  const total = Math.abs(seconds)
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return `${String(days)}d ${String(hours)}h`
  if (hours > 0) return `${String(hours)}h ${String(minutes)}m`
  // 1분 미만도 "0m" 이라고 말한다 — 빈칸이면 값이 없는 것과 구별되지 않는다.
  return `${String(minutes)}m`
}

/**
 * 티켓의 SLA 들 (C5).
 *
 * 네 상태를 **다르게** 그린다. 하나로 뭉치면 상담원이 무엇을 해야 하는지
 * 알 수 없다:
 *
 * - 지켰다 — 남은 시간을 그리지 않는다. 끝난 약속이 계속 재촉하면 안 된다.
 * - 넘겼다 — 얼마나 넘겼는지 말한다. "위반" 만으로는 3분과 3일을 구별 못 한다.
 * - 멈췄다 — 그렇다고 말한다. 안 그러면 줄지 않는 숫자를 보고 화면이 고장난
 *   줄 안다.
 * - 돌고 있다 — 남은 업무 시간.
 */
function SlaRows({ rows }: { rows: SlaStanding[] }) {
  const { t } = useTranslation(['desk'])

  return (
    <Card className="flex flex-col gap-2">
      <h2 className="text-sm font-medium text-muted">{t('desk:sla.title')}</h2>
      {rows.map((row) => (
        <Row key={`${row.policy_name}-${row.metric}`} label={row.policy_name}>
          {row.completed ? (
            <Badge tone="done">{t('desk:sla.met')}</Badge>
          ) : row.breached ? (
            <>
              <Badge tone="danger">{t('desk:sla.breached')}</Badge>
              <span className="ml-1 text-xs text-muted">
                {t('desk:sla.overBy', { time: humanize(row.remaining_seconds) })}
              </span>
            </>
          ) : (
            <>
              <span>{t('desk:sla.left', { time: humanize(row.remaining_seconds) })}</span>
              {row.paused ? (
                <Badge tone="neutral" className="ml-1">
                  {t('desk:sla.paused')}
                </Badge>
              ) : null}
            </>
          )}
          {/* 어느 지표인지 작게 붙인다. "첫 응답 2시간 남음" 과 "해결 2시간
              남음" 은 상담원이 해야 하는 일이 다르다. */}
          <span className="ml-1 text-xs text-muted">
            {t(`desk:sla.metric.${row.metric}`)}
          </span>
        </Row>
      ))}
    </Card>
  )
}

/**
 * 티켓의 데스크 정보. **누가, 어느 창구로** 가 상담원이 먼저 알아야 하는
 * 것이다.
 *
 * 게스트 주소에 "검증되지 않음" 을 붙이는 것이 이 판의 요점이다. 게스트가
 * 적어 낸 주소는 아무나 적을 수 있다 — 계정 주소처럼 믿고 확인 없이 무엇을
 * 보내면, 남의 주소를 적어 넣은 사람이 그 사람에게 무언가를 배달한다.
 */
function TicketFacts({ ticket }: { ticket: AgentTicket }) {
  const { t } = useTranslation(['desk'])

  return (
    <Card className="flex flex-col gap-2">
      <h2 className="text-sm font-medium text-muted">{t('desk:agent.title')}</h2>
      {/* **SLA 를 맨 위에 둔다.** 상담원이 이 티켓에서 먼저 알아야 하는 것은
          "언제까지인가" 다 — 누가 냈는지보다 그것이 먼저 시간에 쫓긴다. */}
      {ticket.sla.length > 0 ? <SlaRows rows={ticket.sla} /> : null}
      {ticket.requester ? (
        <Row label={t('desk:agent.requester')}>
          <span>{ticket.requester.display_name || ticket.requester.email}</span>
          <span className="ml-1 text-xs text-muted">{ticket.requester.email}</span>
          {ticket.requester.verified ? null : (
            <Badge tone="danger" className="ml-1">
              {t('desk:agent.unverified')}
            </Badge>
          )}
        </Row>
      ) : null}
      {ticket.organization_name ? (
        <Row label={t('desk:agent.organization')}>{ticket.organization_name}</Row>
      ) : null}
      {ticket.request_type_name ? (
        <Row label={t('desk:agent.requestType')}>{ticket.request_type_name}</Row>
      ) : null}
      {/* 창구는 링크로 둔다. 상담원이 고객이 보는 화면을 열어 볼 수 있어야
          "고객에게는 어떻게 보이나" 를 확인할 수 있다. */}
      {ticket.portal_slug ? (
        <Row label={t('desk:agent.portal')}>
          <a className="underline" href={`/portal/${ticket.portal_slug}`}>
            /portal/{ticket.portal_slug}
          </a>
        </Row>
      ) : null}
      <Row label={t('desk:agent.channel')}>{t(`desk:agent.channel.${ticket.channel}`)}</Row>
    </Card>
  )
}

/**
 * 정형 응답 고르기.
 *
 * 고른 것을 **덮어쓰지 않고 잇는다.** 쓰던 글을 지우면 되돌릴 수 없고,
 * 상담원은 보통 "인사 + 정형 문구 + 마무리" 로 쓴다.
 *
 * 목록을 못 가져오면(권한이 없거나 이 프로젝트에 없으면) **아무 것도 그리지
 * 않는다.** 빈 선택 상자나 오류를 띄우면, 코멘트를 쓰려는 사람에게 자기가
 * 요청하지도 않은 실패를 보여 주는 것이 된다.
 */
function CannedPicker({
  projectId,
  onPick,
}: {
  projectId: string
  onPick: (text: string) => void
}) {
  const { t } = useTranslation(['desk'])
  const responses = useQuery({
    queryKey: ['canned', projectId],
    queryFn: () => deskApi.listCannedResponses(projectId),
    retry: false,
  })

  const rows = responses.data ?? []
  if (rows.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted">{t('desk:canned.insert')}</span>
      {/* `Chip` 을 쓰지 않는다. 그건 `aria-pressed` 를 붙이므로 토글로
          읽히는데, 여기 있는 것은 누르면 글이 들어가는 **행동**이다 —
          스크린 리더에 "선택 안 됨" 이라고 알려 줄 이유가 없다. */}
      {rows.map((row) => (
        <Button
          key={row.id}
          type="button"
          variant="ghost"
          className="text-xs"
          onClick={() => { onPick(row.body) }}
        >
          {row.shortcut ? `${row.name} (/${row.shortcut})` : row.name}
        </Button>
      ))}
    </div>
  )
}

function Comments({
  issueId,
  projectId,
  isTicket,
}: {
  issueId: string
  projectId: string
  isTicket: boolean
}) {
  const { t } = useTranslation(['issues', 'desk'])
  const queryClient = useQueryClient()
  const [body, setBody] = useState('')
  const [internal, setInternal] = useState(false)

  const comments = useQuery({
    queryKey: ['issues', 'comments', issueId],
    queryFn: () => issuesApi.comments(issueId),
  })
  const authors = useUserNames((comments.data ?? []).map((c) => c.author_id))

  const add = useMutation({
    mutationFn: (asInternal: boolean) =>
      issuesApi.addComment(issueId, { body, is_internal: asInternal }),
    onSuccess: () => {
      setBody('')
      void queryClient.invalidateQueries({ queryKey: ['issues', 'comments', issueId] })
    },
  })

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-medium text-muted">{t('issues:comment.title')}</h2>

      {comments.isPending ? null : comments.isError ? (
        <Alert>{describeError(comments.error)}</Alert>
      ) : comments.data.length === 0 ? (
        <p className="text-sm text-muted">{t('issues:comment.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-4">
          {comments.data.map((comment) => (
            <li key={comment.id} className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-xs text-muted">
                <span className="font-medium text-fg">
                  {authors.data?.get(comment.author_id) ?? '…'}
                </span>
                <span>{formatDateTime(comment.created_at)}</span>
                {comment.edited_at ? <span>({t('issues:comment.edited')})</span> : null}
                {comment.is_internal ? (
                  <Badge tone="in_progress">{t('issues:comment.internalBadge')}</Badge>
                ) : null}
              </div>
              <RichText source={comment.body} className="text-sm" />
            </li>
          ))}
        </ul>
      )}

      <form
        className="flex flex-col gap-2 border-t border-border pt-4"
        onSubmit={(event) => { event.preventDefault(); add.mutate(internal) }}
      >
        {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
        <MarkdownEditor
          label={t('issues:comment.title')}
          placeholder={t('issues:comment.placeholder')}
          rows={4}
          value={body}
          onChange={setBody}
          // 코멘트에 붙인 이미지도 이슈의 첨부다. 코멘트마다 따로 두면
          // 코멘트를 지울 때 본문에서 참조하던 그림이 같이 사라진다.
          attachTo={{ ownerType: 'issue', ownerId: issueId }}
        />
        {/*
          **티켓에서는 체크박스를 쓰지 않는다.**

          티켓의 코멘트는 고객이 읽는다. 체크박스는 한 번의 미스클릭으로 두
          방향 모두 사고가 된다: 꺼진 채로 쓰면 내부 노트가 고객에게 가고,
          켜진 채로 쓰면 회신이 고객에게 닿지 않는다. 어느 쪽도 되돌릴 수
          없다 — 보낸 것은 이미 읽혔고, 안 간 것은 기다림이 된다.

          그래서 **버튼을 둘로 나눈다.** 무엇을 하는지가 누르는 행동에 적혀
          있으면 기본값을 틀릴 수 없다. 티켓이 아닌 이슈에는 고객이 없으므로
          위험이 없고, 잘 돌던 화면을 바꾸지 않는다.
        */}
        {/* 정형 응답은 **티켓에만** 둔다. 티켓이 아닌 이슈에는 고객이 없고,
            정형 응답은 고객에게 하는 말이다. */}
        {isTicket ? (
          <CannedPicker
            projectId={projectId}
            onPick={(text) => {
              // **덮어쓰지 않고 잇는다.** 쓰던 글이 있는데 지우면 그건
              // 되돌릴 수 없다 — 편집기의 실행 취소는 프로그램이 바꾼
              // 값까지 되돌려 주지 않는다.
              setBody((current) => (current.trim() === '' ? text : `${current}\n\n${text}`))
            }}
          />
        ) : null}
        {isTicket ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              loading={add.isPending && !internal}
              disabled={body.trim() === ''}
              onClick={() => { setInternal(false); add.mutate(false) }}
            >
              {t('desk:agent.replyToCustomer')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              loading={add.isPending && internal}
              disabled={body.trim() === ''}
              onClick={() => { setInternal(true); add.mutate(true) }}
            >
              {t('desk:agent.addInternalNote')}
            </Button>
            <span className="text-xs text-muted">{t('desk:agent.replyHint')}</span>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <Button type="submit" loading={add.isPending} disabled={body.trim() === ''}>
              {t('issues:comment.submit')}
            </Button>
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input
                type="checkbox"
                checked={internal}
                onChange={(e) => { setInternal(e.target.checked); }}
              />
              {t('issues:comment.internal')}
            </label>
          </div>
        )}
      </form>
    </Card>
  )
}

function History({ issueId }: { issueId: string }) {
  const { t } = useTranslation(['issues'])
  const history = useQuery({
    queryKey: ['issues', 'history', issueId],
    queryFn: () => issuesApi.history(issueId),
  })
  const actors = useUserNames((history.data ?? []).map((h) => h.actor_id))

  if (history.isPending || history.isError) return null

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-sm font-medium text-muted">{t('issues:history.title')}</h2>
      {history.data.length === 0 ? (
        <p className="text-sm text-muted">{t('issues:history.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-2 text-sm">
          {history.data.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium">
                {entry.actor_id ? (actors.data?.get(entry.actor_id) ?? '…') : '—'}
              </span>
              <span className="text-xs text-muted">{formatDateTime(entry.created_at)}</span>
              <span className="text-muted">
                {entry.changes
                  .map((change) => t('issues:history.changed', { field: change.field }))
                  .join(', ')}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
