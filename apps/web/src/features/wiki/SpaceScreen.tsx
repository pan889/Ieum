/**
 * 스페이스 한 곳. 왼쪽에 문서 트리, 오른쪽에 문서.
 *
 * 열려 있는 문서는 URL 이 소유한다(`/wiki/ENG/deploy/rollback`). 링크 하나로
 * 같은 문서가 열려야 하고, 새로고침·뒤로가기가 같은 경로를 타야 한다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import clsx from 'clsx'
import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { saveBlob } from '@/shared/download'
import { Alert, Button, EmptyState, Field, Select } from '@/shared/ui/primitives'

import { BlogList } from './Blog'
import { PageDetail } from './PageDetail'
import { WatchButton } from '@/features/notifications/WatchButton'

import { Templates } from './Templates'
import { usePageByPath, useSpaceByKey, useSpaceTree } from './hooks'
import { buildTree, flatten } from './tree'
import type { TreeNode } from './tree'

export function SpaceScreen() {
  // 이 화면은 `/wiki/$spaceKey` 와 `/wiki/$spaceKey/$` 두 경로를 함께 맡는다.
  // strict 로는 한쪽만 고를 수 있어서 느슨하게 받고 여기서 좁힌다.
  const params = useParams({ strict: false })
  const spaceKey = params.spaceKey ?? ''
  const path = (params._splat ?? '').replace(/^\/+|\/+$/g, '')

  const { t } = useTranslation(['wiki', 'common'])
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const space = useSpaceByKey(spaceKey || null)
  const tree = useSpaceTree(space.data?.id ?? null)
  // `blog` 는 글 목록이다. 그 아래(`blog/<slug>`)는 보통 문서와 똑같이 열린다
  // — 블로그 글도 문서라서 주소 규칙을 따로 두지 않았다.
  const showingBlog = path === 'blog'
  const page = usePageByPath(spaceKey || null, showingBlog ? null : path || null)

  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [creatingUnder, setCreatingUnder] = useState<string | null | undefined>(undefined)
  const [showTemplates, setShowTemplates] = useState(false)

  const nodes = useMemo(() => buildTree(tree.data ?? []), [tree.data])
  const rows = useMemo(() => flatten(nodes, collapsed), [nodes, collapsed])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['wiki', 'tree', space.data?.id] })
    void queryClient.invalidateQueries({ queryKey: ['wiki', 'page', spaceKey] })
    // 블로그 목록은 글의 앞부분을 싣는다. 글을 고치고 목록으로 돌아가면
    // 옛 앞부분이 남아 있다.
    void queryClient.invalidateQueries({ queryKey: ['wiki', 'blog', space.data?.id] })
    // 코멘트는 여기서 건드리지 않는다. 판 번호가 키에 들어 있어 본문이
    // 바뀌면 저절로 다시 받는다. 여기서 무효화하면 휴지통으로 보낸 직후
    // 사라진 문서의 코멘트를 부르러 가서 404 가 난다.
  }

  if (space.isPending) {
    return <p className="text-sm text-muted">{t('common:state.loading')}</p>
  }
  if (space.isError) {
    return <Alert>{describeError(space.error)}</Alert>
  }

  return (
    <section className="flex gap-6">
      <nav className="w-64 shrink-0">
        <header className="mb-2 flex items-baseline gap-2">
          <Link to="/wiki" className="text-xs text-accent hover:underline">
            {t('wiki:spaces.title')}
          </Link>
          <span className="truncate text-sm font-semibold">{space.data.name}</span>
        </header>

        <Button
          variant="ghost"
          className="w-full justify-start text-xs"
          onClick={() => { setCreatingUnder(null) }}
        >
          {t('wiki:page.newTop')}
        </Button>

        <Link
          to="/wiki/$spaceKey/$"
          params={{ spaceKey, _splat: 'blog' }}
          className={clsx(
            'rounded-md px-2 py-1.5 text-xs',
            showingBlog ? 'bg-surface-raised font-medium text-fg' : 'text-muted hover:text-fg',
          )}
        >
          {t('wiki:blog.nav')}
        </Link>

        <WatchButton target="space" id={space.data.id} />

        <Transfer spaceId={space.data.id} spaceKey={spaceKey} onImported={refresh} />

        <Button
          variant="ghost"
          className="mb-2 w-full justify-start text-xs"
          onClick={() => { setShowTemplates((v) => !v) }}
        >
          {t('wiki:template.title')}
        </Button>

        {creatingUnder !== undefined ? (
          <NewPageForm
            spaceId={space.data.id}
            parentId={creatingUnder}
            onDone={(created) => {
              setCreatingUnder(undefined)
              refresh()
              if (created) {
                void navigate({
                  to: '/wiki/$spaceKey/$',
                  params: { spaceKey, _splat: created },
                })
              }
            }}
          />
        ) : null}

        {tree.isPending ? (
          <p className="text-xs text-muted">{t('common:state.loading')}</p>
        ) : rows.length === 0 ? (
          <p className="text-xs text-muted">{t('wiki:tree.empty')}</p>
        ) : (
          <ul className="flex flex-col">
            {rows.map((node) => (
              <TreeRow
                key={node.id}
                node={node}
                spaceKey={spaceKey}
                active={node.path === path}
                collapsed={collapsed.has(node.id)}
                onToggle={() => {
                  setCollapsed((current) => {
                    const next = new Set(current)
                    if (next.has(node.id)) next.delete(node.id)
                    else next.add(node.id)
                    return next
                  })
                }}
                onAddChild={() => { setCreatingUnder(node.id) }}
              />
            ))}
          </ul>
        )}
      </nav>

      <div className="min-w-0 flex-1">
        {showTemplates ? <Templates spaceId={space.data.id} /> : null}
        {showingBlog ? (
          <BlogList
            spaceId={space.data.id}
            spaceKey={spaceKey}
            onCreated={(created) => {
              void navigate({ to: '/wiki/$spaceKey/$', params: { spaceKey, _splat: created } })
            }}
          />
        ) : !path ? (
          <EmptyState
            title={t('wiki:page.pickOne')}
            description={t('wiki:page.pickOneHint')}
          />
        ) : page.isPending ? (
          <p className="text-sm text-muted">{t('common:state.loading')}</p>
        ) : page.isError ? (
          <Alert>{describeError(page.error)}</Alert>
        ) : (
          <PageDetail
            page={page.data}
            allNodes={tree.data ?? []}
            spaceKey={spaceKey}
            onChanged={refresh}
          />
        )}
      </div>
    </section>
  )
}

function TreeRow({
  node,
  spaceKey,
  active,
  collapsed,
  onToggle,
  onAddChild,
}: {
  node: TreeNode
  spaceKey: string
  active: boolean
  collapsed: boolean
  onToggle: () => void
  onAddChild: () => void
}) {
  const { t } = useTranslation(['wiki'])
  const hasChildren = node.children.length > 0

  return (
    <li className="group flex items-center gap-0.5 text-sm">
      {hasChildren ? (
        <button
          type="button"
          className="w-4 shrink-0 text-xs text-muted hover:text-fg"
          aria-label={
            collapsed
              ? t('wiki:tree.expand', { title: node.title })
              : t('wiki:tree.collapse', { title: node.title })
          }
          aria-expanded={!collapsed}
          onClick={onToggle}
        >
          {collapsed ? '▸' : '▾'}
        </button>
      ) : (
        <span className="w-4 shrink-0" aria-hidden="true" />
      )}

      <Link
        to="/wiki/$spaceKey/$"
        params={{ spaceKey, _splat: node.path }}
        className={clsx(
          'min-w-0 flex-1 truncate rounded px-1 py-1 hover:bg-surface-raised',
          active ? 'bg-surface-raised font-medium text-accent' : 'text-fg',
        )}
        style={{ paddingLeft: `${String(node.depth * 0.75 + 0.25)}rem` }}
        aria-current={active ? 'page' : undefined}
      >
        {node.title}
        {/* 초안은 아직 게시되지 않았다. 트리에서 구분이 안 되면 다 쓴 줄 안다. */}
        {node.status === 'draft' ? (
          <span className="ml-1 text-xs text-muted">{t('wiki:page.draftMark')}</span>
        ) : null}
      </Link>

      <button
        type="button"
        className="shrink-0 px-1 text-xs text-muted opacity-0 group-hover:opacity-100 hover:text-accent"
        aria-label={t('wiki:page.newUnder', { title: node.title })}
        onClick={onAddChild}
      >
        +
      </button>
    </li>
  )
}

