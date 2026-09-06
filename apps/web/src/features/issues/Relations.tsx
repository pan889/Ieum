import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { IssueSummary } from '@ieum/api-client'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field, Select } from '@/shared/ui/primitives'

import { categoryTone } from './format'

const LINK_KINDS = ['blocks', 'relates', 'duplicates', 'precedes', 'copied'] as const

export function Relations({ issueId, issueKey }: { issueId: string; issueKey: string }) {
  const { t } = useTranslation(['issues', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [targetKey, setTargetKey] = useState('')
  const [kind, setKind] = useState<string>('relates')

  const relations = useQuery({
    queryKey: ['issues', 'relations', issueId],
    queryFn: () => issuesApi.relations(issueId),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['issues', 'relations', issueId] })
  }

  const add = useMutation({
    mutationFn: async () => {
      // 사람은 키로 말한다. id 는 여기서 찾아 준다 — 사용자에게 UUID 를
      // 복사하게 만들면 아무도 관계를 안 건다.
      const target = await issuesApi.getByKey(targetKey.trim().toUpperCase())
      await issuesApi.link(issueId, { to_issue_id: target.id, kind })
    },
    onSuccess: () => {
      setTargetKey('')
      setAdding(false)
      refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (linkId: string) => issuesApi.unlink(linkId),
    onSuccess: refresh,
  })

  if (relations.isPending) return null
  if (relations.isError) {
    return (
      <Card>
        <Alert>{describeError(relations.error)}</Alert>
      </Card>
    )
  }

  const { parent, children, links } = relations.data
  const empty = parent === null && children.length === 0 && links.length === 0

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted">{t('issues:relations.title')}</h2>
        <div className="flex gap-1">
          <Link
            to="/issues/new"
            search={{ parent: issueKey }}
            className="rounded-md px-2 py-1 text-xs text-muted hover:text-fg"
          >
            {t('issues:relations.addChild')}
          </Link>
          <Button variant="ghost" className="text-xs" onClick={() => { setAdding((v) => !v); }}>
            {t('issues:relations.add')}
          </Button>
        </div>
      </div>

      {adding ? (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => { event.preventDefault(); add.mutate() }}
        >
          <Select
            label={t('issues:relations.kind')}
            value={kind}
            onChange={(e) => { setKind(e.target.value); }}
          >
            {LINK_KINDS.map((value) => (
              <option key={value} value={value}>
                {t(`issues:link.${value}`)}
              </option>
            ))}
          </Select>
          <div className="w-40">
            <Field
              label={t('issues:relations.targetKey')}
              placeholder="ENG-123"
              value={targetKey}
              onChange={(e) => { setTargetKey(e.target.value); }}
            />
          </div>
          <Button type="submit" loading={add.isPending} disabled={targetKey.trim() === ''}>
            {t('issues:relations.submit')}
          </Button>
        </form>
      ) : null}

      {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {empty ? <p className="text-sm text-muted">{t('issues:relations.empty')}</p> : null}

      {parent ? (
        <Group label={t('issues:relations.parent')}>
          <IssueRow issue={parent} />
        </Group>
      ) : null}

      {children.length > 0 ? (
        <Group label={t('issues:relations.children')}>
          {children.map((child) => (
            <IssueRow key={child.id} issue={child} />
          ))}
        </Group>
      ) : null}

      {links.length > 0 ? (
        <Group label={t('issues:relations.links')}>
          {links.map((link) => (
            <IssueRow
              key={link.link_id}
              issue={link.issue}
              // 방향에 따라 문구가 달라진다. "blocks" 와 "is blocked by" 는
              // 같은 행이 아니라 반대 의미다.
              prefix={t(
                link.outward ? `issues:link.${link.kind}` : `issues:link.${link.kind}.inward`,
              )}
              onRemove={() => { remove.mutate(link.link_id); }}
              removeLabel={t('issues:relations.remove')}
            />
          ))}
        </Group>
      ) : null}
    </Card>
  )
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
    </div>
  )
}

function IssueRow({
  issue,
  prefix,
  onRemove,
  removeLabel,
}: {
  issue: IssueSummary
  prefix?: string
  onRemove?: () => void
  removeLabel?: string
}) {
  return (
    <div className="flex items-baseline gap-2 text-sm">
      {prefix ? <span className="text-xs text-muted">{prefix}</span> : null}
      <Link
        to="/issues/$issueKey"
        params={{ issueKey: issue.key }}
        className="font-mono text-xs text-accent hover:underline"
      >
        {issue.key}
      </Link>
      <span className="min-w-0 flex-1 truncate">{issue.summary}</span>
      <Badge tone={categoryTone(issue.state_category)}>{issue.state_name}</Badge>
      {onRemove ? (
        <Button
          variant="ghost"
          className="px-1 py-0 text-xs"
          aria-label={removeLabel}
          onClick={onRemove}
        >
          ×
        </Button>
      ) : null}
    </div>
  )
}
