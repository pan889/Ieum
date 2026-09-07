/**
 * 그룹 관리.
 *
 * 역할을 그룹에 붙이고 사람은 그룹에 넣는다 — 권한을 사람 단위로 만지지
 * 않기 위한 자리다.
 *
 * **IdP 가 만든 그룹도 보여 준다.** 그게 SSO 그룹 동기화가 돌고 있다는 유일한
 * 증거다. 대신 그 그룹의 이름과 멤버는 여기서 고칠 수 없다 — 다음 로그인의
 * 동기화가 되돌린다. 되돌려질 변경을 받아 주면 화면은 성공을 보여 주고 결과는
 * 사라진다. 그래서 편집 손잡이를 아예 그리지 않고 왜 그런지 적어 둔다.
 *
 * 그룹을 지우면 그 그룹에 걸린 역할 할당도 함께 사라진다. 지우기 전에
 * 말해 준다 — 인원수만 보고 지운 사람은 권한이 함께 사라진 것을 모른다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { CurrentUser, UserGroup } from '@ieum/api-client'

import { SettingsNav } from '@/features/settings/SettingsNav'
import { groupsApi, usersApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field, Select } from '@/shared/ui/primitives'

export function GroupsScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  const groups = useQuery({ queryKey: ['groups'], queryFn: () => groupsApi.list() })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['groups'] })

  const create = useMutation({
    mutationFn: () => groupsApi.create({ name }),
    onSuccess: async () => {
      setAdding(false)
      setName('')
      await refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => groupsApi.remove(id),
    onSuccess: async () => {
      setOpen(null)
      await refresh()
    },
  })

  const rows = groups.data ?? []

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('admin:groups.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('admin:groups.description')}</p>
        </div>
        <Button
          className="ml-auto shrink-0 text-xs"
          variant={adding ? 'ghost' : 'primary'}
          onClick={() => { setAdding(!adding) }}
        >
          {adding ? t('common:action.cancel') : t('admin:groups.add')}
        </Button>
      </header>

      {groups.isError ? <Alert>{describeError(groups.error)}</Alert> : null}
      {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {adding ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              create.mutate()
            }}
          >
            <Field
              label={t('admin:groups.name')}
              required
              value={name}
              onChange={(event) => { setName(event.target.value) }}
            />
            <Button type="submit" className="self-start" loading={create.isPending}>
              {t('admin:groups.save')}
            </Button>
          </form>
        </Card>
      ) : null}

      <ul className="flex flex-col gap-2" aria-label={t('admin:groups.title')}>
        {rows.map((group) => (
          <li key={group.id}>
            <GroupCard
              group={group}
              expanded={open === group.id}
              busy={remove.isPending}
              onToggle={() => { setOpen(open === group.id ? null : group.id) }}
              onRemove={() => { remove.mutate(group.id) }}
            />
          </li>
        ))}
      </ul>

      {!groups.isPending && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('admin:groups.empty')}</p>
      ) : null}
    </section>
  )
}

function GroupCard({
  group,
  expanded,
  busy,
  onToggle,
  onRemove,
}: {
  group: UserGroup
  expanded: boolean
  busy: boolean
  onToggle: () => void
  onRemove: () => void
}) {
  const { t } = useTranslation(['admin', 'common'])
  const managed = group.source === 'idp'
  const [confirming, setConfirming] = useState(false)

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="truncate text-sm font-medium">
            {group.name}
            {managed ? (
              <span className="ml-2 align-middle">
                <Badge tone="info">{t('admin:groups.managed')}</Badge>
              </span>
            ) : null}
          </p>
          <p className="text-xs text-muted">
            {t('admin:groups.memberCount', { count: group.member_count })}
          </p>
        </div>

        <div className="ml-auto flex shrink-0 gap-1">
          <Button
            variant="ghost"
            className="text-xs"
            aria-label={t('admin:groups.membersLabel', { name: group.name })}
            aria-expanded={expanded}
            onClick={onToggle}
          >
            {t('admin:groups.members')}
          </Button>
          {confirming ? (
            <>
              <Button
                variant="secondary"
                className="text-xs"
                aria-label={t('admin:groups.confirmDeleteLabel', { name: group.name })}
                loading={busy}
                onClick={onRemove}
              >
                {t('common:action.confirm')}
              </Button>
              <Button
                variant="ghost"
                className="text-xs"
                onClick={() => { setConfirming(false) }}
              >
                {t('common:action.cancel')}
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              className="text-xs"
              aria-label={t('admin:groups.deleteLabel', { name: group.name })}
              onClick={() => { setConfirming(true) }}
            >
              {t('common:action.delete')}
            </Button>
          )}
        </div>
      </div>

      {/* 지우면 역할 할당까지 사라진다. 인원수만 보고 지운 사람은 권한이
          함께 사라진 것을 모른다 — 누르기 전에 말해 준다. */}
      {confirming ? <p className="text-sm text-muted">{t('admin:groups.deleteWarning')}</p> : null}

      {expanded ? <Members group={group} managed={managed} /> : null}
    </Card>
  )
}

