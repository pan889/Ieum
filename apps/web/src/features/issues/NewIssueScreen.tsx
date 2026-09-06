import { useMutation } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'
import { MarkdownEditor } from '@/shared/markdown/MarkdownEditor'

import { priorityLabel } from './format'
import { useFieldDefinitions, useIssueTypes, useProjects } from './hooks'

export function NewIssueScreen() {
  const { t } = useTranslation(['issues', 'common'])
  const navigate = useNavigate()

  const projects = useProjects()
  const [projectId, setProjectId] = useState<string | null>(null)
  //: 사용자가 직접 고른 유형. null 이면 그 프로젝트의 첫 유형을 쓴다.
  const [pickedTypeId, setPickedTypeId] = useState<string | null>(null)
  const [summary, setSummary] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState(3)
  const [dueDate, setDueDate] = useState('')
  const [labels, setLabels] = useState('')
  const [custom, setCustom] = useState<Record<string, string>>({})

  const types = useIssueTypes(projectId)
  // 기본값은 effect 로 넣지 않는다 — 렌더 중에 계산하면 한 번 덜 그린다.
  const typeId = pickedTypeId ?? types.data?.[0]?.id ?? null
  const fields = useFieldDefinitions(projectId, typeId)

  const create = useMutation({
    mutationFn: () =>
      issuesApi.create({
        project_id: projectId as string,
        summary,
        ...(typeId ? { type_id: typeId } : {}),
        ...(description ? { description } : {}),
        priority,
        ...(dueDate ? { due_date: dueDate } : {}),
        ...(labels.trim()
          ? { labels: labels.split(',').map((l) => l.trim()).filter(Boolean) }
          : {}),
        ...(Object.keys(custom).length > 0 ? { custom_fields: custom } : {}),
      }),
    onSuccess: (issue) => {
      void navigate({ to: '/issues/$issueKey', params: { issueKey: issue.key } })
    },
  })

  return (
    <section className="mx-auto max-w-2xl">
      <h1 className="text-xl font-semibold">{t('issues:create.title')}</h1>

      <Card className="mt-5">
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => { event.preventDefault(); create.mutate() }}
        >
          {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}

          <Select
            label={t('issues:create.project')}
            required
            value={projectId ?? ''}
            onChange={(e) => {
              // 프로젝트가 바뀌면 고른 유형은 그 프로젝트에 없을 수 있다.
              setProjectId(e.target.value || null)
              setPickedTypeId(null)
            }}
          >
            <option value="" disabled>
              {t('issues:list.projectAll')}
            </option>
            {projects.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.key} · {p.name}
              </option>
            ))}
          </Select>

          {types.data && types.data.length > 0 ? (
            <Select
              label={t('issues:create.type')}
              value={typeId ?? ''}
              onChange={(e) => { setPickedTypeId(e.target.value || null); }}
            >
              {types.data.map((type) => (
                <option key={type.id} value={type.id}>{type.name}</option>
              ))}
            </Select>
          ) : null}

          <Field
            label={t('issues:create.summary')}
            required
            value={summary}
            onChange={(e) => { setSummary(e.target.value); }}
          />

          <MarkdownEditor
            label={t('issues:create.description')}
            value={description}
            onChange={setDescription}
          />

          <Select
            label={t('issues:create.priority')}
            value={String(priority)}
            onChange={(e) => { setPriority(Number(e.target.value)); }}
          >
            {[1, 2, 3, 4, 5].map((p) => (
              <option key={p} value={p}>{priorityLabel(p)}</option>
            ))}
          </Select>

          <Field
            label={t('issues:create.dueDate')}
            type="date"
            value={dueDate}
            onChange={(e) => { setDueDate(e.target.value); }}
          />

          <Field
            label={t('issues:create.labels')}
            hint={t('issues:create.labelsHint')}
            value={labels}
            onChange={(e) => { setLabels(e.target.value); }}
          />

          {(fields.data ?? []).map((definition) => (
            <Field
              key={definition.id}
              label={definition.name}
              required={definition.is_required}
              {...(definition.description ? { hint: definition.description } : {})}
              value={custom[definition.key] ?? ''}
              onChange={(e) => {
                const value = e.target.value
                setCustom((prev) => ({ ...prev, [definition.key]: value }))
              }}
            />
          ))}

          <Button type="submit" loading={create.isPending} disabled={projectId === null}>
            {t('issues:create.submit')}
          </Button>
        </form>
      </Card>
    </section>
  )
}
