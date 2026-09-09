import {
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'

import { BoardScreen } from '@/features/boards/BoardScreen'
import { BoardsScreen } from '@/features/boards/BoardsScreen'
import { CustomerOrgsScreen } from '@/features/desk/CustomerOrgsScreen'
import { PortalsScreen } from '@/features/desk/PortalsScreen'
import { QueuesScreen } from '@/features/desk/QueuesScreen'
import { AutomationScreen } from '@/features/desk/AutomationScreen'
import { EmailChannelsScreen } from '@/features/desk/EmailChannelsScreen'
import { SlaScreen } from '@/features/desk/SlaScreen'
import { ReportScreen as DeskReportScreen } from '@/features/desk/ReportScreen'
import { IssueDetailScreen } from '@/features/issues/IssueDetailScreen'
import { IssuesScreen } from '@/features/issues/IssuesScreen'
import { SpaceScreen } from '@/features/wiki/SpaceScreen'
import { MyTasksScreen } from '@/features/wiki/MyTasksScreen'
import { SpacesScreen } from '@/features/wiki/SpacesScreen'
import { parseSearch } from '@/features/issues/urlState'
import type { IssuesSearch } from '@/features/issues/urlState'
import { NewIssueScreen } from '@/features/issues/NewIssueScreen'
import { NotificationsScreen } from '@/features/notifications/NotificationsScreen'
import { ApiTokensScreen } from '@/features/settings/ApiTokensScreen'
import { AuditLogScreen } from '@/features/settings/AuditLogScreen'
import { SecurityScreen } from '@/features/settings/SecurityScreen'
import { PasskeysScreen } from '@/features/settings/PasskeysScreen'
import { GroupsScreen } from '@/features/settings/GroupsScreen'
import { FieldsScreen } from '@/features/settings/FieldsScreen'
import { RolesScreen } from '@/features/settings/RolesScreen'
import { SsoScreen } from '@/features/settings/SsoScreen'
import { UsersScreen } from '@/features/settings/UsersScreen'
import { WorkflowsScreen } from '@/features/settings/WorkflowsScreen'
import { SessionsScreen } from '@/features/settings/SessionsScreen'
import { ProjectsScreen } from '@/features/projects/ProjectsScreen'
import { HomeScreen } from '@/features/home/HomeScreen'
import { SearchScreen } from '@/features/search/SearchScreen'
import { RecurrencesScreen } from '@/features/recurrences/RecurrencesScreen'
import { SprintsScreen } from '@/features/sprints/SprintsScreen'
import { CalendarScreen } from '@/features/calendar/CalendarScreen'
import { GanttScreen } from '@/features/gantt/GanttScreen'
import { CountScreen } from '@/features/reports/CountScreen'

import { AppShell } from './AppShell'

const rootRoute = createRootRoute({ component: AppShell })

// 첫 화면. 전에는 `/projects` 로 넘겼는데, 프로젝트 목록은 "무엇이 있나" 를
// 말하고 "오늘 뭘 해야 하나" 를 말하지 않는다 (M5 대시보드).
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: HomeScreen,
})

const projectsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/projects',
  component: ProjectsScreen,
})

const issuesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/issues',
  component: IssuesScreen,
  // 필터를 URL 이 소유한다. 링크 하나로 같은 목록이 나와야 한다.
  validateSearch: (search: Record<string, unknown>): IssuesSearch => parseSearch(search),
})

// `/issues/new` 는 `/issues/$issueKey` 보다 **먼저** 등록해야 한다.
// 뒤에 두면 "new" 가 이슈 키로 잡혀 404 를 부른다.
const newIssueRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/issues/new',
  component: NewIssueScreen,
  // `?parent=ENG-1` 로 상위 이슈를 미리 채운다.
  validateSearch: (search: Record<string, unknown>): { parent?: string } =>
    typeof search['parent'] === 'string' ? { parent: search['parent'] } : {},
})

const issueDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/issues/$issueKey',
  component: IssueDetailScreen,
})

const boardsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/boards',
  component: BoardsScreen,
})

const boardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/boards/$boardId',
  component: BoardScreen,
})

const recurrencesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/recurrences',
  component: RecurrencesScreen,
})

const sprintsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/sprints',
  component: SprintsScreen,
})

const calendarRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/calendar',
  component: CalendarScreen,
})

const ganttRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/gantt',
  component: GanttScreen,
})

const reportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/reports',
  component: CountScreen,
})

const tokensRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/tokens',
  component: ApiTokensScreen,
})

const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/sessions',
  component: SessionsScreen,
})

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/audit',
  component: AuditLogScreen,
})

const securityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/security',
  component: SecurityScreen,
})

const passkeysRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/passkeys',
  component: PasskeysScreen,
})

const ssoRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/sso',
  component: SsoScreen,
})

const peopleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/people',
  component: UsersScreen,
})

const groupsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/groups',
  component: GroupsScreen,
})

const rolesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/roles',
  component: RolesScreen,
})

const workflowsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/workflows',
  component: WorkflowsScreen,
})

const fieldsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/fields',
  component: FieldsScreen,
})

const portalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/portals',
  component: PortalsScreen,
})

const customerOrgsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/customers',
  component: CustomerOrgsScreen,
})

/**
 * 큐는 **설정이 아니라 작업 화면**이다. 상담원이 하루 종일 여는 자리이므로
 * `/settings/` 아래가 아니라 최상위에 둔다 — 보드가 그런 것처럼.
 */
const deskRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/desk',
  component: QueuesScreen,
})

/**
 * 데스크 리포트 (C14). 설정이 아니라 **작업 화면**이라 `/desk` 아래다 —
 * 관리자가 정책을 고치는 자리가 아니라, 상담원과 팀장이 이번 주가 어땠는지
 * 보는 자리다.
 */
const deskReportRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/desk/reports',
  component: DeskReportScreen,
})

/**
 * SLA 는 **설정**이다 — 상담원이 매일 여는 자리가 아니라 관리자가 드물게
 * 고치는 자리다. 그래서 `/settings/` 아래에 둔다(큐는 작업 화면이라 최상위).
 */
const slaRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/sla',
  component: SlaScreen,
})

/**
 * 메일 채널 (C6). SLA 와 같은 이유로 `/settings/` 아래다 — 메일함
 * 비밀번호를 받고, 받는 주소를 바꾸면 그 뒤로 오는 고객의 메일이 다른
 * 프로젝트의 티켓이 된다.
 */
const emailChannelsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/email-channels',
  component: EmailChannelsScreen,
})

/**
 * 자동화 (C9). 메일 채널과 같은 이유로 `/settings/` 아래다 — 규칙 하나가
 * 그 뒤로 오는 모든 티켓에 걸리고, 고객에게 글을 보낸다.
 */
const automationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/automation',
  component: AutomationScreen,
})

/** 검색어를 URL 이 소유한다. 링크 하나로 같은 결과가 나와야 한다. */
export interface SearchParams {
  q: string
  /** 없으면 전체. `undefined` 를 넣어 종류 필터를 끌 수 있어야 한다. */
  kind?: 'issue' | 'page' | undefined
  offset: number
}

const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/search',
  component: SearchScreen,
  validateSearch: (search: Record<string, unknown>): SearchParams => {
    const kind = search['kind']
    const offset = Number(search['offset'])
    return {
      q: typeof search['q'] === 'string' ? search['q'] : '',
      ...(kind === 'issue' || kind === 'page' ? { kind } : {}),
      // 망가진 링크도 목록을 보여 준다. 숫자가 아니면 처음부터.
      offset: Number.isInteger(offset) && offset > 0 ? offset : 0,
    }
  },
})

/**
 * 내 할 일 (B12).
 *
 * **`/wiki/$spaceKey` 보다 위에 선언한다.** 아래에 두면 `tasks` 가 스페이스
 * 키로 읽혀 "그런 스페이스가 없다" 가 된다 (conventions.md "고정 경로는
 * 형제 전부보다 위에 둔다").
 */
const myTasksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/wiki/tasks',
  component: MyTasksScreen,
})

const wikiRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/wiki',
  component: SpacesScreen,
})

// 문서 경로는 깊이가 정해져 있지 않다(`/wiki/ENG/deploy/rollback`).
// splat 라우트로 나머지를 통째로 받는다.
const spaceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/wiki/$spaceKey',
  component: SpaceScreen,
})

const wikiPageRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/wiki/$spaceKey/$',
  component: SpaceScreen,
})

const notificationsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/notifications',
  component: NotificationsScreen,
})

const routeTree = rootRoute.addChildren([
  indexRoute,
  projectsRoute,
  issuesRoute,
  newIssueRoute,
  issueDetailRoute,
  boardsRoute,
  boardRoute,
  sprintsRoute,
  recurrencesRoute,
  calendarRoute,
  ganttRoute,
  reportsRoute,
  tokensRoute,
  sessionsRoute,
  auditRoute,
  securityRoute,
  passkeysRoute,
  ssoRoute,
  peopleRoute,
  groupsRoute,
  rolesRoute,
  workflowsRoute,
  fieldsRoute,
  portalsRoute,
  customerOrgsRoute,
  deskRoute,
  deskReportRoute,
  slaRoute,
  emailChannelsRoute,
  automationRoute,
  searchRoute,
  myTasksRoute,
  wikiRoute,
  spaceRoute,
  wikiPageRoute,
  notificationsRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
