/**
 * 역할과 권한 (auth.md 3절).
 *
 * 역할을 만들고, 권한을 고치고, 준 것을 **회수한다.** 주는 길만 있으면 그건
 * 권한 관리가 아니다 — 잘못 준 하나가 영구히 남는다.
 *
 * 이 화면은 `ROLE_MANAGE` 를 요구하고, 그건 step-up 대상이다. 2FA 를 등록하지
 * 않은 관리자는 목록조차 못 본다. 화면은 그 거절을 **그대로 보여 준다**(숨기면
 * 왜 안 되는지 알 수 없다 — SSO 화면과 같은 판단).
 *
 * 내장 역할의 권한은 고치지 못한다. 시드가 매 기동마다 정의로 되돌리기
 * 때문이다 — 받아 주면 화면은 성공을 보여 주고 다음 배포가 조용히 되돌린다.
 * 설명과 2FA 요구는 시드가 손대지 않으므로 고칠 수 있다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { PermissionDef, Role, RoleAssignment } from '@ieum/api-client'

import { PersonPicker } from '@/features/settings/PersonPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { groupsApi, rolesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Chip, Field, Select } from '@/shared/ui/primitives'

const SCOPES = ['global', 'project', 'space', 'queue'] as const

export function RolesScreen() {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [scope, setScope] = useState<(typeof SCOPES)[number]>('global')
  const [open, setOpen] = useState<string | null>(null)

  const roles = useQuery({ queryKey: ['roles'], queryFn: () => rolesApi.list() })
  const catalogue = useQuery({
    queryKey: ['roles', 'permissions'],
    queryFn: () => rolesApi.permissions(),
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['roles'] })

  const create = useMutation({
    mutationFn: () => rolesApi.create({ name, scope_kind: scope, grants: [] }),
    onSuccess: async () => {
      setAdding(false)
      setName('')
      await refresh()
    },
  })

  const rows = roles.data ?? []
  const permissions = catalogue.data ?? []

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline gap-3">
        <div>
          <h1 className="text-xl font-semibold">{t('admin:roles.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('admin:roles.description')}</p>
        </div>
        <Button
          className="ml-auto shrink-0 text-xs"
          variant={adding ? 'ghost' : 'primary'}
          onClick={() => { setAdding(!adding) }}
        >
          {adding ? t('common:action.cancel') : t('admin:roles.add')}
        </Button>
      </header>

      {roles.isError ? <Alert>{describeError(roles.error)}</Alert> : null}
      {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}

      {adding ? (
        <Card>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              create.mutate()
            }}
          >
            <Field
              label={t('admin:roles.name')}
              required
              value={name}
              onChange={(event) => { setName(event.target.value) }}
            />
            <Select
              label={t('admin:roles.scope')}
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as (typeof SCOPES)[number])
              }}
            >
              {SCOPES.map((kind) => (
                <option key={kind} value={kind}>
                  {t(`admin:roles.scope.${kind}`)}
                </option>
              ))}
            </Select>
            <Button type="submit" loading={create.isPending}>
              {t('admin:roles.save')}
            </Button>
          </form>
          {/* 권한은 만든 **뒤에** 고른다. 만들기 폼에 권한 목록 서른 개를
              펼쳐 두면 이름 하나 넣으려는 사람이 그 앞에서 멈춘다. */}
          <p className="mt-3 text-sm text-muted">{t('admin:roles.grantsAfter')}</p>
        </Card>
      ) : null}

      <ul className="flex flex-col gap-2" aria-label={t('admin:roles.title')}>
        {rows.map((role) => (
          <li key={role.id}>
            <RoleCard
              role={role}
              permissions={permissions}
              expanded={open === role.id}
              onToggle={() => { setOpen(open === role.id ? null : role.id) }}
            />
          </li>
        ))}
      </ul>
    </section>
  )
}

