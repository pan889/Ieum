/**
 * 코드 저장소 연동 (A22, M6).
 *
 * **이 화면의 일은 두 가지다.**
 *
 * 1. **시크릿과 주소를 한 번 건네준다.** 등록 응답에만 있으므로, 이 카드를
 *    닫으면 다시 못 읽는다 — 그렇게 적어 둔다. 주소는 서버가 준 경로에
 *    지금 origin 을 붙여 **완성된 주소로** 보여 준다. 경로만 주면 사람이
 *    도메인을 손으로 붙이고, 그때 `/api` 를 빠뜨린다.
 * 2. **"켰는데 안 오는 것" 을 보이게 한다.** 이 연동의 흔한 고장은 조용하다:
 *    코드 호스트 쪽 설정이 틀렸거나 시크릿을 잘못 붙여 넣었는데, 우리 화면은
 *    아무 말도 하지 않는다. 그래서 마지막으로 받은 시각을 목록에 적고, 한
 *    번도 못 받았으면 그렇게 말한다.
 *
 * 목록은 **꺼진 것도 보여 준다.** 숨기면 사람은 등록이 지워진 줄 알고 다시
 * 등록한다 (같은 이름은 거절되므로 그때서야 알게 된다).
 *
 * 프로젝트를 여러 개 붙일 수 있다 — 모노레포 하나가 팀 둘의 코드를 담는다.
 * **등록하는 사람은 그 프로젝트 전부에 권한이 있어야 한다**(서버가 본다).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { IssuedRepository, Project, Repository, VcsProvider } from '@ieum/api-client'

import { formatDateTime } from '@/features/issues/format'
import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { repositoriesApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Chip, Field, Select } from '@/shared/ui/primitives'

const PROVIDERS: VcsProvider[] = ['github', 'gitlab']

/** 서버가 준 경로에 지금 origin 을 붙인다. 셀프호스팅은 같은 도메인이다. */
function webhookUrl(issued: IssuedRepository): string {
  return `${window.location.origin}${issued.webhook_path}`
}

