/**
 * 사용자 관리.
 *
 * 초대하고, 잠그고, 되살리고, 한 사람에게만 2FA 를 강제한다.
 *
 * 잠그는 일과 되살리는 일은 **한 쌍으로** 둔다. 잠그기만 있으면 실수 한 번이
 * 되돌릴 수 없는 상태가 되고, 그 계정으로만 할 수 있는 일이 있었다면 조직이
 * 그것을 영구히 잃는다.
 *
 * 정지·2FA 강제는 서버가 step-up 을 요구한다(계정 탈취 경로다). 2FA 를
 * 등록하지 않은 관리자는 여기서 막히고, 화면은 그 거절을 **그대로 보여
 * 준다** — 버튼을 숨기면 왜 안 되는지 알 방법이 없다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { CurrentUser } from '@ieum/api-client'

import { formatRelative } from '@/features/issues/format'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { usersApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Field } from '@/shared/ui/primitives'

const EMPTY_INVITE = { email: '', display_name: '' }

export function UsersScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [inviting, setInviting] = useState(false)
  const [invite, setInvite] = useState(EMPTY_INVITE)

  const users = useQuery({
    queryKey: ['admin', 'users', query],
    queryFn: () => usersApi.list(query ? { q: query, limit: 100 } : { limit: 100 }),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })

  const send = useMutation({
    mutationFn: () => usersApi.invite(invite),
    onSuccess: async () => {
      setInviting(false)
      setInvite(EMPTY_INVITE)
      await refresh()
    },
  })

  const status = useMutation({
    mutationFn: ({ id, suspend }: { id: string; suspend: boolean }) =>
      suspend ? usersApi.suspend(id) : usersApi.reactivate(id),
    onSuccess: () => { void refresh() },
  })

  const requireMfa = useMutation({
    mutationFn: ({ id, required }: { id: string; required: boolean }) =>
      usersApi.setRequireMfa(id, required),
    onSuccess: () => { void refresh() },
  })

  const revoke = useMutation({
    mutationFn: (id: string) => usersApi.revokeSessions(id),
    onSuccess: () => { void refresh() },
  })

  const rows = users.data?.items ?? []

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('admin:users.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('admin:users.description')}</p>
        </div>
        <Button
          className="ml-auto shrink-0 text-xs"
          variant={inviting ? 'ghost' : 'primary'}
          onClick={() => { setInviting(!inviting) }}
        >
          {inviting ? t('common:action.cancel') : t('admin:users.invite')}
        </Button>
      </header>

      {users.isError ? <Alert>{describeError(users.error)}</Alert> : null}
      {send.isError ? <Alert>{describeError(send.error)}</Alert> : null}
      {status.isError ? <Alert>{describeError(status.error)}</Alert> : null}
      {requireMfa.isError ? <Alert>{describeError(requireMfa.error)}</Alert> : null}
      {revoke.isError ? <Alert>{describeError(revoke.error)}</Alert> : null}

      {inviting ? (
        <Card>
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              send.mutate()
            }}
          >
            <Field
              label={t('admin:users.email')}
              type="email"
              required
              value={invite.email}
              onChange={(event) => { setInvite({ ...invite, email: event.target.value }) }}
            />
            <Field
              label={t('admin:users.displayName')}
              required
              value={invite.display_name}
              onChange={(event) => {
                setInvite({ ...invite, display_name: event.target.value })
              }}
            />
            <Button type="submit" className="self-start" loading={send.isPending}>
              {t('admin:users.sendInvite')}
            </Button>
          </form>
        </Card>
      ) : null}

      <Field
        label={t('admin:users.search')}
        value={query}
        onChange={(event) => { setQuery(event.target.value) }}
      />

      {/* 이름을 붙인다. 화면에 목록이 여럿이면 "목록" 만으로는 어느 쪽인지
          알 수 없다 — 보조 기술에도, 테스트에도 똑같이 그렇다. */}
      <ul className="flex flex-col gap-2" aria-label={t('admin:users.title')}>
        {rows.map((user) => (
          <li key={user.id}>
            <UserCard
              user={user}
              busy={status.isPending || requireMfa.isPending || revoke.isPending}
              onSuspend={() => {
                status.mutate({ id: user.id, suspend: user.status !== 'suspended' })
              }}
              onRequireMfa={() => {
                requireMfa.mutate({ id: user.id, required: !user.require_mfa })
              }}
              onRevoke={() => { revoke.mutate(user.id) }}
            />
          </li>
        ))}
      </ul>

      {!users.isPending && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('admin:users.empty')}</p>
      ) : null}
    </section>
  )
}

function UserCard({
  user,
  busy,
  onSuspend,
  onRequireMfa,
  onRevoke,
}: {
  user: CurrentUser
  busy: boolean
  onSuspend: () => void
  onRequireMfa: () => void
  onRevoke: () => void
}) {
  const { t } = useTranslation('admin')
  const suspended = user.status === 'suspended'

  return (
    <Card className="flex flex-wrap items-center gap-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="truncate text-sm font-medium">
          {user.display_name}
          {/* 상태는 이름 옆에 둔다. 목록을 훑는 사람이 찾는 것은 "누가 잠겨
              있나" 이고, 그건 행 끝의 버튼 모양으로 읽으면 늦다. */}
          <StatusBadge status={user.status} />
          {user.require_mfa ? (
            <span className="ml-1 align-middle">
              <Badge tone="info">{t('users.mfaRequired')}</Badge>
            </span>
          ) : null}
        </p>
        <p className="truncate text-xs text-muted">
          {user.email}
          {user.last_login_at
            ? ` · ${t('users.lastSeen', { when: formatRelative(user.last_login_at) })}`
            : ` · ${t('users.neverSignedIn')}`}
        </p>
      </div>

      <div className="ml-auto flex shrink-0 flex-wrap gap-1">
        <Button
          variant="ghost"
          className="text-xs"
          aria-label={t('users.mfaToggleLabel', { name: user.display_name })}
          loading={busy}
          onClick={onRequireMfa}
        >
          {user.require_mfa ? t('users.dropMfa') : t('users.forceMfa')}
        </Button>
        {/* 세션만 끊는 길을 따로 둔다. 기기를 잃은 사람의 계정을 잠그면
            본인도 못 들어온다 — 필요한 것은 그 기기를 끊는 것뿐이다. */}
        <Button
          variant="ghost"
          className="text-xs"
          aria-label={t('users.revokeLabel', { name: user.display_name })}
          loading={busy}
          onClick={onRevoke}
        >
          {t('users.revokeSessions')}
        </Button>
        <Button
          variant={suspended ? 'primary' : 'secondary'}
          className="text-xs"
          aria-label={
            suspended
              ? t('users.reactivateLabel', { name: user.display_name })
              : t('users.suspendLabel', { name: user.display_name })
          }
          loading={busy}
          onClick={onSuspend}
        >
          {suspended ? t('users.reactivate') : t('users.suspend')}
        </Button>
      </div>
    </Card>
  )
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('admin')
  if (status === 'active') return null
  return (
    <span className="ml-2 align-middle">
      <Badge tone={status === 'suspended' ? 'danger' : 'neutral'}>
        {t(`users.status.${status}`, { defaultValue: status })}
      </Badge>
    </span>
  )
}
