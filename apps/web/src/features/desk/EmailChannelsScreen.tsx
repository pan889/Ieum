/**
 * 메일 채널 — 고객이 메일로 요청을 내는 창구 (feature-map C6).
 *
 * 포털과 나란한 개념이다: 프로젝트 하나에 붙고, 들어온 것을 어떤 요청 유형의
 * 티켓으로 만들지 정한다. 그래서 요청 유형은 **포털을 먼저 고르고** 그 안에서
 * 고른다 — 프로젝트의 모든 폼을 한 목록에 늘어놓으면 같은 이름의 폼이 여러
 * 포털에 있을 때 어느 것인지 알 수 없다.
 *
 * **비밀번호는 되돌려 받지 않는다.** 저장돼 있는지만 보여 주고, 칸은 비워
 * 둔다. 비워 둔 채로 저장하면 그대로 유지된다 — 이름 하나 고치려고 비밀번호를
 * 다시 적게 하지 않는다.
 *
 * 마지막 폴링의 오류를 **목록에 그린다.** 이 화면이 없으면 비밀번호가 틀렸을
 * 때 아무 메일도 안 들어오고, 관리자는 "고객이 안 보냈나" 로 읽는다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { EmailChannel, Project } from '@ieum/api-client'

import { formatDateTime } from '@/features/issues/format'
import { ProjectPicker } from '@/features/settings/ProjectPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { deskApi, projectsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Checkbox, Field, Select } from '@/shared/ui/primitives'

const EMPTY = {
  address: '',
  outboundFrom: '',
  host: '',
  user: '',
  port: '993',
  folder: 'INBOX',
  useSsl: true,
  password: '',
  portalId: '',
  requestTypeId: '',
}

type Form = typeof EMPTY

/**
 * 저장할 수 있는 폼인가.
 *
 * **서버가 거절하는 것을 여기서 먼저 막는다.** 저장을 눌러 거절당하고 나서
 * 여섯 칸 중 어디가 문제인지 찾게 두지 않는다. `editing` 이면 비밀번호는
 * 비워 둘 수 있다 — 이미 저장돼 있다.
 */
export function channelReady(form: Form, { editing }: { editing: boolean }): boolean {
  const port = Number(form.port)
  return (
    form.address.trim() !== '' &&
    form.outboundFrom.trim() !== '' &&
    form.host.trim() !== '' &&
    form.user.trim() !== '' &&
    Number.isInteger(port) &&
    port >= 1 &&
    port <= 65535 &&
    form.requestTypeId !== '' &&
    (editing || form.password !== '')
  )
}

export function EmailChannelsScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const [picked, setPicked] = useState<Project | null>(null)

  const first = useQuery({
    queryKey: ['projects', 'first'],
    queryFn: () => projectsApi.list({ limit: 1 }),
  })
  const project = picked ?? first.data?.items[0] ?? null

  return (
    <section className="flex flex-col gap-5">
      <SettingsNav />
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t('desk:email.title')}</h1>
        <p className="text-sm text-muted">{t('desk:email.description')}</p>
      </header>

      <ProjectPicker label={t('desk:email.project')} chosen={project} onPick={setPicked} />
      {project ? <Channels projectId={project.id} /> : null}
    </section>
  )
}

