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

/**
 * 요청 중 문서 추천 (feature-map C8).
 *
 * 고객이 제목을 적는 동안 지식베이스에서 찾아 보여 준다. **답이 이미 있는
 * 요청이 티켓으로 들어오지 않게** 하는 것이 요점이다 — 상담원이 같은 답을
 * 백 번 쓰는 일이 데스크에서 가장 흔하다.
 *
 * 뜨는 것은 **제한 없는 공개 문서**뿐이고, 그 좁힘은 서버가 SQL 단계에서
 * 한다. 화면은 거르지 않는다: 가져와서 거르면 한 군데만 빠뜨려도 유출이다.
 *
 * 폼을 막지 않는다. 문서를 보여 주고도 요청을 낼 수 있어야 한다 — 추천이
 * 틀렸을 때 사람을 가둬 두는 화면이 된다.
 */
function Articles({
  slug,
  requestTypeId,
  query,
}: {
  slug: string
  requestTypeId: string
  query: string
}) {
  const { t } = useTranslation(['desk'])
  const text = query.trim()

  const articles = useQuery({
    queryKey: ['portal', slug, 'articles', requestTypeId, text],
    // **두 글자부터 찾는다.** 한 글자로 찾으면 거의 모든 문서가 걸리고,
    // 고객이 첫 글자를 치자마자 목록이 튀어나온다.
    enabled: text.length >= 2,
    queryFn: () => deskApi.portalArticles(slug, requestTypeId, text),
  })

  const rows = articles.data ?? []
  if (rows.length === 0) return null

  return (
    <Card className="flex flex-col gap-2">
      <h2 className="text-sm font-medium">{t('desk:form.articles')}</h2>
      <p className="text-xs text-muted">{t('desk:form.articlesHint')}</p>
      <ul className="flex flex-col gap-2">
        {rows.map((article) => (
          <li key={article.page_id} className="flex flex-col gap-0.5">
            {/* 위키는 앱 셸 안이라 고객이 열 수 있는 주소가 아니다. 지금은
                제목과 발췌만 보여 준다 — 공개 문서 주소(B13 의 블로그와 같은
                표면)가 생기면 그때 링크를 건다. 없는 링크를 그려 두고 404 로
                보내는 것보다 낫다. */}
            <span className="text-sm font-medium">{article.title}</span>
            <span className="text-xs text-muted">{article.excerpt}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

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

      {/* 제목 아래에 둔다. 폼 맨 위에 두면 아직 아무 것도 안 적었을 때 빈
          자리가 되고, 맨 아래면 다 적고 나서야 보인다 — 다 적은 사람은
          문서를 읽지 않고 보낸다. */}
      <Articles
        slug={portal.slug}
        requestTypeId={requestTypeId}
        query={typeof answers['summary'] === 'string' ? answers['summary'] : ''}
      />

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
