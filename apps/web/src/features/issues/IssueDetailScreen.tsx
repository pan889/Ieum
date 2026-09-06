import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { issuesApi } from '@/shared/api'
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
          <Comments issueId={data.id} />
          <History issueId={data.id} />
        </div>
        <Details issue={data} onSaved={invalidate} />
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

function Comments({ issueId }: { issueId: string }) {
  const { t } = useTranslation(['issues'])
  const queryClient = useQueryClient()
  const [body, setBody] = useState('')
  const [internal, setInternal] = useState(false)

  const comments = useQuery({
    queryKey: ['issues', 'comments', issueId],
    queryFn: () => issuesApi.comments(issueId),
  })
  const authors = useUserNames((comments.data ?? []).map((c) => c.author_id))

  const add = useMutation({
    mutationFn: () => issuesApi.addComment(issueId, { body, is_internal: internal }),
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
        onSubmit={(event) => { event.preventDefault(); add.mutate() }}
      >
        {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
        <MarkdownEditor
          label={t('issues:comment.title')}
          placeholder={t('issues:comment.placeholder')}
          rows={4}
          value={body}
          onChange={setBody}
        />
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
