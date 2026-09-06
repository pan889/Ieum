/**
 * 문서 코멘트 (wiki-markdown.md 6절).
 *
 * 본문 일부를 드래그하면 그 자리에 코멘트를 단다. 문서가 고쳐져 인용문이
 * 사라지면 **고아**가 되는데, 조용히 지우지 않고 원문 인용과 함께 남긴다.
 * 소리 없이 사라지는 코멘트는 아무도 믿지 않는다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { PageComment } from '@ieum/api-client'
import { formatDateTime } from '@/features/issues/format'
import { useUserNames } from '@/features/issues/hooks'
import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Markdown } from '@/shared/markdown/Markdown'
import { MarkdownEditor } from '@/shared/markdown/MarkdownEditor'
import { Alert, Badge, Button, Card } from '@/shared/ui/primitives'

import { anchorFromSelection, findQuoteSegments } from './anchors'
import type { DraftAnchor } from './anchors'

const HIGHLIGHT = 'ieum-quote'

/**
 * 문서 코멘트.
 *
 * 판 번호가 키에 들어간다. 앵커가 붙는 자리는 **그 판의 본문**에서 정해지므로
 * 본문이 바뀌면 결과도 달라진다 — 키에 없으면 화면은 계속 "잘 붙어 있음"
 * 이라고 말한다. 저장·복원이 모두 새 판을 만들므로 이걸로 충분하다.
 */
export function usePageComments(pageId: string | null, versionNumber: number | null) {
  return useQuery({
    queryKey: ['wiki', 'comments', pageId, versionNumber],
    queryFn: () => wikiApi.pages.comments.list(pageId as string),
    enabled: pageId !== null,
  })
}

export interface CommentsProps {
  pageId: string
  versionNumber: number | null
  /** 렌더된 본문. 인용을 여기서 찾아 표시한다. */
  bodyRef: React.RefObject<HTMLDivElement | null>
  /** 본문이 다시 그려질 때마다 바뀌는 값. 하이라이트를 다시 칠할 신호다. */
  bodyKey: string
}

