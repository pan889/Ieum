/**
 * 첫 화면 — **오늘 뭘 해야 하나** (M5 대시보드).
 *
 * 로그인하면 여기로 온다. 위젯을 고르고 배치하는 대시보드가 아니라 **고정
 * 구성**이다: 무엇을 보여 줄지 정하는 일까지 사람에게 미루면, 대개 아무도
 * 안 고치고 빈 화면이 남는다.
 *
 * ## 네 칸은 각자 묻는다
 *
 * 한 요청으로 몰아 받지 않는다. 스프린트 조회가 실패했다고 내게 배정된
 * 이슈까지 안 보이면, 첫 화면이 통째로 못 쓰이게 된다 — 칸마다 자기 오류를
 * 자기 자리에서 말한다.
 *
 * ## 비어 있을 때가 이 화면의 절반이다
 *
 * 빈 목록을 그냥 비워 두면 "안 불러온 것" 과 "할 일이 없는 것" 이 같아
 * 보인다. 칸마다 비었을 때 할 말을 두고, 전체 화면으로 가는 길을 함께 둔다 —
 * 여기 다섯 줄만 보이는 것이 전부가 아니라는 것도 말해야 한다.
 */

import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApprovalDecision } from '@/features/desk/ApprovalDecision'
import { categoryTone, formatDate, formatRelative } from '@/features/issues/format'
import { isoDay, urgencyOf, type Urgency } from '@/features/wiki/due'
import { approvalsApi, notificationsApi, searchApi, sprintsApi, wikiApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { RichText } from '@/shared/markdown/RichText'
import { Alert, Badge, Card } from '@/shared/ui/primitives'

import { countDue, isCalm } from './counts'

/**
 * 아직 안 끝난, 내게 배정된 것. **기한 순이다** — 첫 줄이 가장 안 급한 일이면
 * 목록을 보는 이유가 없다.
 *
 * `currentUser()` 를 쓰는 이유: 화면이 자기 id 를 문장에 끼워 넣으면 그 문장을
 * 복사해 남에게 준 사람이 **남의 목록을 자기 것으로** 보게 된다.
 */
export const MY_ISSUES_IQL =
  'assignee = currentUser() AND statusCategory != done ORDER BY due ASC'

/** 한 칸에 보여 줄 줄 수. 넘으면 "전체 보기" 로 간다. */
const ROWS = 5

const MORE = 'text-xs text-accent hover:underline' 

const TONE: Record<Urgency, 'danger' | 'in_progress' | 'neutral'> = {
  overdue: 'danger',
  today: 'danger',
  soon: 'in_progress',
  later: 'neutral',
  none: 'neutral',
}

export function HomeScreen() {
  const { t } = useTranslation(['home', 'common', 'wiki', 'desk'])
  const today = isoDay(new Date())

  const issues = useQuery({
    queryKey: ['home', 'issues'],
    queryFn: () => searchApi.search({ iql: MY_ISSUES_IQL, limit: 50 }),
  })
  const tasks = useQuery({
    queryKey: ['home', 'tasks'],
    queryFn: () => wikiApi.pages.myTasks({ limit: 50 }),
  })
  const sprints = useQuery({
    queryKey: ['home', 'sprints'],
    queryFn: () => sprintsApi.mine(ROWS),
  })
  const news = useQuery({
    queryKey: ['home', 'notifications'],
    queryFn: () => notificationsApi.list({ unreadOnly: true, limit: ROWS }),
  })
  /**
   * 내가 결정해야 할 승인 (C12).
   *
   * **비면 칸을 내지 않는다.** 위 네 칸과 다른 판단이고, 이유가 있다: 저 넷은
   * 누구에게나 매일의 일이라 "없다" 도 답이지만, 승인자인 것은 대부분의
   * 사람에게 해당하지 않는 역할이다. 늘 비어 있는 칸은 첫 화면을 읽는 데
   * 방해만 된다.
   *
   * 그래도 이 칸이 있어야 하는 이유: 알림은 지나가고, 승인은 **남는 일**이다.
   * 알림 하나를 놓친 승인자는 자기 몫이 어디 있는지 볼 곳이 없다.
   */
  const approvals = useQuery({
    queryKey: ['home', 'approvals'],
    queryFn: () => approvalsApi.mine(),
  })

  const issueRows = issues.data?.items ?? []
  const taskRows = tasks.data ?? []
  const sprintRows = sprints.data ?? []
  const newsRows = news.data?.items ?? []
  const approvalRows = approvals.data ?? []
  // **이미 받은 것으로 센다.** 요약을 따로 물어보면 목록과 숫자가 다른 순간이
  // 생기고, 그때 사람은 어느 쪽을 믿어야 할지 모른다.
  const counts = countDue(
    [...issueRows.map((row) => row.due_date), ...taskRows.map((row) => row.due_date)],
    today,
  )

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-5">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">{t('home:title')}</h1>
        <p className="text-sm text-muted" data-testid="home-summary">
          {/*
            **늦은 것이 있으면 그것부터 말한다.** 목록 아래쪽에 묻혀 있으면
            첫 화면이 첫 화면 구실을 못 한다.
          */}
          {isCalm(counts)
            ? t('home:calm')
            : t('home:summary', { overdue: counts.overdue, today: counts.today })}
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel
          title={t('home:myIssues')}
          action={
            <Link to="/issues" search={{ iql: MY_ISSUES_IQL }} className={MORE}>
              {t('home:seeAll')}
            </Link>
          }
          error={issues.error}
          empty={issues.isSuccess && issueRows.length === 0 ? t('home:noIssues') : null}
          testId="home-issues"
        >
          {issueRows.slice(0, ROWS).map((row) => {
            const urgency = urgencyOf(row.due_date, today)
            return (
              <li key={row.id} className="flex flex-wrap items-baseline gap-2 text-sm">
                <Link
                  to="/issues/$issueKey"
                  params={{ issueKey: row.key }}
                  className="font-mono text-xs text-accent hover:underline"
                >
                  {row.key}
                </Link>
                <span className="min-w-0 flex-1 truncate">{row.summary}</span>
                <Badge tone={categoryTone(row.state_category)}>{row.state_name}</Badge>
                {row.due_date === null ? null : (
                  <Badge tone={TONE[urgency]}>{formatDate(row.due_date)}</Badge>
                )}
              </li>
            )
          })}
        </Panel>

        <Panel
          title={t('home:myTasks')}
          action={
            <Link to="/wiki/tasks" className={MORE}>
              {t('home:seeAll')}
            </Link>
          }
          error={tasks.error}
          empty={tasks.isSuccess && taskRows.length === 0 ? t('home:noTasks') : null}
          testId="home-tasks"
        >
          {taskRows.slice(0, ROWS).map((row) => {
            const urgency = urgencyOf(row.due_date, today)
            return (
              <li key={`${row.page_id}:${String(row.line)}`} className="flex flex-col gap-0.5">
                <span className="flex flex-wrap items-baseline gap-2 text-sm">
                  <RichText source={row.text} />
                  {row.due_date === null ? null : (
                    <Badge tone={TONE[urgency]}>{formatDate(row.due_date)}</Badge>
                  )}
                </span>
                <Link
                  to="/wiki/$spaceKey/$"
                  params={{ spaceKey: row.space_key, _splat: row.path }}
                  className="text-xs text-accent hover:underline"
                >
                  {row.space_key} · {row.page_title}
                </Link>
              </li>
            )
          })}
        </Panel>

        <Panel
          title={t('home:mySprints')}
          action={
            <Link to="/sprints" className={MORE}>
              {t('home:seeAll')}
            </Link>
          }
          error={sprints.error}
          empty={
            sprints.isSuccess && sprintRows.length === 0 ? t('home:noSprints') : null
          }
          testId="home-sprints"
        >
          {sprintRows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-baseline gap-2 text-sm">
              <span className="font-mono text-xs text-muted">{row.project_key}</span>
              <span className="min-w-0 flex-1 truncate">{row.name}</span>
              {/*
                남은 양을 함께 적는다. 이름만 있으면 "도는 중" 말고는 아무것도
                말해 주지 않는다.
              */}
              <Badge tone={row.remaining_issues > 0 ? 'in_progress' : 'done'}>
                {t('home:remaining', { remaining: row.remaining_issues, total: row.issues })}
              </Badge>
              {row.ends_at === null ? null : (
                <span className="text-xs text-muted">
                  {t('home:endsAt', { when: formatDate(row.ends_at) })}
                </span>
              )}
            </li>
          ))}
        </Panel>

        <Panel
          title={t('home:unread')}
          action={
            <Link to="/notifications" className={MORE}>
              {t('home:seeAll')}
            </Link>
          }
          error={news.error}
          empty={
            news.isSuccess && newsRows.length === 0 ? t('home:noNotifications') : null
          }
          testId="home-notifications"
        >
          {newsRows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-baseline gap-2 text-sm">
              <span className="min-w-0 flex-1 truncate">{row.title}</span>
              <span className="text-xs text-muted">{formatRelative(row.created_at)}</span>
            </li>
          ))}
        </Panel>

        {/* 기다리는 것이 있을 때만 자리를 차지한다 (위 `approvals` 주석). */}
        {approvalRows.length > 0 ? (
          <Panel
            title={t('desk:approval.mine')}
            action={null}
            error={approvals.error}
            empty={null}
            testId="home-approvals"
          >
            {approvalRows.slice(0, ROWS).map((row) => (
              <li key={row.id} className="flex flex-col gap-1.5 text-sm">
                <span className="flex flex-wrap items-baseline gap-2">
                  {/*
                    키는 링크로 둔다 — 상담원이 열어 보는 길이다. 승인자는
                    그 프로젝트의 이슈를 볼 권한이 없을 수 있으므로, **결정은
                    여기서 끝낼 수 있어야 한다.**
                  */}
                  <Link
                    to="/issues/$issueKey"
                    params={{ issueKey: row.issue_key }}
                    className="font-mono text-xs text-accent hover:underline"
                  >
                    {row.issue_key}
                  </Link>
                  <span className="min-w-0 flex-1 truncate">{row.summary}</span>
                  <Badge tone="todo">{t(`desk:approval.mode.${row.mode}`)}</Badge>
                </span>
                <ApprovalDecision
                  approvalId={row.id}
                  onDecided={() => approvals.refetch().then(() => undefined)}
                />
              </li>
            ))}
          </Panel>
        ) : null}
      </div>
    </section>
  )
}

/**
 * 칸 하나. 제목 · 전체로 가는 길 · 오류 · 비었을 때 할 말.
 *
 * 네 칸이 같은 모양이어야 하는 이유는 눈이다 — 칸마다 다른 자리에서 오류를
 * 말하면 사람은 무엇이 실패했는지 찾느라 화면을 훑는다.
 *
 * **가는 길을 노드로 받는다.** 경로를 문자열로 받으면 라우터의 타입 검사를
 * 빠져나가고(`search` 가 경로마다 다르다), 그러면 없는 질의 인자를 넘겨도
 * 빌드가 통과한다 — 그건 눌러 봐야 아는 종류의 고장이다.
 */
function Panel({
  title,
  action,
  error,
  empty,
  testId,
  children,
}: {
  title: string
  action: ReactNode
  error: unknown
  empty: string | null
  testId: string
  children: ReactNode
}) {
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{title}</h2>
        {action}
      </div>
      {error === null || error === undefined ? null : <Alert>{describeError(error)}</Alert>}
      {empty === null ? null : <p className="text-sm text-muted">{empty}</p>}
      <ul className="flex flex-col gap-2" data-testid={testId}>
        {children}
      </ul>
    </Card>
  )
}
