import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { issuesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card, Field } from '@/shared/ui/primitives'

import { formatDuration, parseDuration } from './duration'
import { formatDate } from './format'
import { useUserNames } from './hooks'

export function TimeTracking({
  issueId,
  version,
}: {
  issueId: string
  /** 낙관적 잠금용. 추정을 고칠 때 같이 보낸다. */
  version: number
}) {
  const { t } = useTranslation(['issues', 'common'])
  const queryClient = useQueryClient()

  const panel = useQuery({
    queryKey: ['issues', 'worklogs', issueId],
    queryFn: () => issuesApi.worklogs(issueId),
  })
  const authors = useUserNames((panel.data?.items ?? []).map((w) => w.user_id))

  const [amount, setAmount] = useState('')
  const [workDate, setWorkDate] = useState('')
  const [comment, setComment] = useState('')

  const minutes = parseDuration(amount)
  const invalid = amount.trim() !== '' && minutes === null

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['issues', 'worklogs', issueId] })
  }

  const add = useMutation({
    mutationFn: () =>
      issuesApi.addWorklog(issueId, {
        spent_minutes: minutes as number,
        ...(workDate ? { work_date: workDate } : {}),
        ...(comment.trim() ? { comment } : {}),
      }),
    onSuccess: () => {
      setAmount('')
      setComment('')
      refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (worklogId: string) => issuesApi.deleteWorklog(worklogId),
    onSuccess: refresh,
  })

  const [estimateDraft, setEstimateDraft] = useState<string | null>(null)
  const estimateMinutes = estimateDraft === null ? null : parseDuration(estimateDraft)
  const setEstimate = useMutation({
    mutationFn: () =>
      issuesApi.change(issueId, { estimate_minutes: estimateMinutes }, version),
    onSuccess: () => {
      setEstimateDraft(null)
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['issues', 'detail'] })
    },
  })

  const summary = panel.data?.summary

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-medium text-muted">{t('issues:time.title')}</h2>

      {summary ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <Stat label={t('issues:time.estimate')}>
              {estimateDraft === null ? (
                <button
                  type="button"
                  className="hover:text-accent"
                  onClick={() => {
                    setEstimateDraft(
                      summary.estimate_minutes === null
                        ? ''
                        : formatDuration(summary.estimate_minutes),
                    )
                  }}
                >
                  {summary.estimate_minutes === null
                    ? t('issues:time.noEstimate')
                    : formatDuration(summary.estimate_minutes)}
                </button>
              ) : (
                <span className="flex items-center gap-1">
                  <input
                    aria-label={t('issues:time.setEstimate')}
                    className="w-24 rounded border border-border bg-surface px-1.5 py-0.5 text-sm"
                    value={estimateDraft}
                    onChange={(e) => { setEstimateDraft(e.target.value); }}
                  />
                  <Button
                    className="px-2 py-0.5 text-xs"
                    loading={setEstimate.isPending}
                    disabled={estimateDraft.trim() !== '' && estimateMinutes === null}
                    onClick={() => { setEstimate.mutate(); }}
                  >
                    {t('issues:detail.save')}
                  </Button>
                  <Button
                    variant="ghost"
                    className="px-2 py-0.5 text-xs"
                    onClick={() => { setEstimateDraft(null); }}
                  >
                    {t('issues:detail.cancel')}
                  </Button>
                </span>
              )}
            </Stat>
            <Stat label={t('issues:time.spent')}>{formatDuration(summary.spent_minutes)}</Stat>
            {summary.remaining_minutes !== null ? (
              <Stat label={t('issues:time.remaining')}>
                <span className={summary.over_estimate ? 'text-danger' : undefined}>
                  {summary.over_estimate
                    ? t('issues:time.over', {
                        amount: formatDuration(-summary.remaining_minutes),
                      })
                    : formatDuration(summary.remaining_minutes)}
                </span>
              </Stat>
            ) : null}
          </div>

          {summary.estimate_minutes !== null && summary.estimate_minutes > 0 ? (
            <ProgressBar
              spent={summary.spent_minutes}
              estimate={summary.estimate_minutes}
              over={summary.over_estimate}
            />
          ) : null}
        </div>
      ) : null}

      {panel.isError ? <Alert>{describeError(panel.error)}</Alert> : null}
      {setEstimate.isError ? <Alert>{describeError(setEstimate.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}

      {panel.data && panel.data.items.length > 0 ? (
        <ul className="flex flex-col gap-2 border-t border-border pt-3 text-sm">
          {panel.data.items.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium">{formatDuration(entry.spent_minutes)}</span>
              <span className="text-xs text-muted">{formatDate(entry.work_date)}</span>
              <span className="text-xs text-muted">
                {entry.user_id ? (authors.data?.get(entry.user_id) ?? '…') : '—'}
              </span>
              {entry.comment ? <span className="text-muted">{entry.comment}</span> : null}
              <Button
                variant="ghost"
                className="ml-auto px-1 py-0 text-xs"
                aria-label={t('issues:time.delete')}
                loading={remove.isPending && remove.variables === entry.id}
                onClick={() => { remove.mutate(entry.id); }}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      ) : panel.data ? (
        <p className="text-sm text-muted">{t('issues:time.empty')}</p>
      ) : null}

      <form
        className="flex flex-col gap-3 border-t border-border pt-3"
        onSubmit={(event) => { event.preventDefault(); if (minutes !== null) add.mutate() }}
      >
        {add.isError ? <Alert>{describeError(add.error)}</Alert> : null}
        <div className="flex flex-wrap items-start gap-3">
          <div className="w-40">
            <Field
              label={t('issues:time.amount')}
              placeholder="1d 2h 30m"
              // 입력한 값이 몇 분으로 읽혔는지 바로 보여준다. 60배 틀린 값을
              // 저장하고 나서 알아채는 것보다 낫다.
              hint={
                minutes === null
                  ? t('issues:time.amountHint')
                  : `= ${formatDuration(minutes)} (${String(minutes)}m)`
              }
              error={invalid ? t('issues:time.amountInvalid') : undefined}
              value={amount}
              onChange={(e) => { setAmount(e.target.value); }}
            />
          </div>
          <div className="w-40">
            <Field
              label={t('issues:time.date')}
              type="date"
              max={new Date().toISOString().slice(0, 10)}
              value={workDate}
              onChange={(e) => { setWorkDate(e.target.value); }}
            />
          </div>
          <div className="min-w-48 flex-1">
            <Field
              label={t('issues:time.comment')}
              value={comment}
              onChange={(e) => { setComment(e.target.value); }}
            />
          </div>
        </div>
        <Button type="submit" loading={add.isPending} disabled={minutes === null}>
          {t('issues:time.submit')}
        </Button>
      </form>
    </Card>
  )
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-xs text-muted">{label}</span>
      <span className="font-medium">{children}</span>
    </span>
  )
}

function ProgressBar({
  spent,
  estimate,
  over,
}: {
  spent: number
  estimate: number
  over: boolean
}) {
  const ratio = Math.min(1, spent / estimate)
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={estimate}
      aria-valuenow={spent}
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
    >
      <div
        className={clsx('h-full rounded-full', over ? 'bg-danger' : 'bg-accent')}
        style={{ width: `${String(Math.round(ratio * 100))}%` }}
      />
    </div>
  )
}
