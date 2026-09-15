import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import { attachmentsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Button, Card } from '@/shared/ui/primitives'

/** 1024 단위. 사람이 파일 크기를 읽는 방식이다. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${String(bytes)} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit] as string}`
}

export function Attachments({
  ownerType,
  ownerId,
}: {
  ownerType: string
  ownerId: string
}) {
  const { t } = useTranslation(['issues', 'common'])
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState<{ name: string; fraction: number } | null>(null)

  const files = useQuery({
    queryKey: ['attachments', ownerType, ownerId],
    queryFn: () => attachmentsApi.list(ownerType, ownerId),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['attachments', ownerType, ownerId] })
  }

  const upload = useMutation({
    mutationFn: async (selected: File[]) => {
      // 하나씩 올린다. 동시에 올리면 진행률이 뒤섞이고, 브라우저 연결 수
      // 제한에 걸려 오히려 느려진다.
      for (const file of selected) {
        setProgress({ name: file.name, fraction: 0 })
        await attachmentsApi.upload(ownerType, ownerId, file, (fraction) => {
          setProgress({ name: file.name, fraction })
        })
      }
    },
    onSettled: () => {
      setProgress(null)
      refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: string) => attachmentsApi.remove(id),
    onSuccess: refresh,
  })

  /**
   * 다운로드는 두 걸음이다: 인증된 요청으로 URL 을 받고 → 그 URL 을 연다.
   *
   * 탭은 **클릭 핸들러 안에서 미리** 연다. 응답을 기다렸다가 열면 사용자
   * 제스처 밖이라 팝업 차단기가 조용히 막는다 — 사용자는 눌렀는데 아무 일도
   * 안 일어난 것처럼 보인다.
   */
  const download = useMutation({
    mutationFn: async (id: string) => {
      const opened = window.open('', '_blank', 'noopener,noreferrer')
      try {
        const result = await attachmentsApi.downloadUrl(id)
        if (opened) opened.location.href = result.url
        // 차단됐으면 같은 탭에서 연다. presigned 응답은 Content-Disposition
        // 이 붙어 있어 대부분 내려받기로 처리된다.
        else window.location.href = result.url
      } catch (error) {
        opened?.close()
        throw error
      }
    },
  })

  const pick = (list: FileList | null) => {
    const selected = [...(list ?? [])]
    if (selected.length > 0) upload.mutate(selected)
  }

  return (
    <Card
      className={clsx('flex flex-col gap-3', dragging && 'border-accent')}
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => { setDragging(false); }}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        pick(event.dataTransfer.files)
      }}
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-fg">{t('issues:attachments.title')}</h2>
        <Button
          variant="ghost"
          className="text-xs"
          loading={upload.isPending}
          onClick={() => input.current?.click()}
        >
          {t('issues:attachments.add')}
        </Button>
      </div>

      <input
        ref={input}
        type="file"
        multiple
        className="hidden"
        aria-label={t('issues:attachments.add')}
        onChange={(event) => {
          pick(event.target.files)
          // 같은 파일을 다시 고를 수 있게 비운다. 안 비우면 change 가 안 온다.
          event.target.value = ''
        }}
      />

      {upload.isError ? <Alert>{describeError(upload.error)}</Alert> : null}
      {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
      {download.isError ? <Alert>{describeError(download.error)}</Alert> : null}

      {progress ? (
        <div className="flex flex-col gap-1">
          <span className="text-xs text-muted">
            {t('issues:attachments.uploading', { name: progress.name })}
          </span>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress.fraction * 100)}
            className="h-1.5 w-full overflow-hidden rounded-full bg-surface-raised"
          >
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${String(Math.round(progress.fraction * 100))}%` }}
            />
          </div>
        </div>
      ) : null}

      {files.isPending ? null : files.isError ? (
        <Alert>{describeError(files.error)}</Alert>
      ) : files.data.length === 0 ? (
        <p className="text-sm text-muted">
          {dragging ? t('issues:attachments.drop') : t('issues:attachments.empty')}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5 text-sm">
          {files.data.map((file) => (
            <li key={file.id} className="flex items-baseline gap-2">
              <button
                type="button"
                className="text-left text-accent hover:underline"
                onClick={() => { download.mutate(file.id); }}
              >
                {file.filename}
              </button>
              <span className="text-xs text-muted">{formatBytes(file.size)}</span>
              <Button
                variant="ghost"
                className="ml-auto px-1 py-0 text-xs"
                aria-label={t('issues:attachments.delete')}
                loading={remove.isPending && remove.variables === file.id}
                onClick={() => { remove.mutate(file.id); }}
              >
                ×
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
