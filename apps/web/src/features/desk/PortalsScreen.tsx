/**
 * 포털과 요청 폼.
 *
 * 포털은 프로젝트에 붙으므로 **프로젝트를 먼저 고른다.** 전체 포털 목록을
 * 주는 API 를 두지 않은 이유가 그것이다 — 권한이 프로젝트 단위이므로
 * "볼 수 있는 것만" 을 합치면 화면이 왜 어떤 프로젝트만 보이는지 설명할 수
 * 없다.
 *
 * **슬러그는 만들 때만 정한다.** 고객에게 배포된 URL 이라 바꾸면 저장해 둔
 * 링크와 메일에 적힌 주소가 죽는다. 편집 폼에 그것이 없고, 왜 없는지 적어
 * 둔다(커스텀 필드의 키·종류와 같은 판단).
 *
 * 포털을 **지우지 않는다.** 지우면 요청 폼이 CASCADE 로 함께 사라지고,
 * 그러면 이미 들어온 티켓의 `request_type_id` 가 NULL 이 되어 "어떤 폼으로
 * 들어왔나" 를 영구히 잃는다. 접는 것으로 충분하다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Portal, Project } from '@ieum/api-client'

import { ProjectPicker } from '@/features/settings/ProjectPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { deskApi, projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Checkbox, Field } from '@/shared/ui/primitives'

import { RequestTypes } from './RequestTypes'

const EMPTY = { name: '', slug: '', description: '', is_public: false }

export function PortalsScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [picked, setPicked] = useState<Project | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [openPortal, setOpenPortal] = useState<string | null>(null)

  // 처음 열었을 때 빈 화면에서 "왜 아무것도 없나" 를 고민하게 만들지
  // 않는다. 첫 프로젝트를 기본값으로 쓰되, **고르는 길은 검색**이다 —
  // 목록을 통째로 드롭다운에 넣으면 프로젝트가 백 개를 넘는 설치에서
  // 마지막 프로젝트의 포털을 볼 수 없다(ux-principles 4절, 두 번 어겼다).
  const first = useQuery({
    queryKey: ['projects', 'first'],
    queryFn: () => projectsApi.list({ limit: 1 }),
  })
  const fallback = first.data?.items[0] ?? null
  const chosenProject = picked ?? fallback
  const chosen = chosenProject?.id ?? ''

  const portals = useQuery({
    queryKey: ['portals', chosen],
    queryFn: () => deskApi.listPortals(chosen),
    enabled: chosen.length > 0,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['portals', chosen] })

  const create = useMutation({
    mutationFn: () =>
      deskApi.createPortal({
        project_id: chosen,
        name: form.name,
        slug: form.slug,
        ...(form.description ? { description: form.description } : {}),
        is_public: form.is_public,
      }),
    onSuccess: async () => {
      setAdding(false)
      setForm(EMPTY)
      await refresh()
    },
  })

  const setPublic = useMutation({
    mutationFn: (portal: Portal) =>
      deskApi.updatePortal(portal.id, { is_public: !portal.is_public }),
    onSuccess: refresh,
  })

  const archive = useMutation({
    mutationFn: (portal: Portal) =>
      portal.is_archived ? deskApi.restorePortal(portal.id) : deskApi.archivePortal(portal.id),
    onSuccess: refresh,
  })

  const rows = portals.data ?? []

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('desk:portals.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('desk:portals.description')}</p>
        </div>
        <Button
          className="ml-auto shrink-0 text-xs"
          variant={adding ? 'ghost' : 'primary'}
          disabled={!chosen}
          onClick={() => { setAdding(!adding) }}
        >
          {adding ? t('common:action.cancel') : t('desk:portals.add')}
        </Button>
      </header>

      <ProjectPicker
        label={t('desk:portals.pickProject')}
        chosen={chosenProject}
        onPick={setPicked}
      />

      {portals.isError ? <Alert>{describeError(portals.error)}</Alert> : null}
      {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
      {setPublic.isError ? <Alert>{describeError(setPublic.error)}</Alert> : null}

      {adding ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              create.mutate()
            }}
          >
            <div className="flex flex-wrap items-end gap-3">
              <Field
                label={t('desk:portals.name')}
                required
                value={form.name}
                onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
              />
              <Field
                label={t('desk:portals.slug')}
                hint={t('desk:portals.slugHint')}
                required
                value={form.slug}
                onChange={(event) => { setForm({ ...form, slug: event.target.value }) }}
              />
            </div>
            <Field
              label={t('desk:portals.description')}
              value={form.description}
              onChange={(event) => { setForm({ ...form, description: event.target.value }) }}
            />
            <Checkbox
              label={t('desk:portals.isPublic')}
              hint={t('desk:portals.isPublicHint')}
              checked={form.is_public}
              onChange={(event) => { setForm({ ...form, is_public: event.target.checked }) }}
            />
            <Button type="submit" className="self-start" disabled={create.isPending}>
              {t('common:action.create')}
            </Button>
          </form>
        </Card>
      ) : null}

      {/* 조회가 실패했으면 "없다" 고 말하지 않는다 — 목록이 빈 것과
          못 읽은 것은 다르다. */}
      {portals.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:portals.empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-2">
        {rows.map((portal) => (
          <li key={portal.id} className="rounded-md border border-border bg-surface">
            <div className="flex flex-wrap items-baseline gap-2 px-4 py-3">
              <span className="text-sm font-medium">{portal.name}</span>
              {portal.is_public ? <Badge tone="info">{t('desk:portals.isPublic')}</Badge> : null}
              {portal.is_archived ? (
                <Badge tone="danger">{t('desk:portals.archived')}</Badge>
              ) : null}
              <span className="text-xs text-muted">
                {t('desk:portals.requestTypes', { count: portal.request_type_count })}
              </span>
              <Button
                className="ml-auto text-xs"
                variant="ghost"
                onClick={() => { setOpenPortal(openPortal === portal.id ? null : portal.id) }}
              >
                {t('desk:requestTypes.title')}
              </Button>
              <Button
                className="text-xs"
                variant="ghost"
                onClick={() => { setPublic.mutate(portal) }}
              >
                {portal.is_public ? t('desk:portals.makePrivate') : t('desk:portals.makePublic')}
              </Button>
              <Button
                className="text-xs"
                variant="ghost"
                title={t('desk:portals.noDelete')}
                onClick={() => { archive.mutate(portal) }}
              >
                {portal.is_archived ? t('desk:portals.restore') : t('desk:portals.archive')}
              </Button>
            </div>

            {/* 고객이 여는 주소를 그대로 보여 준다. 관리자가 이 링크를 복사해
                고객에게 붙여 준다 — 화면 어딘가에서 조립하게 하면 슬러그를
                손으로 옮겨 적다가 틀린다. */}
            <p className="px-4 pb-3 text-xs text-muted">
              {t('desk:portals.customerUrl')}:{' '}
              <a className="underline" href={`/portal/${portal.slug}`}>
                /portal/{portal.slug}
              </a>
            </p>

            {openPortal === portal.id ? (
              <div className="border-t border-border px-4 py-3">
                <RequestTypes portal={portal} onChanged={() => {
                  void refresh()
                }} />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
