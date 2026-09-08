/**
 * 문서의 태스크 목록 (B12).
 *
 * ## 왜 본문 안의 체크박스가 아니라 옆의 목록인가
 *
 * 렌더된 본문의 체크박스를 직접 누르게 하려면 화면이 **그 체크박스가 원문
 * 몇 번째 줄인지** 알아야 한다. 그런데 이 렌더러는 본문을 디렉티브 단위로
 * 쪼개 각 조각을 따로 파싱하므로(`splitDirectives`), 토큰의 줄 번호는 조각
 * 기준이다. 절대 줄 번호로 되돌리려면 쪼개는 규칙을 서버의 파서와 맞춰
 * 유지해야 하고 — **두 파서가 줄을 세는 순간 어긋날 자리가 생긴다.** 어긋난
 * 채로 체크하면 사람이 안 누른 줄이 바뀌고, 그건 화면에 아무 표시도 없다.
 *
 * 그래서 줄 번호는 서버만 센다(`GET /pages/{id}/tasks`). 목록이 옆에 서는
 * 대신 담당자와 기한을 나란히 보여 줄 수 있어서, 본문 안 체크박스로는 못
 * 하는 것을 한다.
 *
 * ## 체크하면 판이 하나 생긴다
 *
 * 체크는 내용의 변경이다. 이력에 안 남기면 "누가 언제 이걸 끝냈다고 했나" 를
 * 답할 수 없다. 그래서 저장 버튼 없이 바로 저장되고, 화면은 그 사실을
 * 숨기지 않는다.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import type { PageTaskItem, WikiPage } from '@ieum/api-client'

import { useUserNames } from '@/features/issues/hooks'
import { wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { RichText } from '@/shared/markdown/RichText'
import { Alert, Badge, Card } from '@/shared/ui/primitives'

import { isoDay, urgencyOf, type Urgency } from './due'

const TONE: Record<Urgency, 'danger' | 'in_progress' | 'neutral'> = {
  overdue: 'danger',
  today: 'danger',
  soon: 'in_progress',
  later: 'neutral',
  none: 'neutral',
}

export function TaskPanel({ page }: { page: WikiPage }) {
  const { t } = useTranslation(['wiki', 'common'])
  const queryClient = useQueryClient()
  /**
   * **판 번호가 키에 들어간다.**
   *
   * 이 목록은 본문에서 유도된 것이므로 본문이 바뀌면 낡는다. 그런데 본문을
   * 바꾸는 길은 하나가 아니다 — 저장, 이력 되돌리기, 임포트, 같이 편집,
   * 체크. 길마다 무효화를 하나씩 붙이면 다음에 길이 하나 늘 때 조용히
   * 빠지고, 화면은 **낡은 목록을 아무 표시 없이** 보여 준다.
   *
   * 판 번호를 키에 넣으면 본문이 바뀔 때마다 키가 바뀌므로 그럴 자리가
   * 없다. 실제로 없으면 이렇게 된다: 문서를 열고(태스크 0개) 편집해
   * 체크리스트를 넣고 저장하면, 30초(전역 `staleTime`) 동안 상자가 아예
   * 안 뜬다.
   */
  const tasks = useQuery({
    queryKey: ['wiki', 'tasks', page.id, page.version_number],
    queryFn: () => wikiApi.pages.tasks(page.id),
  })

  const toggle = useMutation({
    mutationFn: (task: PageTaskItem) =>
      wikiApi.pages.toggleTask(
        page.id,
        { line: task.line, done: !task.done, expect_text: task.text },
        page.version,
      ),
    /**
     * 문서를 먼저 받고, 그 다음에 목록을 받는다.
     *
     * **이 순서는 붙잡은 보장이 아니다.** 되돌려 봐도 시험이 붉어지지
     * 않는다 — 체크가 본문과 어긋나는 문제의 진짜 원인은 서버였고(같이
     * 편집하는 방의 상태가 안 따라왔다, `collab.reconcile`), 그건 거기서
     * 고쳤다.
     *
     * 그래도 순서를 지키는 값은 있다: 목록이 먼저 도착하면 화면은 "0 / 1
     * 남음" 이라고 말하는데 이 컴포넌트가 받은 `page` 는 아직 옛 판이다.
     * 그 순간 편집을 열면 방이 없는 경우(소켓이 못 붙은 경우) 옛 본문이
     * 잠깐 뜨고, 저장은 판 번호가 안 맞아 거절된다. 잃는 것은 없지만
     * 사람에게는 설명할 수 없는 거절이다.
     *
     * 문서가 오면 판 번호가 바뀌어 위의 키가 달라지므로 목록은 저절로 다시
     * 받는다. 그래도 무효화를 남겨 두는 이유: 판 번호 없이 본문만 바뀌는
     * 길이 생기는 날에도 목록은 갱신돼야 한다.
     */
    onSuccess: async () => {
      await queryClient.refetchQueries({ queryKey: ['wiki', 'page'], type: 'active' })
      await queryClient.invalidateQueries({ queryKey: ['wiki', 'tasks', page.id] })
    },
  })

  const rows = tasks.data ?? []
  const names = useUserNames(rows.map((row) => row.assignee_id))
  const today = isoDay(new Date())
  const open = rows.filter((row) => !row.done).length

  // 태스크가 없는 문서에는 아무것도 그리지 않는다. 빈 상자를 두면 모든
  // 문서에 쓸모 없는 칸이 하나 생긴다.
  if (tasks.isSuccess && rows.length === 0) return null

  return (
    <Card className="flex flex-col gap-3" data-testid="task-panel">
      <h2 className="flex items-baseline gap-2 text-sm font-semibold">
        {t('wiki:tasks.title')}
        <span className="text-xs font-normal text-muted">
          {t('wiki:tasks.openOf', { open, total: rows.length })}
        </span>
      </h2>

      {tasks.isError ? <Alert>{describeError(tasks.error)}</Alert> : null}
      {/*
        거절당하면 **왜인지 말한다.** 그 사이 남이 줄을 고쳤다는 뜻이므로,
        조용히 아무 일도 안 하면 사람은 클릭이 안 먹는다고 생각한다.
      */}
      {toggle.isError ? <Alert>{describeError(toggle.error)}</Alert> : null}

      <ul className="flex flex-col gap-1.5">
        {rows.map((row) => {
          const urgency = urgencyOf(row.due_date, today)
          return (
            <li key={row.line} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={row.done}
                disabled={toggle.isPending}
                aria-label={row.text || t('wiki:tasks.untitled')}
                onChange={() => {
                  toggle.mutate(row)
                }}
              />
              <span className="flex flex-1 flex-wrap items-baseline gap-2">
                {/* 멘션이 마크다운으로 남아 있으므로 그대로 렌더한다 —
                    담당자가 링크로 보인다. */}
                <RichText
                  source={row.text}
                  className={row.done ? 'text-muted line-through' : undefined}
                />
                {row.assignee_id === null ? null : (
                  <span className="text-xs text-muted">
                    {names.data?.get(row.assignee_id) ?? ''}
                  </span>
                )}
                {row.due_date === null ? null : (
                  <Badge tone={row.done ? 'neutral' : TONE[urgency]}>
                    {t(`wiki:tasks.due.${urgency}`, { date: row.due_date })}
                  </Badge>
                )}
              </span>
            </li>
          )
        })}
      </ul>

      <p className="text-xs text-muted">{t('wiki:tasks.hint')}</p>
    </Card>
  )
}
