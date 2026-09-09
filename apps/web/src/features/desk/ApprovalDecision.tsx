/**
 * 승인·거절 손잡이 (C12).
 *
 * **두 화면이 같은 것을 쓴다.** 이슈 상세(상담원이 맥락을 보며)와 첫 화면의
 * "내가 승인할 것"(승인자가 자기 몫만 보며)이다. 승인자는 그 프로젝트의
 * 이슈를 볼 권한이 없을 수 있으므로 — 근거가 권한이 아니라 명단이다 —
 * 이슈 상세로 보내는 것만으로는 결정할 길이 없다. 실제로 그렇게 만들었고,
 * 승인자로 로그인해 보니 링크를 눌러 403 을 받았다.
 *
 * 이유 칸은 **거절에 사실상 필요하다.** 이유 없는 거절을 받은 사람은 무엇을
 * 고쳐 다시 낼지 모른다. 그래도 강제하지 않는다 — 승인에는 필요 없고,
 * 강제하면 승인이 느려진다.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { approvalsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Field } from '@/shared/ui/primitives'

export function ApprovalDecision({
  approvalId,
  onDecided,
}: {
  approvalId: string
  onDecided: () => void | Promise<void>
}) {
  const { t } = useTranslation(['desk'])
  const [comment, setComment] = useState('')

  const decide = useMutation({
    mutationFn: (decision: 'approve' | 'decline') =>
      approvalsApi.decide(approvalId, {
        decision,
        ...(comment.trim() ? { comment: comment.trim() } : {}),
      }),
    onSuccess: async () => {
      setComment('')
      await onDecided()
    },
  })

  return (
    <div className="flex flex-col gap-2">
      {decide.isError ? <Alert>{describeError(decide.error)}</Alert> : null}
      <Field
        label={t('desk:approval.comment')}
        hint={t('desk:approval.commentHint')}
        value={comment}
        onChange={(event) => { setComment(event.target.value) }}
      />
      <div className="flex gap-2">
        <Button
          loading={decide.isPending && decide.variables === 'approve'}
          onClick={() => { decide.mutate('approve') }}
        >
          {t('desk:approval.approve')}
        </Button>
        <Button
          variant="secondary"
          loading={decide.isPending && decide.variables === 'decline'}
          onClick={() => { decide.mutate('decline') }}
        >
          {t('desk:approval.decline')}
        </Button>
      </div>
    </div>
  )
}
