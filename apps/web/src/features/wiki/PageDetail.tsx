/** 문서 한 편. 보기·편집·이력. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { PageNode, WikiPage } from '@ieum/api-client'
import { formatDateTime } from '@/features/issues/format'
import { useUserNames } from '@/features/issues/hooks'
import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { saveBlob } from '@/shared/download'
import { DocumentPlaceProvider, RichText } from '@/shared/markdown/RichText'
import { MarkdownEditor } from '@/shared/markdown/MarkdownEditor'
import { Alert, Badge, Button, Card, Field } from '@/shared/ui/primitives'

import { Comments } from './Comments'
import { ancestorsOf } from './tree'
import { usePageHistory } from './hooks'

export interface PageDetailProps {
  page: WikiPage
  allNodes: PageNode[]
  spaceKey: string
  onChanged: () => void
}

export function PageDetail({ page, allNodes, spaceKey, onChanged }: PageDetailProps) {
  const { t } = useTranslation(['wiki', 'common'])
  const [editing, setEditing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  // 인용 하이라이트는 렌더된 DOM 위에 얹는다. 그래서 본문 요소가 필요하다.
  const body = useRef<HTMLDivElement | null>(null)

  // 자기 자신은 제목으로 따로 그린다. 빵부스러기에는 조상만 남긴다.
  const trail = ancestorsOf(allNodes, page.id).slice(0, -1)

  const archive = useMutation({
    mutationFn: () => wikiApi.pages.archive(page.id),
    onSuccess: onChanged,
  })

  const exportMd = useMutation({
    mutationFn: async () => {
      saveBlob(await wikiApi.pages.exportMarkdown(page.id), `${page.slug}.md`)
    },
  })

  return (
    <article className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        {trail.length > 0 ? (
          <nav aria-label={t('wiki:page.breadcrumb')} className="flex flex-wrap gap-1 text-xs">
            {trail.map((node) => (
              <span key={node.id} className="text-muted">
                <Link
                  to="/wiki/$spaceKey/$"
                  params={{ spaceKey, _splat: node.path }}
                  className="hover:text-accent hover:underline"
                >
                  {node.title}
                </Link>
                <span className="mx-1" aria-hidden="true">
                  /
                </span>
              </span>
            ))}
          </nav>
        ) : null}

        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold">{page.title}</h1>
          {page.status === 'draft' ? <Badge>{t('wiki:page.draft')}</Badge> : null}
          {page.archived_at ? <Badge tone="done">{t('wiki:page.archived')}</Badge> : null}
          <span className="ml-auto flex shrink-0 gap-2">
            <Button
              variant="ghost"
              className="text-xs"
              loading={exportMd.isPending}
              onClick={() => { exportMd.mutate() }}
            >
              {t('wiki:transfer.exportPage')}
            </Button>
            <Button variant="ghost" className="text-xs" onClick={() => { setShowHistory((v) => !v) }}>
              {t('wiki:page.history')}
            </Button>
            <Button variant="secondary" onClick={() => { setEditing((v) => !v) }}>
              {editing ? t('common:action.cancel') : t('wiki:page.edit')}
            </Button>
          </span>
        </div>

        <p className="text-xs text-muted">
          {t('wiki:page.versionLine', {
            number: page.version_number ?? 0,
            when: formatDateTime(page.updated_at),
          })}
        </p>
      </header>

      {archive.isError ? <Alert>{describeError(archive.error)}</Alert> : null}
      {exportMd.isError ? <Alert>{describeError(exportMd.error)}</Alert> : null}

      {editing ? (
        <PageEditor
          page={page}
          onSaved={() => { setEditing(false); onChanged() }}
          onCancel={() => { setEditing(false) }}
        />
      ) : (
        <Card>
          {page.body.trim() ? (
            <div ref={body}>
              {/* `::children` 은 지금 문서가 어디에 있는지 알아야 한다. */}
              <DocumentPlaceProvider place={{ spaceKey, path: page.path, nodes: allNodes }}>
                <RichText source={page.body} />
              </DocumentPlaceProvider>
            </div>
          ) : (
            <p className="text-sm text-muted">{t('wiki:page.emptyBody')}</p>
          )}
        </Card>
      )}

      {page.labels.length > 0 ? (
        <p className="flex flex-wrap gap-1.5 text-xs">
          {page.labels.map((label) => (
            <span key={label} className="rounded bg-surface-raised px-1.5 py-0.5 text-muted">
              {label}
            </span>
          ))}
        </p>
      ) : null}

      {showHistory ? <History page={page} onRestored={onChanged} /> : null}

      {!editing ? (
        <Comments
          pageId={page.id}
          versionNumber={page.version_number}
          bodyRef={body}
          bodyKey={page.body}
        />
      ) : null}

      {!page.archived_at && !editing ? (
        <Button
          variant="ghost"
          className="self-start text-xs"
          loading={archive.isPending}
          onClick={() => { archive.mutate() }}
        >
          {t('wiki:page.archive')}
        </Button>
      ) : null}
    </article>
  )
}

