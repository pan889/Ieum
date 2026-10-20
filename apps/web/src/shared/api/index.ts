import {
  ApiClient,
  createApiTokensApi,
  createApprovalsApi,
  createAssetsApi,
  createPluginsApi,
  createAttachmentsApi,
  createAuditApi,
  createAuthApi,
  createBoardsApi,
  createCalendarApi,
  createDeskApi,
  createFieldsApi,
  createGanttApi,
  createGroupsApi,
  createIdpApi,
  createImportsApi,
  createIssuesApi,
  createNotificationsApi,
  createProjectsApi,
  createRecurrencesApi,
  createReportsApi,
  createRepositoriesApi,
  createRolesApi,
  createSearchApi,
  createSecurityApi,
  createSprintsApi,
  createUsersApi,
  createWikiApi,
  createWorkflowsApi,
} from '@ieum/api-client'

import { useAuthStore } from '@/features/auth/store'

import { queryClient } from './queryClient'

export const apiClient = new ApiClient({
  // 개발은 vite 프록시로 same-origin, 배포는 같은 도메인에서 서빙한다.
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  /**
   * 세션을 잃었다. **로그아웃 단추와 같은 뒷정리를 한다**(`AppShell`).
   *
   * 단계만 내리고 캐시를 두면, 다음에 로그인한 사람이 앞사람의 목록을 잠깐
   * 보고, `me` 는 앞사람의 응답이 캐시에 남아 그 사람으로 앱이 선다 — 같은
   * 브라우저를 넘겨받은 사람에게 남의 이름과 데이터가 보이는 셈이다.
   *
   * **잃을 세션이 있었을 때만** 버린다. 로그인하지 않은 채로 여는 첫 화면도
   * `me` 가 401 을 받아 여기로 온다. 그때 캐시를 통째로 버리면 함께 날아가던
   * 조회(고객 포털의 게스트 화면)가 소리 없이 취소되고, 버릴 남의 데이터는
   * 애초에 없다.
   */
  onSessionLost: () => {
    const { stage, reset } = useAuthStore.getState()
    reset()
    if (stage !== 'unknown' && stage !== 'anonymous') queryClient.clear()
  },
})

export const authApi = createAuthApi(apiClient)
export const projectsApi = createProjectsApi(apiClient)
export const issuesApi = createIssuesApi(apiClient)
export const searchApi = createSearchApi(apiClient)
export const boardsApi = createBoardsApi(apiClient)
export const usersApi = createUsersApi(apiClient)
export const attachmentsApi = createAttachmentsApi(apiClient)
export const wikiApi = createWikiApi(apiClient)
export const notificationsApi = createNotificationsApi(apiClient)
export const apiTokensApi = createApiTokensApi(apiClient)
export const rolesApi = createRolesApi(apiClient)
export const auditApi = createAuditApi(apiClient)
export const securityApi = createSecurityApi(apiClient)
export const idpApi = createIdpApi(apiClient)
export const groupsApi = createGroupsApi(apiClient)
export const workflowsApi = createWorkflowsApi(apiClient)
export const fieldsApi = createFieldsApi(apiClient)
export const deskApi = createDeskApi(apiClient)
export const sprintsApi = createSprintsApi(apiClient)
export const calendarApi = createCalendarApi(apiClient)
export const ganttApi = createGanttApi(apiClient)
export const reportsApi = createReportsApi(apiClient)
export const recurrencesApi = createRecurrencesApi(apiClient)
export const repositoriesApi = createRepositoriesApi(apiClient)
export const approvalsApi = createApprovalsApi(apiClient)
export const assetsApi = createAssetsApi(apiClient)
export const pluginsApi = createPluginsApi(apiClient)
export const importsApi = createImportsApi(apiClient)
