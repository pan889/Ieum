import { useMutation } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Project } from '@ieum/api-client'

import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field, Select } from '@/shared/ui/primitives'
import { MarkdownEditor } from '@/shared/markdown/MarkdownEditor'

import { CustomField } from './CustomField'
import type { FieldValue } from './customFields'
import { priorityLabel } from './format'
import { useFieldDefinitions, useIssueTypes } from './hooks'

export function NewIssueScreen() {
  const { t } = useTranslation(['issues', 'common'])
  const navigate = useNavigate()
  // `?parent=ENG-1` 로 들어오면 상위 이슈를 미리 채운다. 상세 화면의
  // "하위 이슈 만들기" 가 이 경로로 보낸다.
  const search = useSearch({ from: '/issues/new' })

  // 프로젝트는 **검색으로** 고른다. 목록을 통째로 `<select>` 에 넣으면
  // 설치가 커진 순간 뒤쪽 프로젝트에 이슈를 만들 길이 사라진다 — 개발 DB 가
  // 2334개가 됐을 때 실제로 그랬다(allProjects.ts, ux-principles 4절).
  const [project, setProject] = useState<Project | null>(null)
  const projectId = project?.id ?? null
  //: 사용자가 직접 고른 유형. null 이면 그 프로젝트의 첫 유형을 쓴다.
  const [pickedTypeId, setPickedTypeId] = useState<string | null>(null)
  const [summary, setSummary] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState(3)
  const [dueDate, setDueDate] = useState('')
  const [labels, setLabels] = useState('')
  const [parentKey, setParentKey] = useState(search.parent ?? '')
  // 서버가 받는 모양 그대로 담는다. 숫자 필드에 문자열을 보내면 거절당한다.
  const [custom, setCustom] = useState<Record<string, FieldValue>>({})

  const types = useIssueTypes(projectId)
  // 기본값은 effect 로 넣지 않는다 — 렌더 중에 계산하면 한 번 덜 그린다.
  const typeId = pickedTypeId ?? types.data?.[0]?.id ?? null
  const fields = useFieldDefinitions(projectId, typeId)

  // 비운 필드는 아예 안 보낸다. null 을 보내면 필수 필드 검사에서
  // '채우지 않음'과 똑같이 걸리지만, 요청만 커진다.
  const filled = Object.fromEntries(
    Object.entries(custom).filter(([, value]) => value !== null),
  )

  const create = useMutation({
    mutationFn: async () => {
      // 사람은 키로 말한다. id 는 여기서 찾는다 — UUID 를 복사하게 만들면
      // 아무도 하위 이슈를 안 만든다.
      const parent = parentKey.trim()
        ? await issuesApi.getByKey(parentKey.trim().toUpperCase())
        : null
      return issuesApi.create({
        project_id: projectId as string,
        summary,
        ...(typeId ? { type_id: typeId } : {}),
        ...(description ? { description } : {}),
        priority,
        ...(dueDate ? { due_date: dueDate } : {}),
        ...(labels.trim()
          ? { labels: labels.split(',').map((l) => l.trim()).filter(Boolean) }
          : {}),
        ...(Object.keys(filled).length > 0 ? { custom_fields: filled } : {}),
        ...(parent ? { parent_id: parent.id } : {}),
      })
    },
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

          <ProjectPicker
            label={t('issues:create.project')}
            chosen={project}
            onPick={(picked) => {
              // 프로젝트가 바뀌면 고른 유형은 그 프로젝트에 없을 수 있다.
              setProject(picked)
              setPickedTypeId(null)
            }}
          />

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
            label={t('issues:create.parent')}
            hint={t('issues:create.parentHint')}
            placeholder="ENG-123"
            value={parentKey}
            onChange={(e) => { setParentKey(e.target.value); }}
          />

          <Field
            label={t('issues:create.labels')}
            hint={t('issues:create.labelsHint')}
            value={labels}
            onChange={(e) => { setLabels(e.target.value); }}
          />

          {(fields.data ?? []).map((definition) => (
            <CustomField
              key={definition.id}
              definition={definition}
              projectId={projectId}
              value={custom[definition.key] ?? null}
              onChange={(value) => {
                setCustom((prev) => ({ ...prev, [definition.key]: value }))
              }}
            />
          ))}

          {/* 눌리지 않는 버튼만 두지 않는다 — 무엇이 남았는지 옆에 적는다
              (ux-principles). `<select required>` 를 걷어내면서 유일한 단서가
              사라졌다. */}
          <div className="flex items-center gap-3">
            <Button type="submit" loading={create.isPending} disabled={projectId === null}>
              {t('issues:create.submit')}
            </Button>
            {projectId === null ? (
              <span className="text-xs text-muted">{t('issues:create.projectFirst')}</span>
            ) : null}
          </div>
        </form>
      </Card>
    </section>
  )
}