export function Comments({ pageId, versionNumber, bodyRef, bodyKey }: CommentsProps) {
  const { t } = useTranslation(['wiki', 'common'])
  const queryClient = useQueryClient()
  const comments = usePageComments(pageId, versionNumber)
  const [draft, setDraft] = useState<DraftAnchor | null>(null)
  const [showResolved, setShowResolved] = useState(false)

  // 새 배열이 매 렌더 생기면 하이라이트를 다시 칠하는 effect 가 끝없이 돈다.
  const rows = useMemo(() => comments.data ?? [], [comments.data])
  const names = useUserNames(rows.map((c) => c.author_id))
  // 접두사로 무효화한다 — 판 번호까지 맞출 필요가 없다.
  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['wiki', 'comments', pageId] })
  }, [queryClient, pageId])

  // 인용 자리에 표시를 얹는다. 서버가 이미 고아 여부를 정했으므로 여기서
  // 못 찾아도 코멘트는 목록에 그대로 남는다.
  useEffect(() => {
    const container = bodyRef.current
    if (!container) return
    const painted = paint(container, rows)
    return () => { painted() }
  }, [bodyRef, rows, bodyKey])

  const startDraft = () => {
    // 본문이 아직 안 그려졌을 수 있다 — 빈 문서이거나, 방금 저장해서 질의가
    // 갱신되기 전이거나. 그때 조용히 돌아가면 버튼을 눌러도 아무 일이
    // 안 일어난다. 고를 자리가 없을 뿐이지 문서 전체 코멘트는 늘 달 수 있다.
    const container = bodyRef.current
    const next = container ? anchorFromSelection(container, window.getSelection()) : null
    // 아무것도 안 골랐으면 문서 전체 코멘트를 연다.
    setDraft(next ?? { exact: '', prefix: '', suffix: '', occurrence: 1 })
  }

  const open = rows.filter((c) => c.resolved_at === null)
  const resolved = rows.filter((c) => c.resolved_at !== null)
  const shown = showResolved ? rows : open

  return (
    <section className="flex flex-col gap-3" aria-label={t('wiki:comments.title')}>
      <header className="flex items-baseline gap-2">
        <h2 className="text-sm font-medium text-muted">
          {t('wiki:comments.title')} ({open.length})
        </h2>
        <Button variant="ghost" className="text-xs" onClick={startDraft}>
          {t('wiki:comments.add')}
        </Button>
        {resolved.length > 0 ? (
          <Button
            variant="ghost"
            className="ml-auto text-xs"
            onClick={() => { setShowResolved((v) => !v) }}
          >
            {showResolved
              ? t('wiki:comments.hideResolved')
              : t('wiki:comments.showResolved', { count: resolved.length })}
          </Button>
        ) : null}
      </header>

      {draft ? (
        <NewComment
          pageId={pageId}
          versionNumber={versionNumber}
          draft={draft}
          onDone={() => { setDraft(null); invalidate() }}
        />
      ) : (
        <p className="text-xs text-muted">{t('wiki:comments.selectHint')}</p>
      )}

      {comments.isError ? <Alert>{describeError(comments.error)}</Alert> : null}
      {shown.length === 0 && !comments.isPending ? (
        <p className="text-sm text-muted">{t('wiki:comments.empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-2">
        {shown
          .filter((c) => c.parent_id === null)
          .map((comment) => (
            <li key={comment.id}>
              <CommentCard
                comment={comment}
                replies={rows.filter((r) => r.parent_id === comment.id)}
                names={names.data ?? new Map()}
                onChanged={invalidate}
                bodyRef={bodyRef}
              />
            </li>
          ))}
      </ul>
    </section>
  )
}

/**
 * 인용 자리에 표시를 얹고, 걷어내는 함수를 돌려준다.
 *
 * 텍스트 노드 **조각마다** 감싼다. 범위 하나를 `surroundContents` 로 감싸면
 * 태그 경계를 걸친 인용에서 던진다 — `**굵게**` 뒤로 조금 이어지는 인용이
 * 딱 그 경우고, 그때 하이라이트가 통째로 사라졌다.
 */
function paint(container: HTMLElement, comments: PageComment[]): () => void {
  const marks: HTMLElement[] = []
  for (const comment of comments) {
    if (!comment.anchor || comment.anchor_status === 'orphaned') continue
    if (comment.resolved_at !== null) continue
    // 퍼지로 붙었으면 인용문과 지금 글자가 다르다. 서버가 실제로 찾은
    // 글자로 칠해야 표시가 따라간다 — 원래 인용문으로 찾으면 못 찾는다.
    const segments = findQuoteSegments(container, {
      ...comment.anchor,
      exact: comment.match?.found ?? comment.anchor.exact,
    })
    if (!segments) continue
    // 뒤에서부터 감싼다. 앞을 먼저 쪼개면 뒤 조각의 오프셋이 밀린다.
    for (const segment of [...segments].reverse()) {
      const range = document.createRange()
      range.setStart(segment.node, segment.start)
      range.setEnd(segment.node, segment.end)
      const mark = document.createElement('mark')
      mark.className = HIGHLIGHT
      mark.dataset['commentId'] = comment.id
      range.surroundContents(mark)
      marks.push(mark)
    }
  }
  return () => {
    for (const mark of marks) {
      const parent = mark.parentNode
      if (!parent) continue
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
      parent.removeChild(mark)
      parent.normalize()
    }
  }
}

function NewComment({
  pageId,
  versionNumber,
  draft,
  onDone,
}: {
  pageId: string
  versionNumber: number | null
  draft: DraftAnchor
  onDone: () => void
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const [body, setBody] = useState('')

  const add = useMutation({
    mutationFn: () =>
      wikiApi.pages.comments.add(pageId, {
        body,
        ...(draft.exact
          ? { anchor: { ...draft, version_number: versionNumber } }
          : {}),
      }),
    onSuccess: onDone,
  })

  return (
    <Card className="flex flex-col gap-2">
      {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
      {draft.exact ? (
        <blockquote className="border-l-2 border-accent pl-2 text-xs text-muted">
          {draft.exact}
        </blockquote>
      ) : (
        <p className="text-xs text-muted">{t('wiki:comments.wholePage')}</p>
      )}
      <MarkdownEditor
        label={t('wiki:comments.body')}
        value={body}
        onChange={setBody}
        rows={4}
        attachTo={{ ownerType: 'page', ownerId: pageId }}
      />
      <div className="flex gap-2">
        <Button
          className="text-xs"
          loading={add.isPending}
          disabled={!body.trim()}
          onClick={() => { add.mutate() }}
        >
          {t('wiki:comments.post')}
        </Button>
        <Button variant="ghost" className="text-xs" onClick={onDone}>
          {t('common:action.cancel')}
        </Button>
      </div>
    </Card>
  )
}

function CommentCard({
  comment,
  replies,
  names,
  onChanged,
  bodyRef,
}: {
  comment: PageComment
  replies: PageComment[]
  names: ReadonlyMap<string, string>
  onChanged: () => void
  bodyRef: React.RefObject<HTMLDivElement | null>
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const [replying, setReplying] = useState(false)
  const [replyBody, setReplyBody] = useState('')

  const resolve = useMutation({
    mutationFn: () => wikiApi.pages.comments.resolve(comment.id, comment.resolved_at === null),
    onSuccess: onChanged,
  })
  const remove = useMutation({
    mutationFn: () => wikiApi.pages.comments.remove(comment.id),
    onSuccess: onChanged,
  })
  const reply = useMutation({
    mutationFn: () =>
      wikiApi.pages.comments.add(comment.page_id, { body: replyBody, parent_id: comment.id }),
    onSuccess: () => { setReplying(false); setReplyBody(''); onChanged() },
  })

  const scrollToQuote = () => {
    const container = bodyRef.current
    if (!container || !comment.anchor) return
    const found = container.querySelector(`[data-comment-id="${comment.id}"]`)
    found?.scrollIntoView({ block: 'center' })
  }

  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2 text-xs text-muted">
        <span className="font-medium text-fg">
          {comment.author_id ? (names.get(comment.author_id) ?? '…') : '—'}
        </span>
        <span>{formatDateTime(comment.created_at)}</span>
        {comment.edited_at ? <span>{t('wiki:comments.edited')}</span> : null}
        {comment.anchor_status === 'orphaned' ? (
          <Badge tone="todo">{t('wiki:comments.orphaned')}</Badge>
        ) : null}
        {comment.resolved_at ? <Badge tone="done">{t('wiki:comments.resolved')}</Badge> : null}
      </div>

      {comment.anchor ? (
        <blockquote
          className={
            comment.anchor_status === 'orphaned'
              ? 'border-l-2 border-border pl-2 text-xs text-muted line-through'
              : 'border-l-2 border-accent pl-2 text-xs text-muted'
          }
        >
          {comment.anchor.exact}
        </blockquote>
      ) : null}
      {comment.anchor_status === 'orphaned' ? (
        // 인용문이 사라졌다고 코멘트를 지우지 않는다. 왜 남았는지 말해 준다.
        <p className="text-xs text-muted">{t('wiki:comments.orphanedHint')}</p>
      ) : null}

      <Markdown source={comment.body} className="text-sm" />

      {resolve.isError ? <Alert>{describeError(resolve.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      <div className="flex flex-wrap gap-1 text-xs">
        <Button variant="ghost" className="text-xs" onClick={() => { setReplying((v) => !v) }}>
          {t('wiki:comments.reply')}
        </Button>
        <Button
          variant="ghost"
          className="text-xs"
          loading={resolve.isPending}
          onClick={() => { resolve.mutate() }}
        >
          {comment.resolved_at ? t('wiki:comments.reopen') : t('wiki:comments.resolve')}
        </Button>
        {comment.anchor && comment.anchor_status === 'ok' ? (
          <Button variant="ghost" className="text-xs" onClick={scrollToQuote}>
            {t('wiki:comments.jump')}
          </Button>
        ) : null}
        <Button
          variant="ghost"
          className="ml-auto text-xs"
          loading={remove.isPending}
          onClick={() => { remove.mutate() }}
        >
          {t('common:action.delete')}
        </Button>
      </div>

      {replying ? (
        <div className="flex flex-col gap-2">
          {reply.isError ? <Alert>{describeError(reply.error)}</Alert> : null}
          <MarkdownEditor
            label={t('wiki:comments.body')}
            value={replyBody}
            onChange={setReplyBody}
            rows={3}
            attachTo={{ ownerType: 'page', ownerId: comment.page_id }}
          />
          <Button
            className="self-start text-xs"
            loading={reply.isPending}
            disabled={!replyBody.trim()}
            onClick={() => { reply.mutate() }}
          >
            {t('wiki:comments.post')}
          </Button>
        </div>
      ) : null}

      {replies.length > 0 ? (
        <ul className="flex flex-col gap-2 border-l border-border pl-3">
          {replies.map((child) => (
            <li key={child.id} className="flex flex-col gap-1">
              <div className="flex items-baseline gap-2 text-xs text-muted">
                <span className="font-medium text-fg">
                  {child.author_id ? (names.get(child.author_id) ?? '…') : '—'}
                </span>
                <span>{formatDateTime(child.created_at)}</span>
              </div>
              <Markdown source={child.body} className="text-sm" />
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  )
}

/** 코멘트 폼이 열려 있는 동안 선택이 사라지지 않게 잡아 둔다. */
export function useKeepSelection(): React.RefObject<string> {
  const held = useRef('')
  useEffect(() => {
    const remember = () => {
      const text = window.getSelection()?.toString() ?? ''
      if (text.trim()) held.current = text
    }
    document.addEventListener('selectionchange', remember)
    return () => { document.removeEventListener('selectionchange', remember) }
  }, [])
  return held
}
