/**
 * 다른 도구에서 옮겨 오기 (M6 "임포터").
 *
 * **화면이 두 단계인 이유.** 이관은 되돌리기 번거로운 일이다. 되돌리려면
 * 방금 만든 이슈만 골라 지워야 하는데, 그 사이에 사람이 코멘트를 달면 무엇이
 * 이관분인지 화면에서 구별되지 않는다. 그래서 **누르기 전에** 무엇이 들어오고
 * 무엇이 안 들어오는지 보여 준다.
 *
 * **이 화면이 반드시 구별해 그리는 것 셋.**
 *
 * 1. **이름이 맞은 짝과 짐작한 짝.** 우선순위는 이름으로 못 잇는다(우리는
 *    숫자, 소스는 이름) — 순서로 편 것이다. 그것을 "이었다" 와 같게 그리면
 *    사람이 확인해야 할 자리를 지나친다.
 * 2. **사람을 못 이은 이유 둘.** 소스에 메일이 없는 것과 우리 쪽에 계정이
 *    없는 것은 **고치는 방법이 다르다** — 앞은 소스를 고쳐 다시 뽑아야 하고
 *    뒤는 여기서 계정을 만들면 된다.
 * 3. **못 옮긴 것.** 적재 결과에서 이 목록을 접어 두지 않는다. 들어온 개수는
 *    나중에도 셀 수 있지만, 안 들어온 것은 여기서 안 보면 아무 데도 안 남는다.
 *
 * 짝을 바꾸면 **미리 보기를 다시 부른다.** 화면이 혼자 계산해서 보여 주면 그
 * 계산과 서버의 계산이 갈리는 날이 오고, 그날 사람은 화면을 믿고 적재를
 * 누른다. 그래서 판정은 늘 서버가 한다.
 */

import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type {
  ImportChoice,
  ImportLoaded,
  ImportMatch,
  ImportOverrides,
  ImportPerson,
  ImportPreview,
  Project,
} from '@ieum/api-client'

import { ProjectPicker } from '@/features/projects/ProjectPicker'
import { SettingsNav } from '@/features/settings/SettingsNav'
import { importsApi } from '@/shared/api'
import { describeError } from '@/shared/api/errors'
import { Alert, Badge, Button, Card, Select } from '@/shared/ui/primitives'

/** 우선순위는 1~5 고정이다. 서버가 후보를 안 주므로 여기서 만든다. */
const PRIORITIES: ImportChoice[] = [1, 2, 3, 4, 5].map((n) => ({
  id: String(n),
  name: String(n),
  qualifier: '',
}))

export type Kind = 'types' | 'statuses' | 'priorities'

/**
 * 짝 하나를 바꾼 뒤의 짝 목록.
 *
 * 두 가지를 **지운다**. 빈 칸을 고른 낱말은 목록에서 빼고(빈 문자열로 두지
 * 않는다), 그래서 빈 표가 된 갈래도 통째로 뺀다.
 *
 * 빈 문자열을 남기면 안 되는 이유: 서버는 없는 id 를 가리키는 짝을 무시한다.
 * 그러면 사람이 "안 고름" 으로 되돌린 것과 오타를 낸 것이 **같은 요청**이
 * 되고, 미리 보기는 둘을 구별해 말할 수 없다.
 */
export function nextOverrides(
  overrides: ImportOverrides,
  kind: Kind,
  source: string,
  targetId: string,
): ImportOverrides {
  const table = Object.fromEntries(
    Object.entries(overrides[kind] ?? {}).filter(([key]) => key !== source),
  )
  if (targetId !== '') table[source] = targetId

  return Object.fromEntries(
    Object.entries({ ...overrides, [kind]: table }).filter(
      ([, value]) => Object.keys(value).length > 0,
    ),
  )
}

