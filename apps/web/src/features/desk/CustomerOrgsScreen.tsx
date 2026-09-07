/**
 * 고객 조직과 소속.
 *
 * **소속이 가시성을 정한다.** 같은 조직의 사람은 조직의 요청을 함께 본다.
 * 그래서 `desk.customer.manage` 는 step-up 대상이다 — 2FA 없는 관리자는
 * 거절을 본다.
 *
 * 도메인은 **가입 시 기본 조직을 고르는 힌트**다. 가시성의 근거가 아니다.
 * 그 구분을 화면에 적어 둔다(`customers.domainsHint`): 도메인이 근거라고
 * 오해하면, 도메인을 고쳐서 남의 티켓을 볼 수 있다고 생각하게 된다.
 *
 * 사람을 고르는 자리는 **검색**이다. 목록을 통째로 받아 드롭다운에 넣으면
 * 사람이 백 명을 넘는 조직에서 마지막 사람을 넣을 수 없다 — 앞서 그룹·역할
 * 화면에서 겪은 그대로다(ux-principles.md 4절).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { CustomerOrg } from '@ieum/api-client'

import { PersonPicker } from '@/features/settings/PersonPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field } from '@/shared/ui/primitives'

const EMPTY = { name: '', domains: '', note: '' }

/** 쉼표로 적은 도메인을 목록으로. 서버가 다시 정규화하지만 빈 항목은 여기서 버린다. */
function domainList(value: string): string[] {
  return value
    .split(',')
    .map((domain) => domain.trim())
    .filter(Boolean)
}

