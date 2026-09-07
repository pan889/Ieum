/**
 * 스페이스 블로그 (B13). 뉴스·공지가 여기로 들어온다 (A23).
 *
 * 트리에 안 들어간다 — 날짜순으로 흐르는 글이라 위치가 아니라 시간이 자리를
 * 정한다. 그래도 **문서와 같은 것**이다: 판 이력·코멘트·검색·권한이 전부
 * 그대로 붙는다. 글 하나를 여는 것도 문서와 같은 주소 규칙(`blog/<slug>`)을
 * 타므로 여기서 따로 열지 않는다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { formatDateTime } from '@/features/issues/format'
import { firstParagraph } from '@/shared/markdown/directives'
import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

const PAGE_SIZE = 20

export function BlogList({
  spaceId,
  spaceKey,
  onCreated,
}: {
  spaceId: string
  spaceKey: string
  onCreated: (path: string) => void
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const [writing, setWriting] = useState(false)
  const [offset, setOffset] = useState(0)

  const posts = useQuery({
    queryKey: ['wiki', 'blog', spaceId, offset],
    queryFn: () => wikiApi.spaces.blog(spaceId, { limit: PAGE_SIZE, offset }),
  })

  const items = posts.data?.items ?? []
  const total = posts.data?.total ?? 0

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-xl font-semibold">{t('wiki:blog.title')}</h1>
        <Button className="ml-auto" onClick={() => { setWriting((v) => !v) }}>
          {t('wiki:blog.new')}
        </Button>
      </div>

      {writing ? (
        <NewPostForm
          spaceId={spaceId}
          onDone={(path) => {
            setWriting(false)
            if (path) onCreated(path)
          }}
        />
      ) : null}

      {posts.isError ? <Alert>{describeError(posts.error)}</Alert> : null}
      {items.length === 0 && !posts.isPending ? (
        <p className="text-sm text-muted">{t('wiki:blog.empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-3">
        {items.map((post) => (
          <li key={post.id}>
            <Card className="flex flex-col gap-1">
              <Link
                to="/wiki/$spaceKey/$"
                params={{ spaceKey, _splat: post.path }}
                className="text-base font-medium text-fg hover:underline"
              >
                {post.title}
              </Link>
              <p className="text-xs text-muted">
                {formatDateTime(post.published_at ?? post.created_at)}
              </p>
              {/* 앞부분만 보여 준다. 목록에서 본문을 통째로 그리면 스크롤이
                  끝나지 않고, 무엇이 있는지도 오히려 안 보인다. */}
              <p className="text-sm text-muted">{firstParagraph(post.body)}</p>
            </Card>
          </li>
        ))}
      </ul>

      {total > PAGE_SIZE ? (
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            className="text-xs"
            disabled={offset === 0}
            onClick={() => { setOffset((n) => Math.max(0, n - PAGE_SIZE)) }}
          >
            {t('common:pagination.previous')}
          </Button>
          <Button
            variant="secondary"
            className="text-xs"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => { setOffset((n) => n + PAGE_SIZE) }}
          >
            {t('common:pagination.next')}
          </Button>
          <span className="text-xs text-muted">
            {t('common:pagination.itemCount', { count: total })}
          </span>
        </div>
      ) : null}
    </div>
  )
}

function NewPostForm({
  spaceId,
  onDone,
}: {
  spaceId: string
  onDone: (path: string | null) => void
}) {
  const { t } = useTranslation(['wiki', 'common'])
  const queryClient = useQueryClient()
  const [title, setTitle] = useState('')

  const create = useMutation({
    mutationFn: () =>
      wikiApi.pages.create({
        space_id: spaceId,
        title,
        kind: 'blog',
        // 바로 게시한다. 초안으로 두면 목록에 안 뜨고 "안 만들어졌나" 가 된다.
        publish: true,
      }),
    onSuccess: async (post) => {
      await queryClient.invalidateQueries({ queryKey: ['wiki', 'blog', spaceId] })
      onDone(post.path)
    },
  })

  return (
    <Card>
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => { event.preventDefault(); create.mutate() }}
      >
        {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
        <Field
          label={t('wiki:blog.postTitle')}
          autoFocus
          required
          value={title}
          onChange={(event) => { setTitle(event.target.value) }}
        />
        <div className="flex gap-2">
          <Button type="submit" loading={create.isPending} disabled={!title.trim()}>
            {t('wiki:blog.create')}
          </Button>
          <Button variant="ghost" onClick={() => { onDone(null) }}>
            {t('common:action.cancel')}
          </Button>
        </div>
      </form>
    </Card>
  )
}
