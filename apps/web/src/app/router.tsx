import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from '@tanstack/react-router'

import { BoardScreen } from '@/features/boards/BoardScreen'
import { BoardsScreen } from '@/features/boards/BoardsScreen'
import { IssueDetailScreen } from '@/features/issues/IssueDetailScreen'
import { IssuesScreen } from '@/features/issues/IssuesScreen'
import { parseSearch } from '@/features/issues/urlState'
import type { IssuesSearch } from '@/features/issues/urlState'
import { NewIssueScreen } from '@/features/issues/NewIssueScreen'
import { PlaceholderScreen } from '@/features/projects/PlaceholderScreen'
import { ApiTokensScreen } from '@/features/settings/ApiTokensScreen'
import { ProjectsScreen } from '@/features/projects/ProjectsScreen'

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

const wikiRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/wiki',
  component: () => <PlaceholderScreen titleKey="common:nav.wiki" />,
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
  wikiRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
