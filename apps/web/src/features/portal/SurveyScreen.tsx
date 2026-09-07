/**
 * 만족도 조사 (feature-map C11).
 *
 * 티켓을 닫으면 고객에게 링크가 간다. 그 링크가 여는 화면이다.
 *
 * **로그인 앞에 있다.** 게스트 요청(C1)에는 계정이 아예 없고, 계정이 있는
 * 고객에게도 로그인부터 시키면 응답률이 떨어진다 — 그러면 모은 점수는
 * "로그인할 의지가 있는 사람들" 의 점수이지 만족도가 아니다. 그래서 앱 셸
 * 밖이고, 인증 게이트보다 먼저 갈린다(App.tsx) — 초대 수락과 같은 자리다.
 *
 * **점수는 다섯 개의 버튼이다.** 별점 위젯을 만들지 않는다: 별은 몇 개를
 * 눌러야 하는지가 눈으로만 전달되고, 화면 낭독기에는 이름 없는 버튼 다섯
 * 개로 들린다. 각 점수가 무슨 뜻인지 글자로 적어 둔다.
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AuthLayout } from '@/features/auth/LoginScreen'
import { deskApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Textarea } from '@/shared/ui/primitives'

const SCORES = [1, 2, 3, 4, 5]

/** 주소에서 조사 토큰을 꺼낸다. 없으면 null. */
export function surveyToken(search: string): string | null {
  const value = new URLSearchParams(search).get('token')
  return value && value.trim() !== '' ? value : null
}

export function SurveyScreen() {
  const { t } = useTranslation(['desk', 'common'])
  const token = surveyToken(window.location.search)
  const [score, setScore] = useState<number | null>(null)
  const [comment, setComment] = useState('')

  const survey = useQuery({
    queryKey: ['survey', token],
    queryFn: () => deskApi.survey(token as string),
    enabled: token !== null,
    retry: false,
  })

  const send = useMutation({
    mutationFn: () =>
      deskApi.answerSurvey(token as string, {
        score: score as number,
        comment: comment.trim() === '' ? null : comment,
      }),
  })

  if (token === null || survey.isError) {
    // 만료·위조·지워진 티켓을 하나로 말한다. 어떤 링크가 살아 있는지
    // 알려 주는 자리가 아니다.
    return (
      <AuthLayout title={t('desk:csat.title')}>
        <Alert>{t('desk:csat.gone')}</Alert>
      </AuthLayout>
    )
  }
  if (!survey.isSuccess) {
    return <AuthLayout title={t('desk:csat.title')}>{t('common:state.loading')}</AuthLayout>
  }

  const answered = send.data ?? (survey.data.score !== null ? survey.data : null)
  if (answered !== null) {
    return (
      <AuthLayout title={t('desk:csat.title')} subtitle={answered.summary}>
        <p className="text-sm text-fg">{t('desk:csat.thanks')}</p>
        <p className="mt-2 text-sm text-muted">
          {t('desk:csat.yourScore', {
            score: t(`desk:csat.score.${String(answered.score)}`),
          })}
        </p>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title={t('desk:csat.title')} subtitle={survey.data.summary}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          send.mutate()
        }}
      >
        {send.isError ? <Alert>{describeError(send.error)}</Alert> : null}

        {/* 묶음에는 묶음의 이름을 준다. 버튼 다섯 개에 각자 이름이 있어도
            "무엇을 고르는 것인지" 는 여기에만 있다. */}
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium text-fg">{t('desk:csat.question')}</legend>
          <div className="flex flex-wrap gap-2">
            {SCORES.map((value) => (
              <Button
                key={value}
                type="button"
                variant={score === value ? 'primary' : 'ghost'}
                aria-pressed={score === value}
                className="flex-1 text-xs"
                onClick={() => { setScore(value) }}
              >
                {t(`desk:csat.score.${String(value)}`)}
              </Button>
            ))}
          </div>
        </fieldset>

        <Textarea
          label={t('desk:csat.comment')}
          hint={t('desk:csat.commentHint')}
          rows={4}
          value={comment}
          onChange={(event) => { setComment(event.target.value) }}
        />

        {/* 점수를 안 고르면 보낼 수 없다. 한마디만 오는 답을 받으면 평균의
            분모를 말할 수 없다 — 서버도 거절한다. */}
        <Button type="submit" loading={send.isPending} disabled={score === null}>
          {t('desk:csat.send')}
        </Button>
      </form>
    </AuthLayout>
  )
}
