/**
 * 앱 등록과 자리 지정 (M6 "플러그인 훅").
 *
 * **이 화면이 나누는 두 권한이 이 기능의 전부다.**
 *
 * - **자리는 여기서 정한다.** 어느 자리에 무엇을 놓을지는 관리자가 고른다.
 *   앱이 스스로 자리를 넓히거나 옮길 수 없다 — 옮길 수 있으면 앱 하나가
 *   전이 버튼 옆으로 옮겨 앉아 우리가 쓴 글처럼 보인다.
 * - **안은 앱이 채운다.** 패널 본문은 앱이 자기 토큰으로 쓴다. 이 화면에서
 *   대신 쓰지 않는다 — 그러면 앱이 아니라 그냥 메모다.
 *
 * **토큰은 등록 응답에만 있다.** 이 카드를 닫으면 다시 못 읽는다고 적어
 * 둔다(저장소 연동과 같은 규칙). 잃었으면 새로 내면 되고, 그때 옛것은 죽는다.
 *
 * **자리 어휘를 화면이 들고 있지 않는다.** `/apps/slots` 에서 받아 그린다 —
 * 서버가 자리를 늘렸을 때 화면이 조용히 어긋나지 않게 한다.
 *
 * 목록은 **꺼진 앱도 보여 준다.** 숨기면 사람은 지워진 줄 알고 다시 등록한다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { App, SlotCatalogEntry, SlotKind } from '@ieum/api-client'

import { formatDateTime } from '@/features/issues/format'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { pluginsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field, Select } from '@/shared/ui/primitives'

export function AppsScreen() {
  const { t } = useTranslation(['plugins', 'common'])
  const queryClient = useQueryClient()

  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
  const [eventUrl, setEventUrl] = useState('')
  const [events, setEvents] = useState('')
  const [issued, setIssued] = useState<{ app: string; token: string } | null>(null)
  const [copied, setCopied] = useState(false)

  const apps = useQuery({ queryKey: ['plugins', 'apps'], queryFn: () => pluginsApi.list() })
  const catalog = useQuery({
    queryKey: ['plugins', 'slots'],
    queryFn: () => pluginsApi.slotCatalog(),
    staleTime: 5 * 60_000,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['plugins'] })

  const register = useMutation({
    mutationFn: () =>
      pluginsApi.register({
        name: name.trim(),
        slug: slug.trim(),
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(eventUrl.trim() ? { event_url: eventUrl.trim() } : {}),
        ...(events.trim()
          ? { events: events.split(',').map((row) => row.trim()).filter(Boolean) }
          : {}),
      }),
    onSuccess: async (made) => {
      setIssued({ app: made.app.name, token: made.token })
      setName('')
      setSlug('')
      setDescription('')
      setEventUrl('')
      setEvents('')
      setAdding(false)
      await refresh()
    },
  })

  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      pluginsApi.setEnabled(id, enabled),
    onSuccess: refresh,
  })

  const rotate = useMutation({
    mutationFn: (row: App) =>
      pluginsApi.rotateToken(row.id).then((made) => ({ app: row.name, token: made.token })),
    onSuccess: async (made) => {
      setIssued(made)
      await refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => pluginsApi.remove(id),
    onSuccess: refresh,
  })

  const rows = apps.data ?? []

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">{t('plugins:title')}</h1>
        <p className="text-sm text-muted">{t('plugins:description')}</p>
      </header>

      {/* **토큰은 여기서 한 번만 보인다.** */}
      {issued ? (
        <Card className="flex flex-col gap-3 border-accent" data-testid="app-issued">
          <p className="text-sm font-medium">{t('plugins:issued.title', { app: issued.app })}</p>
          <p className="text-xs text-muted">{t('plugins:issued.hint')}</p>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">{t('plugins:issued.token')}</span>
            <div className="flex items-center gap-2">
              <code
                data-testid="app-token"
                className="flex-1 overflow-x-auto rounded bg-surface-raised px-2 py-1.5 font-mono text-xs"
              >
                {issued.token}
              </code>
              <Button
                variant="secondary"
                aria-label={t('plugins:issued.copyToken')}
                onClick={() => {
                  void navigator.clipboard.writeText(issued.token).then(() => { setCopied(true) })
                }}
              >
                {copied ? t('common:action.copied') : t('common:action.copy')}
              </Button>
            </div>
          </div>
          <Button
            variant="ghost"
            className="self-start text-xs"
            onClick={() => { setIssued(null); setCopied(false) }}
          >
            {t('common:action.close')}
          </Button>
        </Card>
      ) : null}

      <div className="flex flex-wrap items-end gap-3">
        <Button variant={adding ? 'ghost' : 'primary'} onClick={() => { setAdding((v) => !v) }}>
          {adding ? t('common:action.cancel') : t('plugins:add')}
        </Button>
      </div>

      {adding ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            data-testid="app-form"
            onSubmit={(event) => { event.preventDefault(); register.mutate() }}
          >
            {register.isError ? <Alert>{describeError(register.error)}</Alert> : null}
            <div className="flex flex-wrap items-end gap-3">
              <Field
                label={t('plugins:field.name')}
                required
                value={name}
                onChange={(event) => { setName(event.target.value) }}
              />
              <Field
                label={t('plugins:field.slug')}
                hint={t('plugins:field.slugHint')}
                required
                value={slug}
                onChange={(event) => { setSlug(event.target.value) }}
              />
            </div>
            <Field
              label={t('plugins:field.description')}
              value={description}
              onChange={(event) => { setDescription(event.target.value) }}
            />
            {/* 서버 이벤트. 비우면 이벤트를 안 받는 앱이다 — 패널만 쓰는
                앱이 정당한 형태다. */}
            <div className="flex flex-wrap items-end gap-3">
              <Field
                label={t('plugins:field.eventUrl')}
                hint={t('plugins:field.eventUrlHint')}
                value={eventUrl}
                onChange={(event) => { setEventUrl(event.target.value) }}
              />
              <Field
                label={t('plugins:field.events')}
                hint={t('plugins:field.eventsHint')}
                value={events}
                onChange={(event) => { setEvents(event.target.value) }}
              />
            </div>
            <Button
              type="submit"
              className="self-start"
              loading={register.isPending}
              disabled={name.trim() === '' || slug.trim() === ''}
            >
              {t('common:action.create')}
            </Button>
          </form>
        </Card>
      ) : null}

      {apps.isError ? <Alert>{describeError(apps.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
      {setEnabled.isError ? <Alert>{describeError(setEnabled.error)}</Alert> : null}
      {apps.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('plugins:empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-3" data-testid="apps">
        {rows.map((row) => (
          <li key={row.id}>
            <Card className="flex flex-col gap-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-sm font-medium">{row.name}</span>
                <code className="font-mono text-xs text-muted">{row.slug}</code>
                {row.enabled ? null : <Badge tone="neutral">{t('plugins:off')}</Badge>}
                <code className="font-mono text-xs text-muted">{row.token_prefix}…</code>
                <span className="ml-auto text-xs text-muted">
                  {/* **"켰는데 안 오는 것" 을 보이게 한다.** 한 번도 안 들어온
                      앱은 그렇게 말한다 — 조용한 고장이 제일 오래 산다. */}
                  {row.last_seen_at
                    ? t('plugins:lastSeen', { when: formatDateTime(row.last_seen_at) })
                    : t('plugins:neverSeen')}
                </span>
              </div>
              {row.description ? (
                <p className="text-xs text-muted">{row.description}</p>
              ) : null}

              {row.stream ? (
                <p className="text-xs text-muted">
                  {t('plugins:stream', {
                    url: row.stream.url,
                    events: row.stream.events.join(', '),
                  })}
                  {row.stream.enabled ? null : ` — ${t('plugins:streamOff')}`}
                </p>
              ) : (
                <p className="text-xs text-muted">{t('plugins:noStream')}</p>
              )}

              <Placements app={row} catalog={catalog.data ?? []} onChanged={refresh} />

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="ghost"
                  className="text-xs"
                  loading={setEnabled.isPending && setEnabled.variables.id === row.id}
                  onClick={() => { setEnabled.mutate({ id: row.id, enabled: !row.enabled }) }}
                >
                  {row.enabled ? t('plugins:turnOff') : t('plugins:turnOn')}
                </Button>
                <Button
                  variant="ghost"
                  className="text-xs"
                  loading={rotate.isPending && rotate.variables.id === row.id}
                  onClick={() => { rotate.mutate(row) }}
                >
                  {t('plugins:rotate')}
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
            </Card>
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * 이 앱이 차지한 자리와, 자리를 하나 더 주는 폼.
 *
 * 자리 어휘는 **서버에서 받은 것만** 고를 수 있다. 고른 자리의 종류가
 * 링크면 주소 칸이 나오고, 패널이면 안 나온다 — 자리가 종류를 정하고, 종류가
 * 무엇을 물을지 정한다.
 */
function Placements({
  app,
  catalog,
  onChanged,
}: {
  app: App
  catalog: SlotCatalogEntry[]
  onChanged: () => Promise<unknown>
}) {
  const { t } = useTranslation(['plugins', 'common'])
  const [slot, setSlot] = useState('')
  const [label, setLabel] = useState('')
  const [url, setUrl] = useState('')

  const chosen = catalog.find((row) => row.name === slot) ?? null
  const kind: SlotKind | null = chosen?.kind ?? null

  const place = useMutation({
    mutationFn: () =>
      pluginsApi.place(app.id, {
        slot,
        kind: kind as SlotKind,
        label: label.trim(),
        ...(kind === 'link' ? { url_template: url.trim() } : {}),
      }),
    onSuccess: async () => {
      setLabel('')
      setUrl('')
      await onChanged()
    },
  })

  const unplace = useMutation({
    mutationFn: (slotId: string) => pluginsApi.unplace(app.id, slotId),
    onSuccess: () => onChanged(),
  })

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <span className="text-xs font-medium text-muted">{t('plugins:slots.title')}</span>
      {app.slots.length === 0 ? (
        <p className="text-xs text-muted">{t('plugins:slots.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {app.slots.map((row) => (
            <li key={row.id} className="flex flex-wrap items-baseline gap-2 text-xs">
              <code className="font-mono text-muted">{row.slot}</code>
              <span>{row.label}</span>
              {row.url_template ? (
                <code className="font-mono text-muted">{row.url_template}</code>
              ) : null}
              <Button
                variant="ghost"
                className="text-xs"
                loading={unplace.isPending && unplace.variables === row.id}
                onClick={() => { unplace.mutate(row.id) }}
              >
                {t('plugins:slots.remove')}
              </Button>
            </li>
          ))}
        </ul>
      )}

      {place.isError ? <Alert>{describeError(place.error)}</Alert> : null}
      {unplace.isError ? <Alert>{describeError(unplace.error)}</Alert> : null}

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => { event.preventDefault(); place.mutate() }}
      >
        <Select
          label={t('plugins:slots.slot')}
          value={slot}
          onChange={(event) => { setSlot(event.target.value) }}
        >
          <option value="">{t('common:action.choose')}</option>
          {catalog.map((row) => (
            <option key={row.name} value={row.name}>
              {row.name} — {row.where}
            </option>
          ))}
        </Select>
        <Field
          label={t('plugins:slots.label')}
          value={label}
          onChange={(event) => { setLabel(event.target.value) }}
        />
        {/* 링크 자리만 주소를 묻는다. 패널에 주소를 두면 서버가 거절한다. */}
        {kind === 'link' ? (
          <Field
            label={t('plugins:slots.url')}
            hint={t('plugins:slots.urlHint', {
              placeholders: (chosen?.placeholders ?? []).map((p) => `{${p}}`).join(' '),
            })}
            value={url}
            onChange={(event) => { setUrl(event.target.value) }}
          />
        ) : null}
        <Button
          type="submit"
          variant="secondary"
          loading={place.isPending}
          disabled={slot === '' || label.trim() === '' || (kind === 'link' && url.trim() === '')}
        >
          {t('plugins:slots.add')}
        </Button>
      </form>
    </div>
  )
}
