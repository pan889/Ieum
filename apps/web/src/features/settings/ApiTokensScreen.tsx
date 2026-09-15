import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { formatDateTime, formatRelative } from '@/features/issues/format'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { apiTokensApi, rolesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Chip, EmptyState, Field, Select } from '@/shared/ui/primitives'

const EXPIRY_CHOICES = [30, 90, 365] as const

export function ApiTokensScreen() {
  const { t } = useTranslation(['auth', 'admin', 'common'])
  const queryClient = useQueryClient()

  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => apiTokensApi.list() })
  const permissions = useQuery({
    queryKey: ['permissions'],
    queryFn: () => rolesApi.permissions(),
    staleTime: 300_000,
  })

  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>([])
  const [expiry, setExpiry] = useState<string>('90')
  const [secret, setSecret] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const create = useMutation({
    mutationFn: () =>
      apiTokensApi.issue({
        name,
        scopes,
        ...(expiry === '' ? {} : { expires_in_days: Number(expiry) }),
      }),
    onSuccess: (issued) => {
      setSecret(issued.secret)
      setCreating(false)
      setName('')
      setScopes([])
      void queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
  })

  const revoke = useMutation({
    mutationFn: (id: string) => apiTokensApi.revoke(id),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ['tokens'] }) },
  })

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold">{t('auth:tokens.title')}</h1>
          <p className="mt-1 text-sm text-muted">{t('auth:tokens.description')}</p>
        </div>
        <Button onClick={() => { setCreating((v) => !v); }}>{t('auth:tokens.create')}</Button>
      </header>

      {secret ? (
        <Card className="flex flex-col gap-2 border-accent">
          <p className="text-sm font-medium">{t('auth:tokens.secretOnce')}</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded bg-surface-raised px-2 py-1.5 font-mono text-xs">
              {secret}
            </code>
            <Button
              variant="secondary"
              onClick={() => {
                void navigator.clipboard.writeText(secret).then(() => { setCopied(true); })
              }}
            >
              {copied ? t('common:action.copied') : t('common:action.copy')}
            </Button>
            <Button variant="ghost" onClick={() => { setSecret(null); setCopied(false) }}>
              {t('common:action.close')}
            </Button>
          </div>
        </Card>
      ) : null}

      {creating ? (
        <Card>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => { event.preventDefault(); create.mutate() }}
          >
            {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}

            <Field
              label={t('auth:tokens.name')}
              hint={t('auth:tokens.nameHint')}
              required
              value={name}
              onChange={(e) => { setName(e.target.value); }}
            />

            <Select
              label={t('auth:tokens.expiry')}
              value={expiry}
              onChange={(e) => { setExpiry(e.target.value); }}
            >
              {EXPIRY_CHOICES.map((days) => (
                <option key={days} value={days}>
                  {t('auth:tokens.expiryDays', { count: days })}
                </option>
              ))}
              <option value="">{t('auth:tokens.expiryNever')}</option>
            </Select>

            <div className="flex flex-col gap-2">
              <span className="text-sm font-medium">{t('auth:tokens.scopes')}</span>
              <p className="text-xs text-muted">{t('auth:tokens.scopesHint')}</p>
              <div className="flex max-h-64 flex-wrap gap-1.5 overflow-y-auto">
                {(permissions.data ?? []).map((definition) => (
                  <Chip
                    key={definition.key}
                    pressed={scopes.includes(definition.key)}
                    // 예전에는 서버가 준 한국어 설명이 툴팁이었다 — 영어
                    // 화면에서 한국어가 떴다. 이름은 번역한다.
                    //
                    // 정확한 권한 문자열은 **읽히는 이름에도** 넣는다. 툴팁만
                    // 두면 키보드·스크린 리더로 쓰는 사람에게는 없는 값이고,
                    // 스코프를 스크립트에 그대로 적어야 하는 자리가 여기다.
                    title={definition.key}
                    aria-label={t('admin:permissionWithKey', {
                      name: t(`admin:permission.${definition.key}`),
                      key: definition.key,
                    })}
                    onClick={() => {
                      setScopes((current) =>
                        current.includes(definition.key)
                          ? current.filter((s) => s !== definition.key)
                          : [...current, definition.key],
                      )
                    }}
                  >
                    {t(`admin:permission.${definition.key}`)}
                  </Chip>
                ))}
              </div>
            </div>

            <Button
              type="submit"
              loading={create.isPending}
              disabled={name.trim() === '' || scopes.length === 0}
            >
              {t('auth:tokens.submit')}
            </Button>
          </form>
        </Card>
      ) : null}

      {revoke.isError ? <Alert>{describeError(revoke.error)}</Alert> : null}

      {tokens.isPending ? (
        <p className="text-sm text-muted">{t('common:state.loading')}</p>
      ) : tokens.isError ? (
        <Alert>{describeError(tokens.error)}</Alert>
      ) : tokens.data.length === 0 ? (
        <EmptyState title={t('auth:tokens.empty')} />
      ) : (
        <ul className="flex flex-col gap-2">
          {tokens.data.map((token) => (
            <li key={token.id}>
              <Card className="flex flex-col gap-1.5 py-3">
                <div className="flex items-baseline gap-3">
                  <span className="font-medium">{token.name}</span>
                  <span className="text-xs text-muted">
                    {token.last_used_at
                      ? t('auth:tokens.lastUsed', { when: formatRelative(token.last_used_at) })
                      : t('auth:tokens.neverUsed')}
                  </span>
                  <span className="text-xs text-muted">
                    {token.expires_at
                      ? t('auth:tokens.expiresAt', { when: formatDateTime(token.expires_at) })
                      : t('auth:tokens.noExpiry')}
                  </span>
                  <Button
                    variant="ghost"
                    className="ml-auto text-xs"
                    loading={revoke.isPending && revoke.variables === token.id}
                    onClick={() => { revoke.mutate(token.id); }}
                  >
                    {t('auth:tokens.revoke')}
                  </Button>
                </div>
                <div className="flex flex-wrap gap-1">
                  {token.scopes.map((scope) => (
                    <code key={scope} className="rounded bg-surface-raised px-1 py-0.5 text-xs">
                      {scope}
                    </code>
                  ))}
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