function NewPageForm({
  spaceId,
  parentId,
  onDone,
}: {
  spaceId: string
  parentId: string | null
  onDone: (createdPath: string | null) => void
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const [title, setTitle] = useState('')
  const [templateId, setTemplateId] = useState('')

  const templates = useQuery({
    queryKey: ['wiki', 'templates', spaceId],
    queryFn: () => wikiApi.spaces.templates.list(spaceId),
    staleTime: 60_000,
  })

  const create = useMutation({
    mutationFn: () =>
      wikiApi.pages.create({
        space_id: spaceId,
        title,
        parent_id: parentId,
        // 고른 템플릿이 첫 본문이 된다. 뼈대를 매번 손으로 옮겨 적게 하면
        // 결국 아무도 규격을 안 지킨다.
        body: templates.data?.find((row) => row.id === templateId)?.body ?? '',
        // 바로 게시한다. 방금 만든 문서가 트리에서 초안으로만 보이면
        // "안 만들어졌나" 하고 다시 만든다.
        publish: true,
      }),
    onSuccess: (page) => { onDone(page.path) },
  })

  return (
    <form
      className="mb-2 flex flex-col gap-2"
      onSubmit={(event) => { event.preventDefault(); create.mutate() }}
    >
      {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
      <Field
        label={t('wiki:page.title')}
        className="text-sm"
        autoFocus
        value={title}
        onChange={(e) => { setTitle(e.target.value) }}
      />
      {(templates.data ?? []).length > 0 ? (
        <Select
          label={t('wiki:template.pick')}
          className="text-sm"
          value={templateId}
          onChange={(e) => { setTemplateId(e.target.value) }}
        >
          <option value="">{t('wiki:template.blank')}</option>
          {(templates.data ?? []).map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </Select>
      ) : null}
      <div className="flex gap-1">
        <Button type="submit" className="text-xs" loading={create.isPending} disabled={!title.trim()}>
          {t('common:action.create')}
        </Button>
        <Button variant="ghost" className="text-xs" onClick={() => { onDone(null) }}>
          {t('common:action.cancel')}
        </Button>
      </div>
    </form>
  )
}

/**
 * `.md` 가져오기·내보내기.
 *
 * 가져오기는 스토리지를 거치지 않는다 — 첨부와 달리 서버가 내용을 **읽어야**
 * 하고, 크기가 제한돼 있어 요청 하나로 끝난다.
 */
function Transfer({
  spaceId,
  spaceKey,
  onImported,
}: {
  spaceId: string
  spaceKey: string
  onImported: () => void
}) {
  const { t } = useTranslation(['wiki'])
  const input = useRef<HTMLInputElement>(null)

  const importFile = useMutation({
    mutationFn: (file: File) => wikiApi.spaces.importFile(spaceId, file),
    onSuccess: onImported,
  })

  const exportZip = useMutation({
    mutationFn: async () => {
      saveBlob(await wikiApi.spaces.exportZip(spaceId), `${spaceKey}.zip`)
    },
  })

  /**
   * 인쇄물로 내보낸다 (B17). ZIP 과 **다른 일**이다: ZIP 은 옮기기 위한 것,
   * 이쪽은 읽히기 위한 것이다 — 문서 트리 순서가 장 순서인 한 부의 책.
   */
  const exportPaper = useMutation({
    mutationFn: async (word: boolean) => {
      const blob = word
        ? await wikiApi.spaces.exportDocx(spaceId)
        : await wikiApi.spaces.exportPdf(spaceId)
      saveBlob(blob, `${spaceKey}.${word ? 'docx' : 'pdf'}`)
    },
  })

  /** 어느 형식이 지금 만들어지고 있나. 버튼마다 자기 것만 돌아야 한다. */
  const busy = (word: boolean) => exportPaper.isPending && exportPaper.variables === word

  return (
    <div className="mb-2 flex flex-col gap-1">
      {/*
        **묶음에 이름을 준다.** 문서 쪽에도 같은 이름의 PDF·Word 버튼이 있어서,
        이름만으로는 "이 스페이스를 내보내는 버튼" 과 "이 문서를 내보내는
        버튼" 이 구별되지 않는다 — 스크린리더로 듣는 사람에게는 그 둘이 같은
        말이다 (ux-principles "묶음에는 묶음의 이름을 준다").
      */}
      <div className="flex gap-1" role="group" aria-label={t('wiki:transfer.spaceGroup')}>
        <Button
          variant="ghost"
          className="text-xs"
          loading={importFile.isPending}
          onClick={() => { input.current?.click() }}
        >
          {t('wiki:transfer.import')}
        </Button>
        <Button
          variant="ghost"
          className="text-xs"
          loading={exportZip.isPending}
          onClick={() => { exportZip.mutate() }}
        >
          {t('wiki:transfer.export')}
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
      </div>
      <input
        ref={input}
        type="file"
        className="hidden"
        accept=".md,.markdown,.zip"
        aria-label={t('wiki:transfer.import')}
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) importFile.mutate(file)
          // 같은 파일을 다시 고를 수 있게 비운다. 안 비우면 change 가 안 난다.
          event.target.value = ''
        }}
      />
      {importFile.isError ? <Alert>{describeError(importFile.error)}</Alert> : null}
      {exportZip.isError ? <Alert>{describeError(exportZip.error)}</Alert> : null}
      {exportPaper.isError ? <Alert>{describeError(exportPaper.error)}</Alert> : null}
    </div>
  )
}