/**
 * 짝 상자에 지금 보일 값. **사람이 고른 것이 서버가 준 것을 이긴다.**
 *
 * 고른 직후 다시 그릴 때 새 미리 보기는 아직 안 왔다. 서버 것만 보면 그 사이
 * 상자가 옛 값으로 되돌아가고, 사람은 자기가 고른 것이 안 먹었다고 읽는다.
 *
 * 못 이은 줄에 `how === 'unmatched'` 를 따로 보지 않는다 — 그때 서버가 보내는
 * `target_id` 는 **늘 빈 문자열**이다(`mapping.Match(source)` 의 기본값).
 * 여기에 가드를 두면 서버가 만들 수 없는 상태를 막는 죽은 줄이 되고, 그것을
 * 지키는 시험은 무엇을 되돌려도 초록이다 — 실제로 그렇게 써 보고 지웠다.
 */
export function selectedValue(row: ImportMatch, overrides: Record<string, string> | undefined) {
  return overrides?.[row.source] ?? row.target_id
}

export function ImportsScreen() {
  const { t } = useTranslation(['imports', 'common'])

  const [project, setProject] = useState<Project | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [overrides, setOverrides] = useState<ImportOverrides>({})
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [loaded, setLoaded] = useState<ImportLoaded | null>(null)

  const run = useMutation({
    mutationFn: (chosen: ImportOverrides) =>
      importsApi.preview(project?.id as string, file as File, chosen),
    // **여기서 적재 결과를 지우지 않는다.** 적재가 끝나면 스스로 미리 보기를
    // 다시 받는데(아래), 그 응답이 방금 보여 준 결과를 지워 버린다. 처음에
    // 그렇게 썼고 브라우저 시험이 그것을 잡았다 — 화면에는 결과 카드가
    // 잠깐 떴다가 사라졌고, 서버는 200 이었다.
    //
    // 지난 결과를 치우는 것은 **사람이 다시 시작할 때**다: 미리 보기를 새로
    // 누르거나, 짝을 바꾸거나, 프로젝트·파일을 바꿀 때.
    onSuccess: setPreview,
  })

  const load = useMutation({
    mutationFn: () => importsApi.load(project?.id as string, file as File, overrides),
    onSuccess: (result) => {
      setLoaded(result)
      // 실었으니 "이미 옮긴 것" 이 늘었다. 미리 보기를 다시 받아 그 수를
      // 맞춘다 — 안 그러면 두 번째로 누를 사람이 옛 숫자를 본다.
      run.mutate(overrides)
    },
  })

  /** 짝을 바꾸면 곧바로 서버에 다시 물어본다. 판정은 화면이 하지 않는다. */
  const choose = (kind: Kind, source: string, targetId: string) => {
    const next = nextOverrides(overrides, kind, source, targetId)
    setOverrides(next)
    setLoaded(null)
    run.mutate(next)
  }

  const ready = project !== null && file !== null

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <SettingsNav />
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">{t('imports:title')}</h1>
        <p className="text-sm text-muted">{t('imports:description')}</p>
      </header>

      <Card className="flex flex-col gap-4">
        <ProjectPicker
          label={t('imports:pick.project')}
          chosen={project}
          onPick={(picked) => {
            setProject(picked)
            // 프로젝트가 바뀌면 어휘가 통째로 다르다. 지난 짝을 들고 가면
            // 없는 id 를 가리키게 된다.
            setOverrides({})
            setPreview(null)
            setLoaded(null)
          }}
        />
        <p className="text-xs text-muted">{t('imports:pick.projectHint')}</p>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">{t('imports:pick.file')}</span>
          <input
            type="file"
            accept=".zip,application/zip"
            data-testid="import-file"
            className="text-sm"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setOverrides({})
              setPreview(null)
              setLoaded(null)
            }}
          />
          <span className="text-xs text-muted">{t('imports:pick.fileHint')}</span>
        </label>

        <div>
          <Button
            data-testid="import-preview"
            disabled={!ready}
            loading={run.isPending}
            onClick={() => {
              setLoaded(null)
              run.mutate(overrides)
            }}
          >
            {preview ? t('imports:pick.again') : t('imports:pick.preview')}
          </Button>
        </div>
        {run.isError ? <Alert>{describeError(run.error)}</Alert> : null}
      </Card>

      {preview ? (
        <Preview
          preview={preview}
          overrides={overrides}
          busy={run.isPending || load.isPending}
          onChoose={choose}
          onLoad={() => {
            load.mutate()
          }}
        />
      ) : null}
      {load.isError ? <Alert>{describeError(load.error)}</Alert> : null}
      {loaded ? <Loaded loaded={loaded} /> : null}
    </section>
  )
}

