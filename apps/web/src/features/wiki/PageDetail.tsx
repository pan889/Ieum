/** 문서 한 편. 보기·편집·이력. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { PageDraft, PageNode, WikiPage } from '@ieum/api-client'
import { formatDateTime } from '@/features/issues/format'
import { WatchButton } from '@/features/notifications/WatchButton'
import { useUserNames } from '@/features/issues/hooks'
import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { saveBlob } from '@/shared/download'
import { DocumentPlaceProvider, RichText } from '@/shared/markdown/RichText'
import { MarkdownEditor } from '@/shared/markdown/MarkdownEditor'
import { Alert, Badge, Button, Card, Field } from '@/shared/ui/primitives'

import { useAuthStore } from '@/features/auth/store'

import { Comments } from './Comments'
import { Presence } from './Presence'
import { TaskPanel } from './TaskPanel'
import { VersionDiff } from './VersionDiff'
import { ancestorsOf } from './tree'
import { usePageHistory } from './hooks'
import { useCollab } from './useCollab'

/** 손을 멈추고 이만큼 지나면 저장한다. */
const AUTOSAVE_DELAY_MS = 2000

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

  const copy = useMutation({
    mutationFn: () =>
      wikiApi.pages.copy(page.id, {
        new_parent_id: page.parent_id,
        title: t('wiki:page.copySuffix', { title: page.title }),
      }),
    onSuccess: onChanged,
  })

  const exportMd = useMutation({
    mutationFn: async () => {
      saveBlob(await wikiApi.pages.exportMarkdown(page.id), `${page.slug}.md`)
    },
  })

  /**
   * 인쇄물로 내보낸다 (B17). 조판은 **서버가** 한다 — 브라우저 인쇄에 미루면
   * 쪽 번호와 페이지 나눔이 브라우저마다 다르고, 받는 사람이 "인쇄" 를 눌러
   * 주기를 기대할 수 없다. 파일 하나가 나와야 메일에 붙일 수 있다.
   */
  const exportPaper = useMutation({
    mutationFn: async (word: boolean) => {
      const blob = word
        ? await wikiApi.pages.exportDocx(page.id)
        : await wikiApi.pages.exportPdf(page.id)
      saveBlob(blob, `${page.slug}.${word ? 'docx' : 'pdf'}`)
    },
  })

  /** 어느 형식이 지금 만들어지고 있나. 버튼마다 자기 것만 돌아야 한다. */
  const busy = (word: boolean) => exportPaper.isPending && exportPaper.variables === word

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
            {/* 문서를 구독하면 이 문서가 바뀔 때 알림이 온다. 스페이스를
                구독하면 그 안의 문서 전부가 온다 (notify 6절). */}
            <WatchButton target="page" id={page.id} />
            <Button
              variant="ghost"
              className="text-xs"
              loading={exportMd.isPending}
              onClick={() => { exportMd.mutate() }}
            >
              {t('wiki:transfer.exportPage')}
            </Button>
            <Button
              variant="ghost"
              className="text-xs"
              loading={busy(false)}
              onClick={() => { exportPaper.mutate(false) }}
            >
              {t('wiki:transfer.exportPdf')}
            </Button>
            <Button
              variant="ghost"
              className="text-xs"
              loading={busy(true)}
              onClick={() => { exportPaper.mutate(true) }}
            >
              {t('wiki:transfer.exportWord')}
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
      {exportPaper.isError ? <Alert>{describeError(exportPaper.error)}</Alert> : null}
      {copy.isError ? <Alert>{describeError(copy.error)}</Alert> : null}

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

      {/* 태스크는 코멘트보다 위에 둔다 — 문서를 열었을 때 "내가 할 것" 이
          먼저 보여야 한다. 태스크가 없는 문서에는 아무것도 안 그려진다. */}
      {!editing ? <TaskPanel page={page} /> : null}

      {!editing ? (
        <Comments
          pageId={page.id}
          versionNumber={page.version_number}
          bodyRef={body}
          bodyKey={page.body}
        />
      ) : null}

      {!page.archived_at && !editing ? (
        <div className="flex gap-2">
          <Button
            variant="ghost"
            className="text-xs"
            loading={copy.isPending}
            onClick={() => { copy.mutate() }}
          >
            {t('wiki:page.copy')}
          </Button>
          <Button
            variant="ghost"
            className="text-xs"
            loading={archive.isPending}
            onClick={() => { archive.mutate() }}
          >
            {t('wiki:page.archive')}
          </Button>
        </div>
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
  const draft = useQuery({
    queryKey: ['wiki', 'draft', page.id],
    queryFn: () => wikiApi.pages.draft.get(page.id),
    // 열 때 한 번만 본다. 자동 저장이 계속 덮어쓰므로 다시 받을 이유가 없다.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })

  // 초안을 먼저 물어보고 그 답으로 초기값을 정한다. 나중에 setState 로
  // 덮으면 사용자가 이미 친 글자를 지운다.
  if (draft.isPending) return <Card>{t('common:state.loading')}</Card>
  return (
    <PageEditorForm
      key={page.id}
      page={page}
      draft={draft.data ?? null}
      onSaved={onSaved}
      onCancel={onCancel}
    />
  )
}

function PageEditorForm({
  page,
  draft,
  onSaved,
  onCancel,
}: {
  page: WikiPage
  draft: PageDraft | null
  onSaved: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const queryClient = useQueryClient()
  // 저장 안 한 편집이 있으면 그걸로 연다. 사람은 마지막으로 친 글을 기대한다.
  const restored = draft !== null && draft.body !== page.body
  const [title, setTitle] = useState(restored ? draft.title || page.title : page.title)
  const [body, setBody] = useState(restored ? draft.body : page.body)
  const [message, setMessage] = useState('')
  const [labels, setLabels] = useState(page.labels.join(', '))
  const [savedAt, setSavedAt] = useState<string | null>(restored ? draft.updated_at : null)

  /**
   * 같이 편집한다 (B16).
   *
   * 붙기 전에는 내 상태(`body`)를 쓰고, 붙은 뒤에는 **공유 문서가 본문의
   * 임자**다. 둘을 동시에 진짜로 두지 않는 것이 요점이다 — 두 벌이면 남의
   * 편집을 내 상태가 덮는다.
   */
  const me = useAuthStore((s) => s.user)
  const collab = useCollab(
    page.id,
    me === null ? null : { userId: me.id, name: me.display_name },
    true,
  )
  const shared = collab.text !== null
  const text = shared ? (collab.text ?? '') : body
  /**
   * **공유 문서가 곧 초안이다.**
   *
   * 붙어 있으면 개인 초안을 안 쓴다(아래). 그런데 화면의 "초안 저장됨" 과
   * "버리기" 는 개인 초안만 알고 있었으므로, 붙은 뒤에는 저장 안 한 편집이
   * 남는데도 그렇게 말해 주는 것이 하나도 없고 버리기는 눌러도 아무 일이
   * 없었다 — 브라우저 시험 둘이 그 자리에서 붉었다.
   *
   * 지금은 공유 문서를 초안으로 본다: 게시된 본문과 다르면 "저장 안 한
   * 편집이 있다" 이고, 버리면 그 문서를 게시된 본문으로 되돌린다.
   */
  const sharedDirty = shared && text !== page.body
  const setText = (next: string) => {
    if (shared) collab.write(next)
    else setBody(next)
  }

  const autosave = useMutation({
    mutationFn: () =>
      wikiApi.pages.draft.save(page.id, {
        title,
        body,
        base_version: page.version_number,
      }),
    onSuccess: (saved) => { setSavedAt(saved.updated_at) },
  })

  // 손을 멈추면 저장한다. 매 글자마다 보내면 요청이 폭주하고, 시간 간격만
  // 두면 마지막 몇 글자를 잃는다.
  const dirty = title !== page.title || text !== page.body
  useEffect(() => {
    // **공유 문서가 있으면 개인 초안을 쓰지 않는다.** 두 초안이 같은 문서를
    // 두고 다투면, 다음에 문서를 열 때 어느 쪽이 뜨는지 아무도 모른다.
    if (!dirty || shared) return
    const timer = setTimeout(() => { autosave.mutate() }, AUTOSAVE_DELAY_MS)
    return () => { clearTimeout(timer) }
    // autosave 는 매 렌더 새 객체다. 의존에 넣으면 타이머가 끝없이 다시 선다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, text, dirty, shared])

  const save = useMutation({
    mutationFn: () =>
      wikiApi.pages.update(
        page.id,
        {
          title,
          // 게시는 **지금 보이는 본문**으로 한다. 공유 문서가 있으면 그쪽이
          // 본문이고, 없으면 내 상태다.
          body: text,
          labels: labels.split(',').map((l) => l.trim()).filter(Boolean),
          ...(message.trim() ? { message: message.trim() } : {}),
          // 초안을 고쳐 저장하면 게시한다. 안 그러면 고쳤는데 아무도 못 본다.
          ...(page.status === 'draft' ? { publish: true } : {}),
        },
        page.version,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['wiki', 'draft', page.id] })
      onSaved()
    },
  })

  const discard = useMutation({
    mutationFn: async () => {
      // **공유 문서를 먼저 되돌린다.** 개인 초안만 지우면 화면은 그대로
      // 공유 문서를 보여 주므로 버리기가 아무 일도 안 한 것처럼 보인다.
      // 되돌리기도 편집이므로 같이 보고 있는 사람의 화면에서도 그렇게 된다 —
      // 그게 맞다: 버린 편집을 남이 계속 보고 있을 이유가 없다.
      if (shared) collab.write(page.body)
      await wikiApi.pages.draft.discard(page.id)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['wiki', 'draft', page.id] })
      setTitle(page.title)
      setBody(page.body)
      setSavedAt(null)
    },
  })

  return (
    <Card>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => { event.preventDefault(); save.mutate() }}
      >
        {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
        {restored || sharedDirty ? (
          <p className="flex items-baseline gap-2 text-xs text-muted">
            {restored
              ? t('wiki:draft.restored', { when: formatDateTime(draft.updated_at) })
              : t('wiki:draft.sharedUnsaved')}
            <Button
              variant="ghost"
              className="text-xs"
              loading={discard.isPending}
              onClick={() => { discard.mutate() }}
            >
              {t('wiki:draft.discard')}
            </Button>
          </p>
        ) : null}
        <Field
          label={t('wiki:page.title')}
          required
          value={title}
          onChange={(e) => { setTitle(e.target.value) }}
        />
        <Presence status={collab.status} peers={collab.peers} />
        <MarkdownEditor
          label={t('wiki:page.body')}
          value={text}
          onChange={setText}
          rows={18}
          attachTo={{ ownerType: 'page', ownerId: page.id }}
          sourceRef={collab.bindSource}
        />
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
        <div className="flex items-center gap-2">
          <Button type="submit" loading={save.isPending}>
            {t('common:action.save')}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            {t('common:action.cancel')}
          </Button>
          {/* 자동 저장은 조용해야 한다. 다만 저장됐다는 사실은 보여야
              사람이 창을 닫을 수 있다. */}
          {/*
            공유 문서일 때도 저장됐다는 말을 한다. 방이 몇 초마다, 그리고
            **닫을 때 반드시** 남기므로(`rooms.py`) 창을 닫아도 남는다 —
            그것이 사람이 이 줄을 보고 판단하는 것이다.
          */}
          <span className="text-xs text-muted">
            {autosave.isPending
              ? t('wiki:draft.saving')
              : sharedDirty
                ? t('wiki:draft.sharedSaved')
                : savedAt
                  ? t('wiki:draft.savedAt', { when: formatDateTime(savedAt) })
                  : ''}
          </span>
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
  // 비교할 판. 하나만 고르면 지금 판과 견준다.
  const [comparing, setComparing] = useState<number | null>(null)

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
                <>
                  <Button
                    variant="ghost"
                    className="ml-auto shrink-0 text-xs"
                    onClick={() => {
                      setComparing((v) => (v === version.number ? null : version.number))
                    }}
                  >
                    {comparing === version.number
                      ? t('wiki:diff.hide')
                      : t('wiki:diff.compare')}
                  </Button>
                  <Button
                    variant="ghost"
                    className="shrink-0 text-xs"
                    loading={restore.isPending}
                    onClick={() => { restore.mutate(version.number) }}
                  >
                    {t('wiki:page.restore')}
                  </Button>
                </>
              ) : (
                <span className="ml-auto shrink-0 text-xs text-muted">
                  {t('wiki:page.current')}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {comparing !== null && page.version_number !== null ? (
        <VersionDiff pageId={page.id} before={comparing} after={page.version_number} />
      ) : null}

      {/* 되감지 않고 새 판을 만든다. 그래서 이력이 사라지지 않는다. */}
      <p className="text-xs text-muted">{t('wiki:page.restoreHint')}</p>
    </Card>
  )
}
