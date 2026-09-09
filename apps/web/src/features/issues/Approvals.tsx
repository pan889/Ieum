/**
 * 이 티켓의 승인 (C12, M6).
 *
 * **기다리는 승인은 반드시 보인다.** 승인은 아무 일도 일어나지 않는 방식으로
 * 고장난다: 착수가 막혀 있는데 왜 막혔는지가 화면에 없으면, 상담원은 전이
 * 버튼을 누르고 409 를 받고 이유를 모른다. 그래서 이 칸은 기다리는 동안
 * 무엇을 기다리는지(누구를, 몇 명을) 적는다.
 *
 * **끝난 승인도 남긴다.** 거절 이유는 요청자에게 설명할 근거이고, 승인
 * 기록은 나중에 "누가 열어 줬나" 의 답이다. 그래서 목록이지 상태 한 줄이
 * 아니다.
 *
 * **승인자가 없는 승인**을 그대로 보여 준다. 그룹이 비었거나 요청자가 유일한
 * 승인자였던 경우이고, 그때 티켓은 아무도 풀 수 없이 멈춰 있다 — 취소
 * 버튼이 그 자리에 있어야 한다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { Approval } from '@ieum/api-client'

import { ApprovalDecision } from '@/features/desk/ApprovalDecision'
import { approvalsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card } from '@/shared/ui/primitives'

import { formatDateTime } from './format'

function tone(status: Approval['status']): 'todo' | 'done' | 'danger' | 'neutral' {
  if (status === 'pending') return 'todo'
  if (status === 'approved') return 'done'
  if (status === 'declined') return 'danger'
  return 'neutral'
}

export function Approvals({ issueId }: { issueId: string }) {
  const { t } = useTranslation(['desk', 'common'])
  const queryClient = useQueryClient()
  const rows = useQuery({
    queryKey: ['approvals', 'issue', issueId],
    queryFn: () => approvalsApi.forIssue(issueId),
    staleTime: 15_000,
  })

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['approvals'] })
    // 승인이 끝나면 **전이 목록이 달라진다.** 이슈 쪽을 함께 새로 받지
    // 않으면 문이 열렸는데 화면은 여전히 막힌 것처럼 보인다.
    await queryClient.invalidateQueries({ queryKey: ['issues'] })
  }

  const cancel = useMutation({
    mutationFn: (id: string) => approvalsApi.cancel(id),
    onSuccess: refresh,
  })

  // 승인이 없는 이슈가 대부분이다. 빈 칸을 두지 않는다.
  if (!rows.isError && (rows.data ?? []).length === 0) return null

  return (
    <Card className="flex flex-col gap-3" data-testid="issue-approvals">
      <h2 className="text-sm font-medium text-muted">{t('desk:approval.title')}</h2>
      {rows.isError ? <Alert>{describeError(rows.error)}</Alert> : null}
      {cancel.isError ? <Alert>{describeError(cancel.error)}</Alert> : null}

      <ul className="flex flex-col gap-3 text-sm">
        {(rows.data ?? []).map((row) => (
          <li key={row.id} className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-baseline gap-2">
              <Badge tone={tone(row.status)}>{t(`desk:approval.status.${row.status}`)}</Badge>
              <span className="text-xs text-muted">
                {t(`desk:approval.mode.${row.mode}`)}
                {` · ${t('desk:approval.requestedAt', {
                  when: formatDateTime(row.requested_at),
                })}`}
              </span>
            </div>

            {/*
              누구를 기다리는가. **비었으면 그렇게 말한다** — 그 티켓은
              아무도 풀 수 없이 멈춰 있고, 그 사실이 여기 말고는 안 보인다.
            */}
            <p className="text-xs text-muted">
              {row.approvers.length === 0
                ? t('desk:approval.noApprovers')
                : t('desk:approval.approvers', {
                    names: row.approvers.map((person) => person.display_name).join(', '),
                  })}
            </p>

            {row.votes.length > 0 ? (
              <ul className="flex flex-col gap-0.5 text-xs">
                {row.votes.map((vote) => (
                  <li key={vote.user_id}>
                    <span className={vote.decision === 'approve' ? 'text-fg' : 'text-danger'}>
                      {t(`desk:approval.decision.${vote.decision}`, {
                        name: vote.display_name,
                      })}
                    </span>
                    <span className="ml-2 text-muted">{formatDateTime(vote.decided_at)}</span>
                    {vote.comment ? <p className="text-muted">{vote.comment}</p> : null}
                  </li>
                ))}
              </ul>
            ) : null}

            {row.can_decide ? (
              <ApprovalDecision approvalId={row.id} onDecided={refresh} />
            ) : null}

            {row.status === 'pending' ? (
              <div>
                <Button
                  variant="ghost"
                  className="text-xs"
                  loading={cancel.isPending && cancel.variables === row.id}
                  onClick={() => { cancel.mutate(row.id) }}
                >
                  {t('desk:approval.cancel')}
                </Button>
                <p className="text-xs text-muted">{t('desk:approval.cancelHint')}</p>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  )
}
