/**
 * 자산 대장 (C15, M6).
 *
 * **목록은 검색이다.** 자산은 수천 개가 되고, 이 화면은 커서 페이지로만
 * 앞으로 간다 — 서버에 상한 없는 목록을 주는 라우트가 없다.
 *
 * **나간 장비를 기본으로 감춘다.** 재고를 훑는 사람이 찾는 것은 지금 있는
 * 장비다. 감춘다는 것을 손잡이로 말하고, 켜면 보인다.
 *
 * **자산은 지워지지 않는다.** 티켓에 이어진 자산의 삭제는 서버가 거절하고
 * (이력이 곧 이 기능의 값이다), 화면은 그때 "사용 종료" 를 가리킨다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { AssetStatus, AssetType } from '@ieum/api-client'

import { formatDate } from '@/features/issues/format'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { assetsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Checkbox, Field, Select } from '@/shared/ui/primitives'

const STATUSES: AssetStatus[] = ['in_use', 'spare', 'repair', 'retired']
const PAGE = 25

const TONE: Record<AssetStatus, 'done' | 'neutral' | 'danger' | 'todo'> = {
  in_use: 'done',
  spare: 'neutral',
  repair: 'danger',
  retired: 'neutral',
}

/** 새 자산에 붙일 수 있는 종류. 보관은 "이제 이걸로는 등록하지 않는다" 다. */
export function usableTypes(rows: readonly AssetType[]): AssetType[] {
  return rows.filter((row) => !row.is_archived)
}

export interface TypeChoice {
  id: string
  label: string
}

/**
 * 걸러 보기에 올릴 종류. **보관한 종류도 남는다.**
 *
 * 보관해도 그 종류의 자산은 대장에 그대로 있다. 종류로 좁혀 보려는 사람에게
 * 이 드롭다운이 유일한 손잡이인데, 등록용 목록을 그대로 쓰면 그 자산들은
 * 이름으로 검색해야만 닿는다 — 이름을 모르니까 종류로 찾는 것이다.
 */
export function filterChoices(
  rows: readonly AssetType[],
  archived: (name: string) => string,
): TypeChoice[] {
  return rows.map((row) => ({
    id: row.id,
    label: row.is_archived ? archived(row.name) : row.name,
  }))
}

interface Draft {
  type_id: string
  name: string
  tag: string
  status: AssetStatus
  organization_id: string
  /** 고른 조직의 이름. 고른 것을 화면에 되비추는 데만 쓴다. */
  organization_name: string
  location: string
  note: string
}

function emptyDraft(): Draft {
  return {
    type_id: '',
    name: '',
    tag: '',
    status: 'in_use',
    organization_id: '',
    organization_name: '',
    location: '',
    note: '',
  }
}