export function RepositoriesScreen() {
  const { t } = useTranslation(['vcs', 'common'])
  const queryClient = useQueryClient()

  const [project, setProject] = useState<Project | null>(null)
  const projectId = project?.id ?? null
  const [provider, setProvider] = useState<VcsProvider>('github')
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  /** 이 저장소가 함께 가리킬 다른 프로젝트들. 고른 프로젝트는 늘 포함된다. */
  const [extras, setExtras] = useState<Project[]>([])
  const [issued, setIssued] = useState<IssuedRepository | null>(null)
  const [copied, setCopied] = useState<'secret' | 'url' | null>(null)

  const rows = useQuery({
    queryKey: ['vcs', 'repositories', projectId],
    queryFn: () => repositoriesApi.list(projectId as string),
    enabled: projectId !== null,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['vcs'] })

  const create = useMutation({
    mutationFn: () =>
      repositoriesApi.create({
        provider,
        name: name.trim(),
        project_ids: [projectId as string, ...extras.map((row) => row.id)],
        url: url.trim() === '' ? null : url.trim(),
      }),
    onSuccess: async (made) => {
      setIssued(made)
      setName('')
      setUrl('')
      setExtras([])
      await refresh()
    },
  })

  const toggle = useMutation({
    mutationFn: (row: Repository) => repositoriesApi.setEnabled(row.id, !row.is_enabled),
    onSuccess: refresh,
  })

  const remove = useMutation({
    mutationFn: (row: Repository) => repositoriesApi.remove(row.id),
    onSuccess: refresh,
  })

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">{t('vcs:title')}</h1>
        <p className="text-sm text-muted">{t('vcs:description')}</p>
      </header>

      {/*
        **한 번만 보여 준다.** 닫으면 다시 못 읽는다 — 그렇게 적어 두지 않으면
        사람은 닫고 나서 목록에서 찾는다.
      */}
      {issued ? (
        <Card className="flex flex-col gap-3 border-accent" data-testid="vcs-issued">
          <p className="text-sm font-medium">{t('vcs:issued.title')}</p>
          <p className="text-xs text-muted">{t('vcs:issued.hint')}</p>
          <Secret
            label={t('vcs:issued.url')}
            copyLabel={t('vcs:issued.copyUrl')}
            testId="vcs-webhook-url"
            value={webhookUrl(issued)}
            copied={copied === 'url'}
            onCopy={() => { setCopied('url') }}
          />
          <Secret
            label={t('vcs:issued.secret')}
            copyLabel={t('vcs:issued.copySecret')}
            testId="vcs-secret"
            value={issued.secret}
            copied={copied === 'secret'}
            onCopy={() => { setCopied('secret') }}
          />
          <p className="text-xs text-muted">{t(`vcs:issued.where.${issued.provider}`)}</p>
          <div>
            <Button variant="ghost" onClick={() => { setIssued(null); setCopied(null) }}>
              {t('common:action.close')}
            </Button>
          </div>
        </Card>
      ) : null}

      <ProjectPicker label={t('vcs:project')} chosen={project} onPick={setProject} />

      {/*
        `project` 로 가른다. `projectId` 로 가르면 타입 좁히기가 안 되고
        (파생값이다), 아래에서 `project.key` 를 쓸 때 `null` 가능성이 남는다.
      */}
      {project === null ? (
        <p className="text-sm text-muted">{t('vcs:pickProject')}</p>
      ) : (
        <>
          <Card>
            <form
              className="flex flex-col gap-4"
              onSubmit={(event) => { event.preventDefault(); create.mutate() }}
            >
              <h2 className="text-sm font-medium">{t('vcs:newTitle')}</h2>
              {create.isError ? <Alert>{describeError(create.error)}</Alert> : null}

              <Select
                label={t('vcs:provider')}
                value={provider}
                onChange={(e) => { setProvider(e.target.value as VcsProvider) }}
              >
                {PROVIDERS.map((value) => (
                  <option key={value} value={value}>{t(`vcs:provider.${value}`)}</option>
                ))}
              </Select>

              <Field
                label={t('vcs:name')}
                hint={t('vcs:nameHint')}
                required
                value={name}
                onChange={(e) => { setName(e.target.value) }}
              />
              <Field
                label={t('vcs:url')}
                hint={t('vcs:urlHint')}
                value={url}
                onChange={(e) => { setUrl(e.target.value) }}
              />

              {/*
                모노레포. **고른 프로젝트는 늘 들어간다** — 여기서 더하는
                것은 그 밖의 프로젝트다. 서버는 적힌 프로젝트 전부에 권한을
                요구하므로, 권한 없는 것을 더하면 등록 자체가 거절된다.
              */}
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium">{t('vcs:alsoProjects')}</span>
                <p className="text-xs text-muted">{t('vcs:alsoProjectsHint')}</p>
                <div className="flex flex-wrap gap-1.5">
                  <Chip pressed disabled>{project.key}</Chip>
                  {extras.map((row) => (
                    <Chip
                      key={row.id}
                      pressed
                      aria-label={t('vcs:removeProject', { key: row.key })}
                      onClick={() => {
                        setExtras((current) => current.filter((item) => item.id !== row.id))
                      }}
                    >
                      {row.key} ×
                    </Chip>
                  ))}
                </div>
                <ProjectPicker
                  label={t('vcs:addProject')}
                  chosen={null}
                  emptyLabel={t('vcs:addProjectNone')}
                  onPick={(picked) => {
                    setExtras((current) =>
                      picked.id === projectId || current.some((item) => item.id === picked.id)
                        ? current
                        : [...current, picked],
                    )
                  }}
                />
              </div>

              <Button type="submit" loading={create.isPending} disabled={name.trim() === ''}>
                {t('vcs:submit')}
              </Button>
            </form>
          </Card>

          {rows.isError ? <Alert>{describeError(rows.error)}</Alert> : null}
          {remove.isError ? <Alert>{describeError(remove.error)}</Alert> : null}
          {toggle.isError ? <Alert>{describeError(toggle.error)}</Alert> : null}
          {rows.isSuccess && rows.data.length === 0 ? (
            <p className="text-sm text-muted">{t('vcs:empty')}</p>
          ) : null}

          <ul className="flex flex-col gap-2" data-testid="repositories">
            {(rows.data ?? []).map((row) => (
              <li key={row.id}>
                <Card className="flex flex-col gap-2">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-sm font-medium">{row.name}</span>
                    <Badge tone="info">{t(`vcs:provider.${row.provider}`)}</Badge>
                    {row.is_enabled ? (
                      <Badge tone="in_progress">{t('vcs:on')}</Badge>
                    ) : (
                      <Badge tone="neutral">{t('vcs:off')}</Badge>
                    )}
                  </div>
                  {/*
                    **이 줄이 진단이다.** 켜 두었는데 비어 있으면 코드 호스트
                    쪽 배선이 아직 안 된 것이다.
                  */}
                  <p className="text-xs text-muted">
                    {row.last_event_at === null
                      ? t('vcs:neverHeard')
                      : t('vcs:lastEvent', { when: formatDateTime(row.last_event_at) })}
                    {row.project_ids.length > 1
                      ? ` · ${t('vcs:sharedWith', { count: row.project_ids.length - 1 })}`
                      : ''}
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="ghost"
                      className="text-xs"
                      loading={toggle.isPending && toggle.variables.id === row.id}
                      onClick={() => { toggle.mutate(row) }}
                    >
                      {row.is_enabled ? t('vcs:turnOff') : t('vcs:turnOn')}
                    </Button>
                    <Button
                      variant="ghost"
                      className="text-xs"
                      loading={remove.isPending && remove.variables.id === row.id}
                      onClick={() => { remove.mutate(row) }}
                    >
                      {t('common:action.delete')}
                    </Button>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

/**
 * 한 번만 보이는 값 하나.
 *
 * **`<label>` 로 감싸지 않는다.** 감싸면 그 안의 버튼이 라벨의 대상이 되어
 * 복사 버튼의 이름이 "Payload URL" 이 된다 — 값이 아니라 버튼이 그 이름을
 * 갖는 것이고, 실제로 E2E 가 그 버튼을 값으로 집어 왔다. 값은 폼 컨트롤이
 * 아니므로 라벨을 붙일 자리가 아니다.
 *
 * 대신 **복사 버튼에 자기 이름을 준다.** 카드 안에 "복사" 가 둘이라
 * 보이는 글자만으로는 무엇을 복사하는지 소리로 구별되지 않는다.
 */
function Secret({
  label,
  copyLabel,
  testId,
  value,
  copied,
  onCopy,
}: {
  label: string
  copyLabel: string
  testId: string
  value: string
  copied: boolean
  onCopy: () => void
}) {
  const { t } = useTranslation(['common'])
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted">{label}</span>
      <div className="flex items-center gap-2">
        <code
          data-testid={testId}
          className="flex-1 overflow-x-auto rounded bg-surface-raised px-2 py-1.5 font-mono text-xs"
        >
          {value}
        </code>
        <Button
          variant="secondary"
          aria-label={copyLabel}
          onClick={() => { void navigator.clipboard.writeText(value).then(onCopy) }}
        >
          {copied ? t('common:action.copied') : t('common:action.copy')}
        </Button>
      </div>
    </div>
  )
}