export function CustomerOrgsScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [open, setOpen] = useState<string | null>(null)

  const orgs = useQuery({
    queryKey: ['customer-orgs', query],
    queryFn: () => deskApi.listOrgs({ limit: 50, ...(query ? { q: query } : {}) }),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['customer-orgs'] })

  const create = useMutation({
    mutationFn: () =>
      deskApi.createOrg({
        name: form.name,
        domains: domainList(form.domains),
        ...(form.note ? { note: form.note } : {}),
      }),
    onSuccess: async () => {
      setAdding(false)
      setForm(EMPTY)
      await refresh()
    },
  })

  const archive = useMutation({
    mutationFn: (org: CustomerOrg) =>
      org.is_archived ? deskApi.restoreOrg(org.id) : deskApi.archiveOrg(org.id),
    onSuccess: refresh,
  })

  const rows = orgs.data?.items ?? []

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('desk:customers.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('desk:customers.description')}</p>
        </div>
        <Button
          className="ml-auto shrink-0 text-xs"
          variant={adding ? 'ghost' : 'primary'}
          onClick={() => { setAdding(!adding) }}
        >
          {adding ? t('common:action.cancel') : t('desk:customers.add')}
        </Button>
      </header>

      <Field
        label={t('desk:customers.search')}
        className="max-w-xs"
        value={query}
        onChange={(event) => { setQuery(event.target.value) }}
      />

      {orgs.isError ? <Alert>{describeError(orgs.error)}</Alert> : null}
      {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
      {archive.isError ? <Alert>{describeError(archive.error)}</Alert> : null}

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
                label={t('desk:customers.name')}
                required
                value={form.name}
                onChange={(event) => { setForm({ ...form, name: event.target.value }) }}
              />
              <Field
                label={t('desk:customers.domains')}
                hint={t('desk:customers.domainsHint')}
                value={form.domains}
                onChange={(event) => { setForm({ ...form, domains: event.target.value }) }}
              />
            </div>
            <Field
              label={t('desk:customers.note')}
              value={form.note}
              onChange={(event) => { setForm({ ...form, note: event.target.value }) }}
            />
            <Button type="submit" className="self-start" disabled={create.isPending}>
              {t('common:action.create')}
            </Button>
          </form>
        </Card>
      ) : null}

      {/* **실패했을 때 "없다" 고 말하지 않는다.** 조회가 403 이면 목록은
          비어 있지만 없는 것이 아니다 — 앞서 이 화면이 오류와 "아직 없습니다"
          를 나란히 그렸고, 그건 상태에 대한 거짓말이다. `isSuccess` 여야
          "없다" 를 말할 자격이 생긴다. */}
      {orgs.isSuccess && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('desk:customers.empty')}</p>
      ) : null}

      <ul className="flex flex-col gap-2">
        {rows.map((org) => (
          <li key={org.id} className="rounded-md border border-border bg-surface">
            <div className="flex flex-wrap items-baseline gap-2 px-4 py-3">
              <span className="text-sm font-medium">{org.name}</span>
              {org.is_archived ? (
                <Badge tone="danger">{t('desk:portals.archived')}</Badge>
              ) : null}
              <span className="text-xs text-muted">
                {t('desk:customers.membersCount', { count: org.member_count })}
              </span>
              {org.domains.length > 0 ? (
                <span className="text-xs text-muted">{org.domains.join(', ')}</span>
              ) : null}
              <Button
                className="ml-auto text-xs"
                variant="ghost"
                onClick={() => { setOpen(open === org.id ? null : org.id) }}
              >
                {t('desk:customers.members')}
              </Button>
              <Button
                className="text-xs"
                variant="ghost"
                onClick={() => { archive.mutate(org) }}
              >
                {org.is_archived ? t('desk:portals.restore') : t('desk:portals.archive')}
              </Button>
            </div>
            {open === org.id ? (
              <div className="border-t border-border px-4 py-3">
                <Members organizationId={org.id} onChanged={() => {
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

function Members({
  organizationId,
  onChanged,
}: {
  organizationId: string
  onChanged: () => void
}) {
  const { t } = useTranslation(['desk', 'common'])
  const [invited, setInvited] = useState({ email: '', display_name: '' })
  const members = useQuery({
    queryKey: ['customer-orgs', organizationId, 'members'],
    queryFn: () => deskApi.listMembers(organizationId),
  })

  const invite = useMutation({
    mutationFn: () => deskApi.inviteCustomer(organizationId, invited),
    onSuccess: async () => {
      setInvited({ email: '', display_name: '' })
      await members.refetch()
      onChanged()
    },
  })

  const add = useMutation({
    mutationFn: (userId: string) => deskApi.addMember(organizationId, userId),
    onSuccess: async () => {
      await members.refetch()
      onChanged()
    },
  })
  const remove = useMutation({
    mutationFn: (userId: string) => deskApi.removeMember(organizationId, userId),
    onSuccess: async () => {
      await members.refetch()
      onChanged()
    },
  })

  const rows = members.data ?? []

  return (
    <div className="flex flex-col gap-2">
      {members.isError ? <Alert>{describeError(members.error)}</Alert> : null}
      {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {rows.length === 0 ? (
        <p className="text-xs text-muted">{t('desk:customers.membersEmpty')}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((member) => (
            <li key={member.user_id} className="flex items-baseline gap-2 text-sm">
              <span>{member.display_name}</span>
              <span className="text-xs text-muted">{member.email}</span>
              <Button
                className="ml-auto text-xs"
                variant="ghost"
                onClick={() => { remove.mutate(member.user_id) }}
              >
                {t('desk:customers.remove')}
              </Button>
            </li>
          ))}
        </ul>
      )}

      {/* **초대**와 **기존 고객 넣기**를 둘 다 둔다. 초대만 있으면 이미
          계정이 있는 고객을 조직에 넣을 수 없고, 넣기만 있으면 고객 계정을
          만들 길이 없다 — 한동안 실제로 그랬다: `is_customer` 를 읽는 자리만
          있고 켜는 자리가 없어서 고객 계정을 아무도 만들 수 없었다. */}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          invite.mutate()
        }}
      >
        <Field
          label={t('desk:customers.inviteName')}
          required
          value={invited.display_name}
          onChange={(event) => { setInvited({ ...invited, display_name: event.target.value }) }}
        />
        <Field
          label={t('desk:customers.inviteEmail')}
          type="email"
          required
          value={invited.email}
          onChange={(event) => { setInvited({ ...invited, email: event.target.value }) }}
        />
        <Button type="submit" className="text-xs" disabled={invite.isPending}>
          {t('desk:customers.invite')}
        </Button>
      </form>
      {invite.isError ? <Alert>{describeError(invite.error)}</Alert> : null}

      <p className="text-xs text-muted">{t('desk:customers.oneOrgOnly')}</p>
      <PersonPicker
        label={t('desk:customers.addMember')}
        exclude={new Set(rows.map((member) => member.user_id))}
        busy={add.isPending}
        onPick={(userId) => { add.mutate(userId) }}
      />
    </div>
  )
}
