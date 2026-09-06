import type { ApiClient } from './client'

export interface Attachment {
  id: string
  owner_type: string
  owner_id: string
  filename: string
  mime: string
  size: number
  uploaded_by: string | null
  created_at: string | null
}

export interface UploadTicket {
  attachment_id: string
  upload_url: string
  /** PUT 에 그대로 실어야 한다. 서명에 들어 있어 다르면 스토리지가 거절한다. */
  headers: Record<string, string>
  expires_in: number
}

const BASE = '/api/v1/attachments'

export function createAttachmentsApi(client: ApiClient) {
  return {
    list: (ownerType: string, ownerId: string) =>
      client.get<Attachment[]>(`${BASE}?owner_type=${ownerType}&owner_id=${ownerId}`),

    beginUpload: (body: {
      owner_type: string
      owner_id: string
      filename: string
      mime: string
      size: number
    }) => client.post<UploadTicket>(`${BASE}/upload-url`, body),

    complete: (attachmentId: string) =>
      client.post<Attachment>(`${BASE}/${attachmentId}/complete`),

    remove: (attachmentId: string) => client.delete<void>(`${BASE}/${attachmentId}`),

    /**
     * 다운로드용 presigned URL. **클릭 시점에** 부른다.
     *
     * 액세스 토큰은 메모리에만 있고 쿠키가 아니라서 `<a href>` 클릭에는
     * Authorization 헤더가 안 붙는다. 그래서 링크에 서버 경로를 박아 두지
     * 않고, 인증된 요청으로 URL 을 받아 그때 연다. URL 은 곧 만료된다.
     */
    downloadUrl: (attachmentId: string) =>
      client.get<{ url: string; expires_in: number }>(`${BASE}/${attachmentId}/download-url`),

    /**
     * 브라우저에서 스토리지로 직접 올린다. **서버를 경유하지 않는다** —
     * 경유하면 큰 파일 하나가 API 워커를 붙잡는다.
     */
    async upload(
      ownerType: string,
      ownerId: string,
      file: File,
      onProgress?: (fraction: number) => void,
    ): Promise<Attachment> {
      const ticket = await this.beginUpload({
        owner_type: ownerType,
        owner_id: ownerId,
        filename: file.name,
        mime: file.type || 'application/octet-stream',
        size: file.size,
      })
      await putWithProgress(ticket.upload_url, file, ticket.headers, onProgress)
      return this.complete(ticket.attachment_id)
    },
  }
}

/**
 * fetch 는 업로드 진행률을 못 준다. 파일 업로드는 몇십 초가 걸릴 수 있고
 * 아무 표시가 없으면 사용자가 멈춘 줄 안다 — 그래서 XHR 을 쓴다.
 */
function putWithProgress(
  url: string,
  file: File,
  headers: Record<string, string>,
  onProgress?: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value)
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total)
    })
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new Error(`upload failed: ${String(xhr.status)}`))
    })
    xhr.addEventListener('error', () => { reject(new Error('upload failed')); })
    xhr.addEventListener('abort', () => { reject(new Error('upload aborted')); })
    xhr.send(file)
  })
}

export type AttachmentsApi = ReturnType<typeof createAttachmentsApi>
