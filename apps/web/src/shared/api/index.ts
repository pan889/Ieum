import {
  ApiClient,
  createApiTokensApi,
  createAttachmentsApi,
  createAuditApi,
  createAuthApi,
  createBoardsApi,
  createGroupsApi,
  createIdpApi,
  createIssuesApi,
  createNotificationsApi,
  createProjectsApi,
  createRolesApi,
  createSearchApi,
  createSecurityApi,
  createUsersApi,
  createWikiApi,
} from '@ieum/api-client'

import { useAuthStore } from '@/features/auth/store'

export const apiClient = new ApiClient({
  // 개발은 vite 프록시로 same-origin, 배포는 같은 도메인에서 서빙한다.
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  onSessionLost: () => {
    useAuthStore.getState().reset()
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
