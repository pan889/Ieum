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
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'

import { PageDetail } from './PageDetail'
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
  const page = usePageByPath(spaceKey || null, path || null)

  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [creatingUnder, setCreatingUnder] = useState<string | null | undefined>(undefined)
  const [showTemplates, setShowTemplates] = useState(false)

  const nodes = useMemo(() => buildTree(tree.data ?? []), [tree.data])
  const rows = useMemo(() => flatten(nodes, collapsed), [nodes, collapsed])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['wiki', 'tree', space.data?.id] })
    void queryClient.invalidateQueries({ queryKey: ['wiki', 'page', spaceKey] })
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
        {!path ? (
          <Card className="text-center">
            <p className="font-medium">{t('wiki:page.pickOne')}</p>
            <p className="mt-1 text-sm text-muted">{t('wiki:page.pickOneHint')}</p>
          </Card>
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

  return (
    <div className="mb-2 flex flex-col gap-1">
      <div className="flex gap-1">
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
    </div>
  )
}
