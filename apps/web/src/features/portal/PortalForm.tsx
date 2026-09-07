/**
 * 요청 폼. 로그인한 고객과 게스트가 같은 폼을 쓴다.
 *
 * 게스트에게는 이름·이메일을 더 묻는다. 그 주소로는 **요청 번호만** 보낸다 —
 * 본문을 되돌려 보내면 남의 주소로 욕설을 제출하는 것이 그 사람에게 욕설을
 * 배달하는 일이 된다. 화면에도 그렇게 적는다(`form.guestEmailHint`).
 *
 * 필수 검사를 화면에서도 한다. 서버가 어차피 거절하지만, 왕복을 기다린 뒤
 * "필수 항목이 비었다" 를 보는 것과 지금 보는 것은 다르다. 서버 검사가
 * 정본이고 이건 편의다 — 그래서 서버 오류도 그대로 보여 준다.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { PortalInfo } from '@ieum/api-client'

import { useAuthStore } from '@/features/auth/store'
import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

import { PortalField, type AnswerValue } from './PortalField'
import type { PortalRoute } from './routes'

export function PortalForm({
  portal,
  requestTypeId,
  onNavigate,
}: {
  portal: PortalInfo
  requestTypeId: string
  onNavigate: (route: PortalRoute) => void
}) {
  const { t } = useTranslation(['desk', 'common'])
  const user = useAuthStore((s) => s.user)
  const [answers, setAnswers] = useState<Record<string, AnswerValue>>({})
  const [guest, setGuest] = useState({ email: '', name: '' })
  const [filed, setFiled] = useState<{ key: string } | null>(null)

  const form = useQuery({
    queryKey: ['portal', portal.slug, 'form', requestTypeId],
    queryFn: () => deskApi.portalForm(portal.slug, requestTypeId),
    retry: false,
  })

  const submit = useMutation({
    mutationFn: async () => {
      // 빈 답은 보내지 않는다. 서버는 모르는 키를 거절하지만 **빈 값**은
      // 받아 저장하므로, 안 채운 칸이 빈 문자열로 들어가면 "적었는데 비었다"
      // 가 된다.
      const body = Object.fromEntries(
        Object.entries(answers).filter(([, value]) => {
          if (value === null) return false
          if (typeof value === 'string') return value.trim().length > 0
          if (Array.isArray(value)) return value.length > 0
          return true
        }),
      )
      if (user) {
        return deskApi.submit(portal.slug, { request_type_id: requestTypeId, answers: body })
      }
      return deskApi.submitAsGuest(portal.slug, {
        request_type_id: requestTypeId,
        email: guest.email,
        name: guest.name,
        answers: body,
      })
    },
    onSuccess: (ticket) => {
      setFiled({ key: ticket.key })
      // 로그인한 고객은 상세로 보낸다 — 진행을 볼 수 있다. 게스트는 볼 수
      // 없으므로(자기 티켓을 열 세션이 없다) 접수 확인만 남긴다.
      if (user) {
        onNavigate({ kind: 'ticket', slug: portal.slug, issueId: ticket.id })
      }
    },
  })

  if (form.isError) return <Alert>{describeError(form.error)}</Alert>
  if (!form.data) return <p className="text-sm text-muted">{t('common:state.loading')}</p>

  if (filed && !user) {
    return (
      <Card className="flex flex-col gap-2">
        <p className="text-sm font-medium">{t('desk:portal.filed')}</p>
        <p className="text-sm text-muted">{t('desk:portal.filedNumber', { key: filed.key })}</p>
        <Button
          className="self-start text-xs"
          variant="ghost"
          onClick={() => {
            onNavigate({ kind: 'home', slug: portal.slug })
          }}
        >
          {t('common:action.close')}
        </Button>
      </Card>
    )
  }

  const missing = form.data.fields.filter((field) => {
    if (!field.required) return false
    const value = answers[field.key]
    if (value === null || value === undefined) return true
    if (typeof value === 'string') return value.trim().length === 0
    if (Array.isArray(value)) return value.length === 0
    return false
  })
  const guestReady = Boolean(user) || (guest.email.trim() && guest.name.trim())

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        submit.mutate()
      }}
    >
      <header>
        <h1 className="text-xl font-semibold">{form.data.request_type.name}</h1>
        {form.data.request_type.description ? (
          <p className="mt-1 text-sm text-muted">{form.data.request_type.description}</p>
        ) : null}
      </header>

      {submit.isError ? <Alert>{describeError(submit.error)}</Alert> : null}

      {form.data.fields.map((field) => (
        <PortalField
          key={field.key}
          field={field}
          value={answers[field.key] ?? null}
          onChange={(value) => {
            setAnswers((current) => ({ ...current, [field.key]: value }))
          }}
        />
      ))}

      {user ? null : (
        <Card className="flex flex-col gap-3">
          <Field
            label={t('desk:form.guestName')}
            required
            value={guest.name}
            onChange={(event) => {
              setGuest({ ...guest, name: event.target.value })
            }}
          />
          <Field
            label={t('desk:form.guestEmail')}
            hint={t('desk:form.guestEmailHint')}
            type="email"
            required
            value={guest.email}
            onChange={(event) => {
              setGuest({ ...guest, email: event.target.value })
            }}
          />
          {/* 문장만 두면 갈 길이 없는 안내다. 링크로 만든다 — 로그인한
              고객은 `App.tsx` 가 마지막으로 본 창구로 되돌려 보낸다. */}
          <p className="text-xs text-muted">
            {t('desk:form.orSignIn')}{' '}
            <a className="underline" href="/">
              {t('desk:portal.signIn')}
            </a>
          </p>
        </Card>
      )}

      {/* **비활성 버튼은 이유를 말해야 한다.** 말없이 눌리지 않는 버튼은
          "손잡이는 있고 대상만 없고 화면은 아무 말도 하지 않는" 그 자리다 —
          고객은 무엇이 남았는지 모르고 떠난다. 남은 항목의 라벨을 그대로
          읽어 준다. */}
      {missing.length > 0 ? (
        <p className="text-xs text-muted">
          {t('desk:form.missing', { fields: missing.map((field) => field.label).join(', ') })}
        </p>
      ) : null}

      <div className="flex items-center gap-2">
        <Button type="submit" disabled={missing.length > 0 || !guestReady || submit.isPending}>
          {submit.isPending ? t('desk:form.sending') : t('desk:form.submit')}
        </Button>
        <Button
          type="button"
          variant="ghost"
          className="text-xs"
          onClick={() => {
            onNavigate({ kind: 'home', slug: portal.slug })
          }}
        >
          {t('common:action.cancel')}
        </Button>
      </div>
    </form>
  )
}
