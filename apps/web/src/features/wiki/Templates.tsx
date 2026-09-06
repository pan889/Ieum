/**
 * 문서 템플릿 (회의록·결정기록·요구사항).
 *
 * 만드는 것은 스페이스 관리자만 할 수 있다 — 서버가 막는다. 화면은 버튼을
 * 숨기지 않고 거절 사유를 보여 준다. 숨기면 왜 안 되는지 알 수 없고,
 * 숨김은 어차피 보안이 아니다 (절대규칙 2).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { MarkdownEditor } from '@/shared/markdown/MarkdownEditor'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

export function Templates({ spaceId }: { spaceId: string }) {
  const { t } = useTranslation(['wiki', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [body, setBody] = useState('')

  const templates = useQuery({
    queryKey: ['wiki', 'templates', spaceId],
    queryFn: () => wikiApi.spaces.templates.list(spaceId),
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['wiki', 'templates', spaceId] })
  }

  const add = useMutation({
    mutationFn: () => wikiApi.spaces.templates.create(spaceId, { name, body }),
    onSuccess: () => { setAdding(false); setName(''); setBody(''); refresh() },
  })
  const remove = useMutation({
    mutationFn: (id: string) => wikiApi.spaces.templates.remove(id),
    onSuccess: refresh,
  })

  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <h2 className="text-sm font-medium text-muted">{t('wiki:template.title')}</h2>
        <Button
          variant="ghost"
          className="ml-auto text-xs"
          onClick={() => { setAdding((v) => !v) }}
        >
          {adding ? t('common:action.cancel') : t('wiki:template.add')}
        </Button>
      </div>

      {adding ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => { event.preventDefault(); add.mutate() }}
        >
          {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
          <Field
            label={t('wiki:template.name')}
            className="text-sm"
            value={name}
            onChange={(e) => { setName(e.target.value) }}
          />
          <MarkdownEditor label={t('wiki:template.body')} value={body} onChange={setBody} rows={8} />
          <Button type="submit" className="self-start text-xs" loading={add.isPending} disabled={!name.trim()}>
            {t('wiki:template.save')}
          </Button>
        </form>
      ) : null}

      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
      {(templates.data ?? []).length === 0 ? (
        <p className="text-xs text-muted">{t('wiki:template.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {(templates.data ?? []).map((row) => (
            <li key={row.id} className="flex items-baseline gap-2">
              <span>{row.name}</span>
              {row.space_id === null ? (
                <span className="text-xs text-muted">{t('wiki:template.global')}</span>
              ) : null}
              <Button
                variant="ghost"
                className="ml-auto text-xs"
                loading={remove.isPending}
                onClick={() => { remove.mutate(row.id) }}
              >
                {t('common:action.delete')}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