function PageEditor({
  page,
  onSaved,
  onCancel,
}: {
  page: WikiPage
  onSaved: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const [title, setTitle] = useState(page.title)
  const [body, setBody] = useState(page.body)
  const [message, setMessage] = useState('')
  const [labels, setLabels] = useState(page.labels.join(', '))

  const save = useMutation({
    mutationFn: () =>
      wikiApi.pages.update(
        page.id,
        {
          title,
          body,
          labels: labels.split(',').map((l) => l.trim()).filter(Boolean),
          ...(message.trim() ? { message: message.trim() } : {}),
          // 초안을 고쳐 저장하면 게시한다. 안 그러면 고쳤는데 아무도 못 본다.
          ...(page.status === 'draft' ? { publish: true } : {}),
        },
        page.version,
      ),
    onSuccess: onSaved,
  })

  return (
    <Card>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => { event.preventDefault(); save.mutate() }}
      >
        {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
        <Field
          label={t('wiki:page.title')}
          required
          value={title}
          onChange={(e) => { setTitle(e.target.value) }}
        />
        <MarkdownEditor label={t('wiki:page.body')} value={body} onChange={setBody} rows={18} />
        <Field
          label={t('wiki:page.labels')}
          hint={t('wiki:page.labelsHint')}
          value={labels}
          onChange={(e) => { setLabels(e.target.value) }}
        />
        <Field
          label={t('wiki:page.message')}
          hint={t('wiki:page.messageHint')}
          value={message}
          onChange={(e) => { setMessage(e.target.value) }}
        />
        <div className="flex gap-2">
          <Button type="submit" loading={save.isPending}>
            {t('common:action.save')}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            {t('common:action.cancel')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function History({ page, onRestored }: { page: WikiPage; onRestored: () => void }) {
  const { t } = useTranslation(['wiki', 'common'])
  const queryClient = useQueryClient()
  const history = usePageHistory(page.id)
  const names = useUserNames((history.data ?? []).map((v) => v.author_id))

  const restore = useMutation({
    mutationFn: (number: number) => wikiApi.pages.restore(page.id, number),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['wiki', 'history', page.id] })
      onRestored()
    },
  })

  return (
    <Card className="flex flex-col gap-2">
      <h2 className="text-sm font-medium text-muted">{t('wiki:page.history')}</h2>
      {restore.isError ? <Alert>{describeError(restore.error)}</Alert> : null}
      {history.isPending ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {(history.data ?? []).map((version) => (
            <li key={version.id} className="flex items-baseline gap-2">
              <span className="w-10 shrink-0 font-mono text-xs text-muted">
                v{version.number}
              </span>
              <span className="text-xs text-muted">{formatDateTime(version.created_at)}</span>
              <span className="text-xs">
                {version.author_id ? (names.data?.get(version.author_id) ?? '…') : '—'}
              </span>
              {version.message ? (
                <span className="min-w-0 truncate text-xs text-muted">{version.message}</span>
              ) : null}
              {version.number !== page.version_number ? (
                <Button
                  variant="ghost"
                  className="ml-auto shrink-0 text-xs"
                  loading={restore.isPending}
                  onClick={() => { restore.mutate(version.number) }}
                >
                  {t('wiki:page.restore')}
                </Button>
              ) : (
                <span className="ml-auto shrink-0 text-xs text-muted">
                  {t('wiki:page.current')}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {/* 되감지 않고 새 판을 만든다. 그래서 이력이 사라지지 않는다. */}
      <p className="text-xs text-muted">{t('wiki:page.restoreHint')}</p>
    </Card>
  )
}