function Preview({
  preview,
  overrides,
  busy,
  onChoose,
  onLoad,
}: {
  preview: ImportPreview
  overrides: ImportOverrides
  busy: boolean
  onChoose: (kind: Kind, source: string, targetId: string) => void
  onLoad: () => void
}) {
  const { t } = useTranslation(['imports'])

  return (
    <>
      <Card className="flex flex-col gap-3" data-testid="import-source">
        <p className="text-sm font-medium">{t('imports:source.title')}</p>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <Row label={t('imports:source.kind')} value={preview.source_kind} />
          <Row label={t('imports:source.url')} value={preview.source_url} />
          <Row
            label={t('imports:source.project')}
            value={`${preview.project_key} · ${preview.project_name}`}
          />
          <Row label={t('imports:source.takenAt')} value={preview.taken_at} />
          <Row label={t('imports:source.adapter')} value={preview.adapter} />
        </dl>
        <dl className="grid grid-cols-4 gap-2 text-sm" data-testid="import-counted">
          <Count label={t('imports:counted.issues')} value={preview.counted.issues} />
          <Count label={t('imports:counted.comments')} value={preview.counted.comments} />
          <Count label={t('imports:counted.people')} value={preview.counted.people} />
          <Count
            label={t('imports:counted.alreadyHere')}
            value={preview.counted.already_here}
            testId="import-already-here"
          />
        </dl>
      </Card>

      {preview.can_load ? null : (
        <div data-testid="import-blocking">
          <Alert>
            <p className="font-medium">{t('imports:blocking.title')}</p>
            <p className="text-xs">{t('imports:blocking.hint')}</p>
            <ul className="mt-1 list-disc pl-5 text-xs">
              {preview.blocking.map((row) => (
                <li key={row}>{row}</li>
              ))}
            </ul>
          </Alert>
        </div>
      )}

      <Matches
        title={t('imports:match.types')}
        testId="import-types"
        kind="types"
        rows={preview.types}
        choices={preview.type_choices}
        overrides={overrides.types}
        onChoose={onChoose}
      />
      <Matches
        title={t('imports:match.statuses')}
        testId="import-statuses"
        kind="statuses"
        rows={preview.statuses}
        choices={preview.status_choices}
        overrides={overrides.statuses}
        onChoose={onChoose}
      />
      <Matches
        title={t('imports:match.priorities')}
        note={t('imports:match.priorityNote')}
        testId="import-priorities"
        kind="priorities"
        rows={preview.priorities}
        choices={PRIORITIES}
        overrides={overrides.priorities}
        onChoose={onChoose}
      />

      <People rows={preview.people} />
      <Dangling preview={preview} />

      <div className="flex flex-col gap-1">
        <Button
          data-testid="import-load"
          disabled={!preview.can_load || busy}
          onClick={onLoad}
        >
          {t('imports:load.run')}
        </Button>
        <p className="text-xs text-muted">{t('imports:load.confirm')}</p>
      </div>
    </>
  )
}