function Channels({ projectId }: { projectId: string }) {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<EmailChannel | null>(null)
  const [form, setForm] = useState(EMPTY)

  const channels = useQuery({
    queryKey: ['email-channels', projectId],
    queryFn: () => deskApi.listEmailChannels(projectId),
  })
  const portals = useQuery({
    queryKey: ['portals', projectId],
    queryFn: () => deskApi.listPortals(projectId),
  })
  const requestTypes = useQuery({
    queryKey: ['request-types', form.portalId],
    queryFn: () => deskApi.listRequestTypes(form.portalId),
    enabled: form.portalId !== '',
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['email-channels', projectId] })

  const inbound = {
    host: form.host.trim(),
    user: form.user.trim(),
    port: Number(form.port),
    folder: form.folder.trim() || 'INBOX',
    use_ssl: form.useSsl,
  }

  const save = useMutation({
    mutationFn: () =>
      editing
        ? deskApi.updateEmailChannel(editing.id, {
            address: form.address,
            outbound_from: form.outboundFrom,
            inbound,
            default_request_type_id: form.requestTypeId,
            // **빈 값을 보내지 않는다.** 서버는 안 보낸 것을 "그대로 둔다" 로
            // 읽는다 — 빈 문자열을 보내면 그 규칙이 무너진다.
            ...(form.password === '' ? {} : { password: form.password }),
          })
        : deskApi.createEmailChannel({
            project_id: projectId,
            address: form.address,
            outbound_from: form.outboundFrom,
            inbound,
            password: form.password,
            default_request_type_id: form.requestTypeId,
          }),
    onSuccess: async () => {
      setAdding(false)
      setEditing(null)
      setForm(EMPTY)
      await refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => deskApi.deleteEmailChannel(id),
    onSuccess: () => refresh(),
  })
  const toggle = useMutation({
    mutationFn: (row: EmailChannel) =>
      deskApi.updateEmailChannel(row.id, { is_enabled: !row.is_enabled }),
    onSuccess: () => refresh(),
  })

  const rows = channels.data ?? []
  const open = adding || editing !== null

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{t('desk:email.channels')}</h2>
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
          {t('desk:email.addChannel')}
        </Button>
      </div>

      {channels.isError ? <Alert>{describeError(channels.error)}</Alert> : null}
      {channels.isSuccess && rows.length === 0 && !open ? (
        <p className="text-sm text-muted">{t('desk:email.noChannels')}</p>
      ) : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
      {toggle.isError ? <Alert>{describeError(toggle.error)}</Alert> : null}

      <ul className="flex flex-col divide-y divide-border">
        {rows.map((row) => (
          <li key={row.id} className="flex flex-col gap-1 py-2 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-medium">{row.address}</span>
              <span className="text-xs text-muted">{row.request_type_name}</span>
              {row.is_enabled ? null : <Badge tone="neutral">{t('desk:email.paused')}</Badge>}
              {row.has_password ? null : (
                <Badge tone="danger">{t('desk:email.noPassword')}</Badge>
              )}
              <span className="min-w-0 flex-1" />
              <Button
                type="button"
                variant="ghost"
                className="text-xs"
                onClick={() => { toggle.mutate(row) }}
              >
                {row.is_enabled ? t('desk:email.pause') : t('desk:email.resume')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="text-xs"
                onClick={() => {
                  setAdding(false)
                  setEditing(row)
                  setForm({
                    address: row.address,
                    outboundFrom: row.outbound_from,
                    host: row.inbound.host,
                    user: row.inbound.user,
                    port: String(row.inbound.port),
                    folder: row.inbound.folder,
                    useSsl: row.inbound.use_ssl,
                    // **비워 둔다.** 서버가 값을 안 주고, 비워 둔 채 저장하면
                    // 그대로 유지된다.
                    password: '',
                    portalId: '',
                    requestTypeId: row.default_request_type_id,
                  })
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
            </div>
            {/* **마지막 폴링의 오류를 그린다.** 이것이 없으면 비밀번호가
                틀렸을 때 조용히 아무 메일도 안 들어오고, 관리자는 "고객이
                안 보냈나" 로 읽는다. */}
            {row.last_error ? (
              <Alert>
                {t('desk:email.pollFailed')}: {row.last_error}
              </Alert>
            ) : row.last_polled_at ? (
              <span className="text-xs text-muted">
                {t('desk:email.lastPolled')}: {formatDateTime(row.last_polled_at)}
              </span>
            ) : (
              <span className="text-xs text-muted">{t('desk:email.neverPolled')}</span>
            )}
          </li>
        ))}
      </ul>

      {open ? (
        <form
          className="flex flex-col gap-2 border-t border-border pt-3"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}

          <Field
            label={t('desk:email.address')}
            hint={t('desk:email.addressHint')}
            value={form.address}
            onChange={(event) => { setForm({ ...form, address: event.target.value }) }}
          />
          <Field
            label={t('desk:email.outboundFrom')}
            hint={t('desk:email.outboundFromHint')}
            value={form.outboundFrom}
            onChange={(event) => { setForm({ ...form, outboundFrom: event.target.value }) }}
          />

          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-medium text-fg">{t('desk:email.inbox')}</legend>
            <div className="flex flex-wrap items-end gap-2">
              <Field
                label={t('desk:email.host')}
                value={form.host}
                onChange={(event) => { setForm({ ...form, host: event.target.value }) }}
              />
              <Field
                label={t('desk:email.port')}
                type="number"
                className="w-24"
                value={form.port}
                onChange={(event) => { setForm({ ...form, port: event.target.value }) }}
              />
              <Field
                label={t('desk:email.user')}
                value={form.user}
                onChange={(event) => { setForm({ ...form, user: event.target.value }) }}
              />
              <Field
                label={t('desk:email.folder')}
                className="w-32"
                value={form.folder}
                onChange={(event) => { setForm({ ...form, folder: event.target.value }) }}
              />
            </div>
            <Field
              label={t('desk:email.password')}
              hint={
                editing
                  ? t('desk:email.passwordKeptHint')
                  : t('desk:email.passwordHint')
              }
              type="password"
              value={form.password}
              onChange={(event) => { setForm({ ...form, password: event.target.value }) }}
            />
            {/* **손으로 짜지 않는다.** 힌트를 라벨 안에 넣으면 접근성
                이름이 두 문장을 이어 붙인 것이 되고, 이름으로 찾는 모든
                것이 이 칸을 못 찾는다 — 브라우저 시험이 그렇게 붉어졌다. */}
            <Checkbox
              label={t('desk:email.useSsl')}
              hint={t('desk:email.useSslHint')}
              checked={form.useSsl}
              onChange={(event) => { setForm({ ...form, useSsl: event.target.checked }) }}
            />
          </fieldset>

          {/* 포털을 먼저 고르고 그 안에서 폼을 고른다. 같은 이름의 폼이 여러
              포털에 있을 수 있다. */}
          <Select
            label={t('desk:email.portal')}
            value={form.portalId}
            onChange={(event) => {
              setForm({ ...form, portalId: event.target.value, requestTypeId: '' })
            }}
          >
            <option value="">{t('desk:email.choosePortal')}</option>
            {(portals.data ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </Select>
          <Select
            label={t('desk:email.requestType')}
            value={form.requestTypeId}
            onChange={(event) => { setForm({ ...form, requestTypeId: event.target.value }) }}
          >
            <option value="">{t('desk:email.chooseRequestType')}</option>
            {(requestTypes.data ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </Select>

          <div className="flex items-center gap-2">
            <Button
              type="submit"
              loading={save.isPending}
              disabled={!channelReady(form, { editing: editing !== null })}
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