export function AssetsScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()

  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [includeRetired, setIncludeRetired] = useState(false)
  const [cursor, setCursor] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [typeName, setTypeName] = useState('')
  const [orgTerm, setOrgTerm] = useState('')
  const [openAsset, setOpenAsset] = useState<string | null>(null)

  const types = useQuery({
    queryKey: ['assets', 'types'],
    queryFn: () => assetsApi.types(true),
  })
  // 조직 후보. **자산용 손잡이로 묻는다.**
  //
  // 조직 관리 목록(`deskApi.listOrgs`)은 step-up 을 요구한다 — 이 화면을 여는
  // 것만으로 403 이 나고 조직 칸이 죽는다. 서버에 `/assets/organizations` 가
  // 따로 있는 이유가 이것이고, 시험이 두 응답을 나란히 붙잡고 있다.
  //
  // 한 글자도 안 쳤으면 묻지 않는다: 앞의 열 개만 보여 주면 "이게 전부" 로
  // 읽힌다 (ux-principles 4절, 이 저장소가 세 번 겪은 일).
  const orgQuery = orgTerm.trim()
  const orgs = useQuery({
    queryKey: ['assets', 'organizations', orgQuery],
    queryFn: () => assetsApi.organizations({ q: orgQuery, limit: 10 }),
    enabled: adding && orgQuery.length > 0,
  })

  const trimmed = query.trim()
  const assets = useQuery({
    queryKey: ['assets', 'page', trimmed, typeFilter, includeRetired, cursor],
    queryFn: () =>
      assetsApi.search({
        limit: PAGE,
        ...(trimmed ? { q: trimmed } : {}),
        ...(typeFilter ? { type_id: typeFilter } : {}),
        ...(includeRetired ? { include_retired: true } : {}),
        ...(cursor ? { cursor } : {}),
      }),
  })

  const tickets = useQuery({
    queryKey: ['assets', 'tickets', openAsset],
    queryFn: () => assetsApi.tickets(openAsset as string),
    enabled: openAsset !== null,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['assets'] })

  const addType = useMutation({
    mutationFn: () => assetsApi.createType({ name: typeName.trim() }),
    onSuccess: async () => {
      setTypeName('')
      await refresh()
    },
  })

  const create = useMutation({
    mutationFn: () =>
      assetsApi.create({
        type_id: draft.type_id,
        name: draft.name.trim(),
        status: draft.status,
        ...(draft.tag.trim() ? { tag: draft.tag.trim() } : {}),
        ...(draft.organization_id ? { organization_id: draft.organization_id } : {}),
        ...(draft.location.trim() ? { location: draft.location.trim() } : {}),
        ...(draft.note.trim() ? { note: draft.note.trim() } : {}),
      }),
    onSuccess: async () => {
      setDraft(emptyDraft())
      setOrgTerm('')
      setAdding(false)
      await refresh()
    },
  })

  // 상태만 줄에서 바로 바꾼다. 장비가 수리로 들어가고 나오는 것이 이 화면에서
  // 가장 자주 하는 일이고, 그것 때문에 편집 폼을 여는 것은 과하다.
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: AssetStatus }) =>
      assetsApi.update(id, { status }),
    onSuccess: refresh,
  })

  const remove = useMutation({
    mutationFn: (id: string) => assetsApi.remove(id),
    onSuccess: refresh,
  })

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  const rows = assets.data?.items ?? []
  const usable = usableTypes(types.data ?? [])
  const choices = filterChoices(types.data ?? [], (name) =>
    t('desk:asset.typeArchived', { name }),
  )

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">{t('desk:asset.title')}</h1>
        <p className="text-sm text-muted">{t('desk:asset.description')}</p>
      </header>

      {/* 종류가 없으면 자산을 만들 수 없다. 그 사실을 먼저 말한다. */}
      <Card className="flex flex-col gap-2">
        <h2 className="text-sm font-medium">{t('desk:asset.types')}</h2>
        {addType.isError ? <Alert>{describeError(addType.error)}</Alert> : null}
        <div className="flex flex-wrap gap-1.5">
          {usable.map((row) => (
            <Badge key={row.id} tone="info">{row.name}</Badge>
          ))}
          {types.isSuccess && usable.length === 0 ? (
            <span className="text-sm text-muted">{t('desk:asset.noTypes')}</span>
          ) : null}
        </div>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => { event.preventDefault(); addType.mutate() }}
        >
          <Field
            label={t('desk:asset.typeName')}
            hint={t('desk:asset.typeNameHint')}
            value={typeName}
            onChange={(event) => { setTypeName(event.target.value) }}
          />
          <Button type="submit" loading={addType.isPending} disabled={typeName.trim() === ''}>
            {t('common:action.add')}
          </Button>
        </form>
      </Card>

      <div className="flex flex-wrap items-end gap-3">
        <Field
          label={t('desk:asset.find')}
          hint={t('desk:asset.findHint')}
          value={query}
          onChange={(event) => { setQuery(event.target.value); setCursor(null) }}
        />
        <Select
          label={t('desk:asset.typeFilter')}
          value={typeFilter}
          onChange={(event) => { setTypeFilter(event.target.value); setCursor(null) }}
        >
          <option value="">{t('desk:asset.allTypes')}</option>
          {choices.map((choice) => (
            <option key={choice.id} value={choice.id}>{choice.label}</option>
          ))}
        </Select>
        <Checkbox
          label={t('desk:asset.showRetired')}
          checked={includeRetired}
          onChange={(event) => { setIncludeRetired(event.target.checked); setCursor(null) }}
        />
        <Button variant={adding ? 'ghost' : 'primary'} onClick={() => { setAdding((v) => !v) }}>
          {adding ? t('common:action.cancel') : t('desk:asset.add')}
        </Button>
      </div>

      {adding ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            data-testid="asset-form"
            onSubmit={(event) => { event.preventDefault(); create.mutate() }}
          >
            {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
            <div className="flex flex-wrap items-end gap-3">
              <Select
                label={t('desk:asset.type')}
                value={draft.type_id}
                onChange={(event) => { set('type_id', event.target.value) }}
              >
                <option value="">{t('common:action.choose')}</option>
                {usable.map((row) => (
                  <option key={row.id} value={row.id}>{row.name}</option>
                ))}
              </Select>
              <Field
                label={t('desk:asset.name')}
                required
                value={draft.name}
                onChange={(event) => { set('name', event.target.value) }}
              />
              <Field
                label={t('desk:asset.tag')}
                hint={t('desk:asset.tagHint')}
                value={draft.tag}
                onChange={(event) => { set('tag', event.target.value) }}
              />
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <Select
                label={t('desk:asset.status')}
                value={draft.status}
                onChange={(event) => { set('status', event.target.value as AssetStatus) }}
              >
                {STATUSES.map((value) => (
                  <option key={value} value={value}>{t(`desk:asset.status.${value}`)}</option>
                ))}
              </Select>
              <Field
                label={t('desk:asset.location')}
                value={draft.location}
                onChange={(event) => { set('location', event.target.value) }}
              />
            </div>
            {/* 고객 조직. **고르는 것은 검색이다** (orgs 주석 참조). 안 고르면
                우리 것이다 — 대부분의 장비가 그렇다. */}
            <div className="flex flex-col gap-1">
              {draft.organization_id === '' ? (
                <>
                  <Field
                    label={t('desk:asset.organization')}
                    hint={t('desk:asset.organizationHint')}
                    value={orgTerm}
                    onChange={(event) => { setOrgTerm(event.target.value) }}
                  />
                  {orgs.isError ? <Alert>{describeError(orgs.error)}</Alert> : null}
                  {orgs.isSuccess && orgs.data.items.length === 0 ? (
                    <p className="text-xs text-muted">{t('desk:asset.organizationEmpty')}</p>
                  ) : null}
                  {/* 잘렸으면 **잘렸다고 말한다** — 조용히 자르면 사람은
                      "이게 전부" 라고 읽는다 (ux-principles). */}
                  {orgs.data?.has_more ? (
                    <p className="text-xs text-muted">{t('desk:asset.organizationMore')}</p>
                  ) : null}
                  <div className="flex flex-wrap gap-1.5">
                    {(orgs.data?.items ?? []).map((row) => (
                      <Button
                        key={row.id}
                        variant="secondary"
                        className="text-xs"
                        onClick={() => {
                          setDraft((current) => ({
                            ...current,
                            organization_id: row.id,
                            organization_name: row.name,
                          }))
                          setOrgTerm('')
                        }}
                      >
                        {row.name}
                      </Button>
                    ))}
                  </div>
                </>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-muted">
                    {t('desk:asset.organization')}
                  </span>
                  <span className="text-sm">{draft.organization_name}</span>
                  <Button
                    variant="ghost"
                    className="text-xs"
                    onClick={() => {
                      setDraft((current) => ({
                        ...current,
                        organization_id: '',
                        organization_name: '',
                      }))
                    }}
                  >
                    {t('desk:asset.noOrganization')}
                  </Button>
                </div>
              )}
            </div>
            <Field
              label={t('desk:asset.note')}
              value={draft.note}
              onChange={(event) => { set('note', event.target.value) }}
            />
            <Button
              type="submit"
              className="self-start"
              loading={create.isPending}
              disabled={draft.type_id === '' || draft.name.trim() === ''}
            >
              {t('common:action.create')}
            </Button>
          </form>
        </Card>
      ) : null}

      {assets.isError ? <Alert>{describeError(assets.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
      {setStatus.isError ? <Alert>{describeError(setStatus.error)}</Alert> : null}
      {assets.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:asset.empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-2" data-testid="assets">
        {rows.map((row) => (
          <li key={row.id}>
            <Card className="flex flex-col gap-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs text-muted">{row.type_name}</span>
                <span className="text-sm font-medium">{row.name}</span>
                {row.tag ? <code className="font-mono text-xs">{row.tag}</code> : null}
                <Badge tone={TONE[row.status]}>{t(`desk:asset.status.${row.status}`)}</Badge>
                {row.location ? (
                  <span className="text-xs text-muted">{row.location}</span>
                ) : null}
                {row.organization_name ? (
                  <span className="text-xs text-muted">{row.organization_name}</span>
                ) : null}
                <span className="ml-auto text-xs text-muted">
                  {t('desk:asset.since', { when: formatDate(row.created_at) })}
                </span>
              </div>
              {row.note ? <p className="text-xs text-muted">{row.note}</p> : null}
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1 text-xs text-muted">
                  {t('desk:asset.status')}
                  <select
                    className="rounded border border-border bg-surface px-1 py-0.5 text-xs"
                    value={row.status}
                    onChange={(event) => {
                      setStatus.mutate({
                        id: row.id,
                        status: event.target.value as AssetStatus,
                      })
                    }}
                  >
                    {STATUSES.map((value) => (
                      <option key={value} value={value}>
                        {t(`desk:asset.status.${value}`)}
                      </option>
                    ))}
                  </select>
                </label>
                {/* **티켓 수가 이 화면의 값이다.** 자꾸 고장나는 장비를 찾는
                    사람이 보는 숫자다. */}
                <Button
                  variant="ghost"
                  className="text-xs"
                  onClick={() => { setOpenAsset(openAsset === row.id ? null : row.id) }}
                >
                  {t('desk:asset.ticketCount', { count: row.ticket_count })}
                </Button>
                <Button
                  variant="ghost"
                  className="text-xs"
                  loading={remove.isPending && remove.variables === row.id}
                  onClick={() => { remove.mutate(row.id) }}
                >
                  {t('common:action.delete')}
                </Button>
              </div>
              {openAsset === row.id ? (
                <div className="flex flex-col gap-1">
                  {tickets.isError ? <Alert>{describeError(tickets.error)}</Alert> : null}
                  {tickets.isSuccess && tickets.data.length === 0 ? (
                    <p className="text-xs text-muted">{t('desk:asset.noTickets')}</p>
                  ) : null}
                  <ul className="flex flex-col gap-0.5 text-xs">
                    {(tickets.data ?? []).map((ticket) => (
                      <li key={ticket.issue_id} className="flex flex-wrap items-baseline gap-2">
                        <span className="font-mono text-muted">{ticket.key}</span>
                        <span className="min-w-0 flex-1 truncate">{ticket.summary}</span>
                        <span className="text-muted">{ticket.state_name}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </Card>
          </li>
        ))}
      </ul>

      {/* 다음 쪽. **전체 개수를 말하지 않는다** — 커서 페이지라 세지 않았고,
          안 센 값을 만들어 보여 주면 그것을 믿게 된다. */}
      {(assets.data?.next_cursor ?? null) !== null ? (
        <Button
          variant="secondary"
          className="self-start"
          onClick={() => { setCursor(assets.data?.next_cursor ?? null) }}
        >
          {t('common:action.loadMore')}
        </Button>
      ) : null}
      {cursor !== null ? (
        <Button variant="ghost" className="self-start text-xs" onClick={() => { setCursor(null) }}>
          {t('desk:asset.backToStart')}
        </Button>
      ) : null}
    </section>
  )
}