function Members({ group, managed }: { group: UserGroup; managed: boolean }) {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [picked, setPicked] = useState('')

  const members = useQuery({
    queryKey: ['groups', group.id, 'members'],
    queryFn: () => groupsApi.members(group.id),
  })

  // 넣을 사람을 고른다. 목록이 길어지면 검색으로 바꿔야 하지만, 그때까지는
  // 고르는 것이 타이핑보다 빠르고 오타가 없다.
  const candidates = useQuery({
    queryKey: ['admin', 'users', ''],
    queryFn: () => usersApi.list({ limit: 100 }),
    enabled: !managed,
  })

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['groups'] })
  }

  const add = useMutation({
    mutationFn: (userId: string) => groupsApi.addMember(group.id, userId),
    onSuccess: async () => {
      setPicked('')
      await invalidate()
    },
  })

  const drop = useMutation({
    mutationFn: (userId: string) => groupsApi.removeMember(group.id, userId),
    onSuccess: invalidate,
  })

  const rows = members.data ?? []
  const already = new Set(rows.map((row) => row.id))
  const pool = (candidates.data?.items ?? []).filter((user) => !already.has(user.id))

  return (
    <div className="flex flex-col gap-3 border-t border-border pt-3">
      {members.isError ? <Alert>{describeError(members.error)}</Alert> : null}
      {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
      {drop.isError ? <Alert>{describeError(drop.error)}</Alert> : null}

      {managed ? (
        // 손으로 고쳐도 다음 로그인이 되돌린다. 손잡이를 그리지 않는 이유를
        // 적어 둔다 — 없는 것과 못 하는 것은 다르다.
        <p className="text-sm text-muted">{t('admin:groups.managedHint')}</p>
      ) : (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (picked) add.mutate(picked)
          }}
        >
          <Select
            label={t('admin:groups.addMember')}
            className="min-w-56"
            value={picked}
            onChange={(event) => { setPicked(event.target.value) }}
          >
            <option value="">{t('admin:groups.pickPerson')}</option>
            {pool.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name} ({user.email})
              </option>
            ))}
          </Select>
          <Button type="submit" disabled={!picked} loading={add.isPending} className="text-xs">
            {t('common:action.add')}
          </Button>
        </form>
      )}

      <ul
        className="flex flex-col gap-1"
        aria-label={t('admin:groups.membersLabel', { name: group.name })}
      >
        {rows.map((member: CurrentUser) => (
          <li key={member.id} className="flex items-center gap-2 text-sm">
            <span className="truncate">
              {member.display_name} <span className="text-muted">{member.email}</span>
            </span>
            {managed ? null : (
              <Button
                variant="ghost"
                className="ml-auto shrink-0 text-xs"
                aria-label={t('admin:groups.removeMemberLabel', { name: member.display_name })}
                loading={drop.isPending}
                onClick={() => { drop.mutate(member.id) }}
              >
                {t('common:action.delete')}
              </Button>
            )}
          </li>
        ))}
      </ul>

      {!members.isPending && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('admin:groups.noMembers')}</p>
      ) : null}
    </div>
  )
}