function RoleCard({
  role,
  permissions,
  expanded,
  onToggle,
}: {
  role: Role
  permissions: PermissionDef[]
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['roles'] })

  const save = useMutation({
    mutationFn: (grants: string[]) => rolesApi.update(role.id, { grants }),
    onSuccess: () => { void refresh() },
  })

  const remove = useMutation({
    mutationFn: () => rolesApi.remove(role.id),
    onSuccess: () => { void refresh() },
  })

  // 이 역할의 스코프에서 쓸 수 있는 권한만 보여 준다. 나머지를 고르게 하면
  // 저장은 되고 평가에서 조용히 무시된다 — "줬는데 안 된다" 가 된다.
  const usable = permissions.filter((def) => def.scope_kinds.includes(role.scope_kind))
  const held = new Set(role.grants)

  const toggle = (key: string) => {
    const next = new Set(held)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    save.mutate([...next])
  }

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="truncate text-sm font-medium">
            {role.name}
            <span className="ml-2 align-middle">
              <Badge>{t(`admin:roles.scope.${role.scope_kind}`)}</Badge>
            </span>
            {role.is_builtin ? (
              <span className="ml-1 align-middle">
                <Badge tone="info">{t('admin:roles.builtin')}</Badge>
              </span>
            ) : null}
            {role.require_mfa ? (
              <span className="ml-1 align-middle">
                <Badge tone="info">{t('admin:users.mfaRequired')}</Badge>
              </span>
            ) : null}
          </p>
          <p className="text-xs text-muted">
            {t('admin:roles.grantCount', { count: role.grants.length })} ·{' '}
            {t('admin:roles.assignedTo', { count: role.assignment_count })}
          </p>
        </div>

        <div className="ml-auto flex shrink-0 gap-1">
          <Button
            variant="ghost"
            className="text-xs"
            aria-label={t('admin:roles.editLabel', { name: role.name })}
            aria-expanded={expanded}
            onClick={onToggle}
          >
            {t('admin:roles.edit')}
          </Button>
          {role.is_builtin ? null : confirming ? (
            <>
              <Button
                variant="secondary"
                className="text-xs"
                aria-label={t('admin:roles.confirmDeleteLabel', { name: role.name })}
                loading={remove.isPending}
                onClick={() => { remove.mutate() }}
              >
                {t('common:action.confirm')}
              </Button>
              <Button variant="ghost" className="text-xs" onClick={() => { setConfirming(false) }}>
                {t('common:action.cancel')}
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              className="text-xs"
              aria-label={t('admin:roles.deleteLabel', { name: role.name })}
              onClick={() => { setConfirming(true) }}
            >
              {t('common:action.delete')}
            </Button>
          )}
        </div>
      </div>

      {confirming ? <p className="text-sm text-muted">{t('admin:roles.deleteWarning')}</p> : null}
      {save.isError ? <Alert>{describeError(save.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {expanded ? (
        <div className="flex flex-col gap-3 border-t border-border pt-3">
          {role.is_builtin ? (
            // 왜 손잡이가 없는지 적는다. 없는 것과 못 하는 것은 다르다.
            <p className="text-sm text-muted">{t('admin:roles.builtinHint')}</p>
          ) : (
            <fieldset className="flex flex-col gap-2">
              <legend className="text-sm font-medium">{t('admin:roles.grants')}</legend>
              <div className="flex flex-wrap gap-1">
                {usable.map((def) => (
                  <Chip
                    key={def.key}
                    pressed={held.has(def.key)}
                    disabled={save.isPending}
                    // 정확한 권한 문자열은 툴팁과 **읽히는 이름**에 함께
                    // 넣는다. 툴팁만 두면 키보드·스크린 리더로 쓰는 사람에게는
                    // 없는 값이고, 문서·설정을 맞출 때 필요한 것은 키 쪽이다.
                    title={def.key}
                    aria-label={t('admin:permissionWithKey', {
                      name: t(`admin:permission.${def.key}`),
                      key: def.key,
                    })}
                    onClick={() => { toggle(def.key) }}
                  >
                    {t(`admin:permission.${def.key}`)}
                    {/* step-up 이 붙은 권한은 표시한다. 준 사람이 "왜 또
                        코드를 묻지" 를 겪기 전에 알아야 한다. */}
                    {def.requires_step_up ? ' ·2FA' : ''}
                  </Chip>
                ))}
              </div>
            </fieldset>
          )}
          <Assignments role={role} />
        </div>
      ) : null}
    </Card>
  )
}

function Assignments({ role }: { role: Role }) {
  const { t } = useTranslation(['admin', 'common'])
  const queryClient = useQueryClient()
  const [picked, setPicked] = useState('')
  const [kind, setKind] = useState<'user' | 'group'>('user')

  const assignments = useQuery({
    queryKey: ['roles', role.id, 'assignments'],
    queryFn: () => rolesApi.assignments(role.id),
  })
  // 그룹은 통째로 받는다 — 수가 적고 서버가 전부 준다. 사람은 그럴 수 없어서
  // 검색으로 고른다(PersonPicker 주석 참고).
  const groups = useQuery({ queryKey: ['groups'], queryFn: () => groupsApi.list() })

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['roles'] })
  }

  const give = useMutation({
    mutationFn: (principalId: string) =>
      rolesApi.assign({
        role_id: role.id,
        scope_kind: role.scope_kind,
        scope_id: null,
        principal_kind: kind,
        principal_id: principalId,
      }),
    onSuccess: async () => {
      setPicked('')
      await refresh()
    },
  })

  const take = useMutation({
    mutationFn: (id: string) => rolesApi.revoke(id),
    onSuccess: refresh,
  })

  const rows = assignments.data ?? []
  //: 이미 받은 주체. 후보에서 빼면 두 번 주는 실수를 막는다.
  const already = new Set(rows.map((row) => row.principal_id))

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-medium">{t('admin:roles.assignments')}</p>
      {assignments.isError ? <Alert>{describeError(assignments.error)}</Alert> : null}
      {give.isError ? <Alert>{describeError(give.error)}</Alert> : null}
      {take.isError ? <Alert>{describeError(take.error)}</Alert> : null}

      {/* 전역 역할만 여기서 준다. 프로젝트·스페이스 스코프는 그 대상을 고르는
          자리가 따로 있어야 하고, 그건 프로젝트 설정 화면의 일이다. */}
      {role.scope_kind === 'global' ? (
        <div className="flex flex-col gap-2">
          <Select
            label={t('admin:roles.principalKind')}
            className="max-w-40"
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as 'user' | 'group')
              setPicked('')
            }}
          >
            <option value="user">{t('admin:roles.principalUser')}</option>
            <option value="group">{t('admin:roles.principalGroup')}</option>
          </Select>

          {kind === 'user' ? (
            <PersonPicker
              label={t('admin:roles.giveTo')}
              exclude={already}
              busy={give.isPending}
              onPick={(userId) => { give.mutate(userId) }}
            />
          ) : (
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                if (picked) give.mutate(picked)
              }}
            >
              <Select
                label={t('admin:roles.giveTo')}
                className="min-w-56"
                value={picked}
                onChange={(event) => { setPicked(event.target.value) }}
              >
                <option value="">{t('admin:roles.pickPrincipal')}</option>
                {(groups.data ?? [])
                  .filter((group) => !already.has(group.id))
                  .map((group) => (
                    <option key={group.id} value={group.id}>
                      {group.name}
                    </option>
                  ))}
              </Select>
              <Button type="submit" disabled={!picked} loading={give.isPending} className="text-xs">
                {t('common:action.add')}
              </Button>
            </form>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted">{t('admin:roles.scopedElsewhere')}</p>
      )}

      <ul className="flex flex-col gap-1" aria-label={t('admin:roles.assignments')}>
        {rows.map((row: RoleAssignment) => (
          <li key={row.id} className="flex items-center gap-2 text-sm">
            <span className="truncate">
              {/* 주체가 지워졌으면 이름이 없다. 감추지 않는다 — 회수할 수
                  있어야 하고, 무엇을 회수하는지 보여야 한다. */}
              {row.principal_label ?? t('admin:roles.deletedPrincipal', { id: row.principal_id })}
              <span className="ml-1 text-muted">
                {t(`admin:roles.principal.${row.principal_kind}`)}
              </span>
            </span>
            <Button
              variant="ghost"
              className="ml-auto shrink-0 text-xs"
              aria-label={t('admin:roles.revokeLabel', {
                name: row.principal_label ?? row.principal_id,
              })}
              loading={take.isPending}
              onClick={() => { take.mutate(row.id) }}
            >
              {t('admin:roles.revoke')}
            </Button>
          </li>
        ))}
      </ul>

      {!assignments.isPending && rows.length === 0 ? (
        <p className="text-sm text-muted">{t('admin:roles.noAssignments')}</p>
      ) : null}
    </div>
  )
}
