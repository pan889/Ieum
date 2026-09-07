import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from '@tanstack/react-router'

import { BoardScreen } from '@/features/boards/BoardScreen'
import { BoardsScreen } from '@/features/boards/BoardsScreen'
import { CustomerOrgsScreen } from '@/features/desk/CustomerOrgsScreen'
import { PortalsScreen } from '@/features/desk/PortalsScreen'
import { QueuesScreen } from '@/features/desk/QueuesScreen'
import { SlaScreen } from '@/features/desk/SlaScreen'
import { IssueDetailScreen } from '@/features/issues/IssueDetailScreen'
import { IssuesScreen } from '@/features/issues/IssuesScreen'
import { SpaceScreen } from '@/features/wiki/SpaceScreen'
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
import { SearchScreen } from '@/features/search/SearchScreen'

import { AppShell } from './AppShell'

const rootRoute = createRootRoute({ component: AppShell })

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    // TanStack Router 의 리다이렉트는 throw 기반 제어 흐름이다(라이브러리 규약).
    // eslint-disable-next-line @typescript-eslint/only-throw-error
    throw redirect({ to: '/projects' })
  },
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
 * SLA 는 **설정**이다 — 상담원이 매일 여는 자리가 아니라 관리자가 드물게
 * 고치는 자리다. 그래서 `/settings/` 아래에 둔다(큐는 작업 화면이라 최상위).
 */
const slaRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/sla',
  component: SlaScreen,
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
  slaRoute,
  searchRoute,
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
