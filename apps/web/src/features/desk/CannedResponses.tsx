/**
 * 정형 응답 (feature-map C10).
 *
 * 프로젝트 단위의 **공용** 자료다. 소유자를 두지 않는 것이 의도다: 개인
 * 스니펫이면 사람이 떠날 때 같이 사라지고, 그 사람이 쓰던 문구를 다음
 * 사람이 다시 만든다.
 *
 * **치환을 하지 않는다.** `{{고객이름}}` 같은 것을 넣기 시작하면 값이 없을
 * 때 무엇을 내보낼지 정해야 하고, 그 답은 언제나 "고객에게 빈칸이나 중괄호가
 * 배달된다" 로 끝난다. 끼워 넣기는 **초안**이지 발송이 아니다 — 상담원이
 * 보고 고친다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { CannedResponse } from '@ieum/api-client'

import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field, Textarea } from '@/shared/ui/primitives'

const EMPTY = { name: '', body: '', shortcut: '' }

export function CannedResponses({ projectId }: { projectId: string }) {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<CannedResponse | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY)

  const responses = useQuery({
    queryKey: ['canned', projectId],
    queryFn: () => deskApi.listCannedResponses(projectId),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['canned', projectId] })

  const save = useMutation({
    mutationFn: () => {
      const shortcut = form.shortcut.trim()
      return editing
        ? deskApi.updateCannedResponse(editing.id, {
            name: form.name,
            body: form.body,
            // 비운 것은 **지우라는 뜻**이다. `shortcut: null` 을 그렇게 읽지
            // 않기로 했으므로(이름만 고치는 요청이 단축어를 날린다) 손잡이를
            // 따로 보낸다.
            ...(shortcut === '' ? { clear_shortcut: true } : { shortcut }),
          })
        : deskApi.createCannedResponse({
            project_id: projectId,
            name: form.name,
            body: form.body,
            shortcut: shortcut === '' ? null : shortcut,
          })
    },
    onSuccess: async () => {
      setAdding(false)
      setEditing(null)
      setForm(EMPTY)
      await refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => deskApi.deleteCannedResponse(id),
    onSuccess: () => refresh(),
  })

  const rows = responses.data ?? []

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h2 className="text-sm font-medium">{t('desk:canned.title')}</h2>
          <p className="text-xs text-muted">{t('desk:canned.description')}</p>
        </div>
        <Button
          type="button"
          variant="ghost"
          className="text-xs"
          onClick={() => {
            setEditing(null)
            setForm(EMPTY)
            setAdding(true)
          }}
        >
          {t('desk:canned.add')}
        </Button>
      </div>

      {responses.isError ? <Alert>{describeError(responses.error)}</Alert> : null}
      {responses.isSuccess && rows.length === 0 && !adding ? (
        <p className="text-sm text-muted">{t('desk:canned.empty')}</p>
      ) : null}

      <ul className="flex flex-col divide-y divide-border">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <span className="font-medium">{row.name}</span>
            {row.shortcut ? (
              <code className="text-xs text-muted">/{row.shortcut}</code>
            ) : null}
            <span className="min-w-0 flex-1 truncate text-xs text-muted">{row.body}</span>
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => {
                setAdding(false)
                setEditing(row)
                setForm({ name: row.name, body: row.body, shortcut: row.shortcut ?? '' })
              }}
            >
              {t('common:action.edit')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="text-xs"
              onClick={() => { remove.mutate(row.id) }}
            >
              {t('common:action.delete')}
            </Button>
          </li>
        ))}
      </ul>

      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {adding || editing ? (
        <form
          className="flex flex-col gap-2 border-t border-border pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
          <Field
            label={t('desk:canned.name')}
            value={form.name}
            onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
          />
          <Field
            label={t('desk:canned.shortcut')}
            hint={t('desk:canned.shortcutHint')}
            value={form.shortcut}
            onChange={(event) => { setForm({ ...form, shortcut: event.target.value }) }}
          />
          <Textarea
            label={t('desk:canned.body')}
            rows={5}
            value={form.body}
            onChange={(event) => { setForm({ ...form, body: event.target.value }) }}
          />
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              loading={save.isPending}
              disabled={form.name.trim() === '' || form.body.trim() === ''}
            >
              {t('common:action.save')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setAdding(false)
                setEditing(null)
              }}
            >
              {t('common:action.cancel')}
            </Button>
          </div>
        </form>
      ) : null}
    </Card>
  )
}
