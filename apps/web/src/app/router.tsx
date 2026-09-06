import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from '@tanstack/react-router'

import { PlaceholderScreen } from '@/features/projects/PlaceholderScreen'
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
  component: () => <PlaceholderScreen titleKey="common:nav.issues" />,
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
  wikiRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