function Matches({
  title,
  note,
  testId,
  kind,
  rows,
  choices,
  overrides,
  onChoose,
}: {
  title: string
  note?: string
  testId: string
  kind: Kind
  rows: ImportMatch[]
  choices: ImportChoice[]
  overrides: Record<string, string> | undefined
  onChoose: (kind: Kind, source: string, targetId: string) => void
}) {
  const { t } = useTranslation(['imports'])
  if (rows.length === 0) return null

  return (
    <Card className="flex flex-col gap-3" data-testid={testId}>
      <p className="text-sm font-medium">{title}</p>
      {note ? <p className="text-xs text-muted">{note}</p> : null}
      <ul className="flex flex-col gap-2">
        {rows.map((row) => (
          <li key={row.source} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="min-w-32 font-mono text-xs">{row.source}</span>
            <Select
              aria-label={`${title} · ${row.source}`}
              value={selectedValue(row, overrides)}
              onChange={(event) => {
                onChoose(kind, row.source, event.target.value)
              }}
            >
              <option value="">{t('imports:match.unmatched')}</option>
              {choices.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choice.qualifier ? `${choice.name} (${choice.qualifier})` : choice.name}
                </option>
              ))}
            </Select>
            {/*
              **짐작한 것을 이름이 맞은 것과 같게 그리지 않는다.** 이 뱃지가
              이 화면에서 가장 중요한 한 글자다.
            */}
            <Badge tone={row.how === 'name' || row.how === 'override' ? 'neutral' : 'danger'}>
              {t(`imports:match.how.${row.how}`)}
            </Badge>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function People({ rows }: { rows: ImportPerson[] }) {
  const { t } = useTranslation(['imports'])

  return (
    <Card className="flex flex-col gap-2" data-testid="import-people">
      <p className="text-sm font-medium">{t('imports:people.title')}</p>
      <p className="text-xs text-muted">{t('imports:people.hint')}</p>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">{t('imports:people.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {rows.map((row) => (
            <li key={row.source_id} className="flex flex-wrap items-center gap-2">
              <span>{row.name}</span>
              <span className="text-xs text-muted">{row.email}</span>
              <Badge tone={row.reason === '' ? 'neutral' : 'danger'}>
                {t(`imports:people.reason.${row.reason === '' ? 'matched' : row.reason}`)}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Dangling({ preview }: { preview: ImportPreview }) {
  const { t } = useTranslation(['imports'])
  const total =
    preview.dangling_parents.length +
    preview.dangling_relations.length +
    preview.unknown_relation_kinds.length
  if (total === 0) return null

  return (
    <Card className="flex flex-col gap-1" data-testid="import-dangling">
      <p className="text-sm font-medium">{t('imports:dangling.title')}</p>
      <p className="text-xs text-muted">{t('imports:dangling.hint')}</p>
      <ul className="list-disc pl-5 text-sm">
        {preview.dangling_parents.length > 0 ? (
          <li>{t('imports:dangling.parents', { count: preview.dangling_parents.length })}</li>
        ) : null}
        {preview.dangling_relations.length > 0 ? (
          <li>{t('imports:dangling.relations', { count: preview.dangling_relations.length })}</li>
        ) : null}
        {preview.unknown_relation_kinds.length > 0 ? (
          <li>
            {t('imports:dangling.unknownKinds', {
              count: preview.unknown_relation_kinds.length,
            })}
          </li>
        ) : null}
      </ul>
    </Card>
  )
}

function Loaded({ loaded }: { loaded: ImportLoaded }) {
  const { t } = useTranslation(['imports'])

  return (
    <Card className="flex flex-col gap-3 border-accent" data-testid="import-loaded">
      <p className="text-sm font-medium">{t('imports:load.title')}</p>
      <dl className="grid grid-cols-3 gap-2 text-sm">
        <Count
          label={t('imports:load.issuesCreated')}
          value={loaded.issues_created}
          testId="import-issues-created"
        />
        <Count
          label={t('imports:load.issuesSkipped')}
          value={loaded.issues_skipped}
          testId="import-issues-skipped"
        />
        <Count label={t('imports:load.commentsCreated')} value={loaded.comments_created} />
        <Count label={t('imports:load.parentsLinked')} value={loaded.parents_linked} />
        <Count label={t('imports:load.relationsLinked')} value={loaded.relations_linked} />
      </dl>
      {/*
        **접어 두지 않는다.** 이 목록은 여기서 안 보면 아무 데도 안 남는다.
      */}
      <div className="flex flex-col gap-1" data-testid="import-unmoved">
        <p className="text-sm font-medium">{t('imports:load.unmoved')}</p>
        {loaded.unmoved.length === 0 ? (
          <p className="text-sm text-muted">{t('imports:load.unmovedEmpty')}</p>
        ) : (
          <>
            <p className="text-xs text-muted">{t('imports:load.unmovedHint')}</p>
            <ul className="list-disc pl-5 text-sm">
              {loaded.unmoved.map((row) => (
                <li key={row}>{row}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Card>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="truncate">{value}</dd>
    </>
  )
}

function Count({ label, value, testId }: { label: string; value: number; testId?: string }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums" data-testid={testId}>
        {value}
      </dd>
    </div>
  )
}
