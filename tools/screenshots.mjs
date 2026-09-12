/**
 * README 의 화면 사진을 **실제로 도는 앱에서** 찍는다.
 *
 * 사진은 만들어 두면 낡는다. 화면이 바뀌었는데 README 의 사진이 그대로면
 * 그건 이 저장소가 이미 여러 번 겪은 종류의 거짓말이다 — 그래서 다시 찍는
 * 방법을 코드로 둔다.
 *
 *   make dev          # 스택을 띄우고
 *   make reset-db && make seed && make seed-fields
 *   pnpm --filter @ieum/web dev
 *   node tools/screenshots.mjs
 *
 * 데모 데이터도 여기서 만든다. **시험이 남긴 DB 로 찍지 않는다** — 프로젝트
 * 이름이 전부 `E2E` 인 화면은 제품을 잘못 보여 준다(실제로 그 상태였다).
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const API = process.env.IEUM_API ?? 'http://127.0.0.1:8000'
const WEB = process.env.IEUM_WEB ?? 'http://localhost:5173'
const OUT = 'assets/readme'
const EMAIL = process.env.SEED_ADMIN_EMAIL ?? 'admin@example.com'
const PASSWORD = process.env.SEED_ADMIN_PASSWORD ?? 'seed-admin-password-1234'

/** 사진 크기. 노트북 창 하나. 너무 크면 README 에서 글씨가 작아진다. */
const VIEWPORT = { width: 1440, height: 900 }

let token = ''

async function call(method, path, body) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const text = await response.text()
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status} ${text.slice(0, 300)}`)
  return text ? JSON.parse(text) : {}
}

// ── 데모 데이터 ────────────────────────────────────────────────────
//
// 이름을 **읽을 만한 것**으로 짓는다. 사진에 그대로 찍히므로 여기가 곧
// 제품의 첫인상이다.

const PEOPLE = [
  { email: 'jiwoo@example.com', display_name: '김지우' },
  { email: 'minseo@example.com', display_name: '박민서' },
  { email: 'haeun@example.com', display_name: '이하은' },
]

/**
 * `priority` 는 **1 이 최고**다. 처음에 5 를 최고로 알고 넣었더니 화면에
 * "최저" 로 찍혔다 — 사진을 보고 알았다.
 *
 * `mine` 은 관리자에게 배정한다는 뜻이다. 첫 화면("내게 배정된 이슈")이
 * 비어 있으면 README 가 죽은 제품을 보여 준다.
 */
const ISSUES = [
  {
    summary: '로그인 후 첫 화면이 늦게 뜬다',
    priority: 2,
    mine: true,
    advance: 1,
    description:
      '대시보드가 뜨기까지 3초 넘게 걸린다. 위젯 넷을 각각 부르는 것으로 보인다.\n\n' +
      '- [ ] 어느 요청이 오래 걸리는지 계측\n- [ ] 한 번에 부를 수 있는지 확인',
    labels: ['성능'],
  },
  { summary: '이슈 목록에서 담당자로 걸러지지 않는다', priority: 2, who: 0, advance: 1, labels: ['버그'] },
  { summary: '주간 배포 노트를 자동으로 만든다', priority: 4, who: 1 },
  { summary: '문서 첨부가 10MB 를 넘으면 조용히 실패한다', priority: 1, mine: true, labels: ['버그'] },
  { summary: '검색 결과에 문서와 이슈를 섞어 보여 준다', priority: 3, who: 2, advance: 2 },
  { summary: '모바일에서 보드 열이 잘린다', priority: 3, who: 1, labels: ['UI'] },
  { summary: '알림 설정을 프로젝트별로 나눈다', priority: 4 },
  { summary: '오래된 초대 링크를 만료시킨다', priority: 2, who: 0, labels: ['보안'] },
]

const PAGES = [
  {
    title: '팀 위키',
    body:
      '이 스페이스는 **웹 서비스 팀**이 함께 쓰는 곳입니다.\n\n' +
      ':::info\n문서는 마크다운이 정본입니다. 서식 편집기로 고쳐도 저장되는 것은 마크다운입니다.\n:::\n\n' +
      '## 여기 있는 것\n\n::children\n',
  },
  {
    title: '배포 절차',
    body:
      '## 올리기\n\n1. `main` 이 초록인지 본다\n2. 태그를 붙인다 — `git tag -a v1.2.0`\n' +
      '3. 릴리스 워크플로가 이미지를 올리고 노트를 만든다\n\n' +
      '## 되돌리기\n\n앞 판 이미지 태그로 바꿔 다시 띄운다. 마이그레이션이 있었으면\n' +
      '`alembic downgrade -1` 을 먼저 본다.\n\n' +
      '## 이번 분기에 남은 일\n\n' +
      '::issues{query="project = WEB AND status != Done" columns="key,summary,assignee,status"}\n',
  },
  // 온보딩 문서는 **사람을 지목해야** 첫 화면의 "내 할 일" 에 뜬다. id 는
  // 실행할 때 알게 되므로 본문을 그때 만든다.
  null,
]

/** `- [ ] 할 일 [@이름](user:<id>) due:YYYY-MM-DD` */
function onboarding(me) {
  const due = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  const who = `[@${me.display_name}](user:${me.id})`
  return {
    title: '온보딩 첫 주',
    body:
      '새로 온 사람이 첫 주에 하는 것. 체크박스에 사람을 지목하면 그 사람의\n' +
      '첫 화면에 뜹니다.\n\n' +
      `- [x] 계정 만들고 2단계 인증 켜기 ${who}\n` +
      `- [ ] 개발 스택 띄우고 이슈 하나 끝내기 ${who} due:${due}\n` +
      `- [ ] 팀 위키 훑어보기 ${who} due:${due}\n\n` +
      '막히면 `#팀-웹` 에 물어보세요.\n',
  }
}

/**
 * 이미 있으면 그것을 쓴다.
 *
 * **이 스크립트는 여러 번 돌 수 있어야 한다.** 중간에 한 번 실패하면(실제로
 * 그랬다 — 로그인 선택자가 틀려서) 프로젝트만 만들어진 채로 멈추고, 그
 * 다음부터는 409 로 아예 못 돌게 된다.
 */
async function reuse(find, make) {
  const found = await find()
  return found ?? (await make())
}

/** 목록 응답은 맨 배열일 때도 `{ items }` 일 때도 있다. */
const asList = (body) => (Array.isArray(body) ? body : (body.items ?? []))

async function seedDemo() {
  const signedIn = await call('POST', '/api/v1/auth/login', { email: EMAIL, password: PASSWORD })
  token = signedIn.access_token

  const people = []
  for (const person of PEOPLE) {
    try {
      people.push(await call('POST', '/api/v1/users/invite', person))
    } catch (error) {
      // 이미 있으면 그대로 쓴다. 이 스크립트는 여러 번 돌 수 있어야 한다.
      if (!String(error).includes('409')) throw error
    }
  }

  const project = await reuse(
    async () =>
      (await call('GET', '/api/v1/projects?limit=100')).items?.find((p) => p.key === 'WEB'),
    () =>
      call('POST', '/api/v1/projects', {
        key: 'WEB',
        name: '웹 서비스',
        description: '고객이 쓰는 웹 앱. 이슈·문서·요청이 여기 모인다.',
      }),
  )
  const types = await call('GET', `/api/v1/issues/types?project_id=${project.id}`)

  /*
   * **알림 제목은 만들어질 때의 언어로 굳는다.** 나중에 화면 언어를 바꿔도
   * 이미 만들어진 알림은 안 바뀐다 — 사진에 영어 알림이 찍혀서 알았다.
   * 그래서 이벤트를 만들기 **전에** 계정 언어를 정한다.
   */
  await call('PATCH', '/api/v1/users/me', { locale: 'ko' })

  /*
   * **알림은 남이 움직여야 생긴다.** 기본값은 자기 행동을 안 알리므로, 혼자
   * 만드는 데모에서는 알림 칸이 영영 빈다.
   *
   * 두 번째 사람을 진짜로 만들어 그 사람이 코멘트하게 하는 길도 있지만
   * (초대 메일 → 수락 → 역할 부여), 역할 API 는 step-up 2FA 뒤에 있어서
   * 사진 한 칸에 비해 장치가 너무 커진다. 대신 **제품이 실제로 가진 설정**을
   * 켠다 — 알림 자체는 지어낸 것이 아니라 아래 작업들이 만든 진짜 이벤트다.
   */
  await call('PATCH', '/api/v1/notifications/preferences', {
    in_app: true,
    notify_own_actions: true,
  })

  // 배정할 사람. 초대만 된 계정도 담당자로 붙는다.
  const everyone = (await call('GET', '/api/v1/users?limit=50')).items ?? []
  const me = everyone.find((u) => u.email === EMAIL)
  const mates = PEOPLE.map((p) => everyone.find((u) => u.email === p.email)).filter(Boolean)

  const listed = await call('GET', `/api/v1/issues?project_id=${project.id}&limit=100`)
  let made = listed.items ?? []
  if (made.length === 0) {
    for (const [index, spec] of ISSUES.entries()) {
      const { mine, who, advance, ...fields } = spec
      const assignee = mine ? me : who === undefined ? undefined : mates[who]
      const issue = await call('POST', '/api/v1/issues', {
        project_id: project.id,
        type_id: types[index % types.length].id,
        ...fields,
      })
      // **상태가 전부 Open 이면 죽은 데이터로 보인다.** 워크플로가 주는
      // 전이를 그대로 밟는다 — 이름을 여기서 지어내지 않는다.
      for (let step = 0; step < (advance ?? 0); step += 1) {
        const next = await call('GET', `/api/v1/issues/${issue.id}/transitions`)
        const first = (Array.isArray(next) ? next : (next.items ?? []))[0]
        if (!first) break
        await call('POST', `/api/v1/issues/${issue.id}/transition`, { transition_id: first.id })
      }
      // 담당자는 **전이 뒤에** 정한다. 전이가 담당자를 자기에게 가져가는 것을
      // 사진에서 봤다 — 먼저 배정하면 그것이 덮인다.
      if (assignee) {
        await call('PATCH', `/api/v1/issues/${issue.id}`, {
          changes: { assignee_id: assignee.id },
        })
      }
      made.push(issue)
    }
    // 코멘트가 있는 이슈가 하나는 있어야 상세 화면이 비어 보이지 않는다.
    await call('POST', `/api/v1/issues/${made[0].id}/comments`, {
      body: '계측해 보니 위젯 넷이 각각 요청을 보낸다. 한 번에 묶는 쪽으로 본다.',
    })
    await call('POST', `/api/v1/issues/${made[0].id}/comments`, {
      body: '묶어 봤더니 3.1초 → 0.6초. 나머지는 첫 렌더에서 먹는 시간이다.',
    })
  }

  /*
   * **알림은 워치하는 사람에게 간다.** 설정만 켜 두고 아무것도 안 보고
   * 있으면 받을 사람이 없어서 아무 일도 안 난다 — 아웃박스는 다 처리됐는데
   * 알림이 0인 것을 보고 알았다.
   *
   * 프로젝트를 워치하면 그 안의 이슈 변화를 받는다. 실제로 팀에서 하는 것도
   * 이것이다.
   */
  await call('POST', '/api/v1/watches', {
    target_type: 'project',
    target_id: project.id,
  }).catch(() => undefined)

  // 워치한 뒤에 일어난 일만 알림이 된다. 이슈는 위에서 이미 만들었으므로
  // 여기서 한 번 더 움직여 준다 — 코멘트 하나와 전이 하나.
  if (made[0]) {
    await call('POST', `/api/v1/issues/${made[0].id}/comments`, {
      body: '위젯을 묶는 쪽으로 고쳐서 올렸다. 다음 판에서 다시 재 본다.',
    })
  }
  // 전이는 **다른 이슈**에 건다. `made[0]` 에 걸었더니 In Progress 였던 것이
  // 한 칸 더 가서 Open 으로 돌아왔고, 목록 사진에서 상태 변화가 사라졌다.
  if (made[6]) {
    const next = asList(await call('GET', `/api/v1/issues/${made[6].id}/transitions`))
    if (next[0]) {
      await call('POST', `/api/v1/issues/${made[6].id}/transition`, {
        transition_id: next[0].id,
      })
    }
  }

  // 스프린트. 첫 화면의 "내 일이 든 스프린트" 가 이것을 읽는다.
  const sprints = await call('GET', `/api/v1/sprints?project_id=${project.id}`).catch(() => [])
  if (asList(sprints).length === 0) {
    const today = new Date()
    const later = new Date(today.getTime() + 12 * 24 * 60 * 60 * 1000)
    const sprint = await call('POST', '/api/v1/sprints', {
      project_id: project.id,
      name: '9월 둘째 주',
      goal: '첫 화면을 빠르게, 첨부를 조용히 실패하지 않게.',
      starts_at: today.toISOString().slice(0, 10),
      ends_at: later.toISOString().slice(0, 10),
    })
    await call('POST', '/api/v1/sprints/issues', {
      project_id: project.id,
      sprint_id: sprint.id,
      issue_ids: made.slice(0, 4).map((i) => i.id),
    })
    // 시작하지 않으면 백로그다 — 첫 화면은 도는 스프린트를 본다.
    await call('POST', `/api/v1/sprints/${sprint.id}/start`, {}).catch(() => undefined)
  }

  const space = await reuse(
    () => call('GET', '/api/v1/spaces/by-key/WEB').catch(() => null),
    () => call('POST', '/api/v1/spaces', { key: 'WEB', name: '웹 서비스 팀' }),
  )
  // **트리는 맨 배열로 온다.** `{ items }` 로 읽었더니 늘 비어 보였고,
  // 돌릴 때마다 문서가 한 벌씩 더 생겼다(사진에 트리가 두 벌로 찍혔다).
  let pages = asList(await call('GET', `/api/v1/spaces/${space.id}/tree`).catch(() => []))
  if (pages.length === 0) {
    pages = []
    for (const page of PAGES) {
      const body = page ?? onboarding(me)
      pages.push(
        await call('POST', '/api/v1/pages', {
          space_id: space.id,
          publish: true,
          ...(pages.length === 0 ? {} : { parent_id: pages[0].id }),
          ...body,
        }),
      )
    }
  }

  // 만들자마자 받은 응답과 트리가 같은 모양이라는 보장이 없다. 사진에 쓸
  // **경로**는 트리에서 다시 읽는다.
  const walk = (nodes) => nodes.flatMap((n) => [n, ...walk(n.children ?? [])])
  const flat = walk(asList(await call('GET', `/api/v1/spaces/${space.id}/tree`)))

  return { project, issues: made, space, pages: flat.length ? flat : pages }
}

// ── 촬영 ──────────────────────────────────────────────────────────

async function shoot(page, name, { full = false } = {}) {
  // 애니메이션과 늦게 오는 값이 사진마다 달라지지 않게 한 박자 기다린다.
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full })
  console.log(`  ${OUT}/${name}.png`)
}

async function main() {
  await mkdir(OUT, { recursive: true })
  console.log('데모 데이터를 만든다…')
  const demo = await seedDemo()

  const browser = await chromium.launch({
    ...(process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {}),
  })
  const context = await browser.newContext({ viewport: VIEWPORT, locale: 'ko-KR' })
  const page = await context.newPage()

  // **알림은 워커가 아웃박스를 훑은 뒤에 생긴다**(15초 주기). 바로 찍으면
  // 방금 만든 이벤트가 아직 알림이 아니라 첫 화면이 비어 보인다.
  process.stdout.write('알림이 도착하길 기다린다')
  for (let tick = 0; tick < 40; tick += 1) {
    const unread = asList(await call('GET', '/api/v1/notifications?only_unread=true&limit=5'))
    if (unread.length > 0) break
    process.stdout.write('.')
    await new Promise((done) => setTimeout(done, 3000))
  }
  console.log('')

  console.log('찍는다…')
  // 로그인 폼은 `/` 에 있다. 들어가 있지 않으면 셸 대신 그것이 그려진다.
  await page.goto(`${WEB}/`)
  await page.getByLabel(/email|이메일/i).fill(EMAIL)
  await page.getByLabel(/password|비밀번호/i).fill(PASSWORD)
  await page.getByRole('button', { name: /^(sign in|로그인)$/i }).click()
  await page.getByRole('button', { name: /sign out|로그아웃/i }).waitFor()

  // **화면 언어는 서버가 사람마다 기억한다.** README 가 한국어이므로 사진도
  // 한국어여야 한다 — 브라우저 로케일이 아니라 이 선택이 화면을 정한다.
  await page.getByLabel(/language|언어/i).selectOption('ko')
  await page.getByRole('button', { name: /로그아웃/i }).waitFor()

  await shoot(page, '02-dashboard')

  await page.goto(`${WEB}/issues?iql=${encodeURIComponent('project = WEB ORDER BY priority DESC')}`)
  await shoot(page, '03-issues')

  await page.goto(`${WEB}/issues/${demo.issues[0].key}`)
  await shoot(page, '04-issue')

  // **문서 주소는 id 가 아니라 경로다** (`/wiki/$spaceKey/$`). id 로 열면
  // "찾을 수 없습니다" 가 찍힌다 — 사진을 보고 알았다.
  const deploy = demo.pages.find((p) => p.title === '배포 절차') ?? demo.pages[1]
  await page.goto(`${WEB}/wiki/${demo.space.key}/${encodeURI(deploy.path)}`)
  await shoot(page, '05-wiki')

  // **한 낱말이 이슈와 문서를 함께 잡는 것**이 이 화면의 요점이다. 문서만
  // 나오면 통합 검색이라는 말이 사진에서 안 보인다.
  await page.goto(`${WEB}/search?q=${encodeURIComponent('배포')}&offset=0`)
  await shoot(page, '06-search')

  // 단축키 도움말은 덮개라 주소가 없다. 눌러서 띄운다.
  await page.goto(`${WEB}/issues?iql=${encodeURIComponent('project = WEB')}`)
  await page.waitForLoadState('networkidle').catch(() => undefined)
  await page.keyboard.press('?')
  await shoot(page, '07-shortcuts')

  await browser.close()
  await writeFile(
    `${OUT}/README.md`,
    '# README 의 화면 사진\n\n' +
      '`node tools/screenshots.mjs` 가 만든다. 손으로 고치지 않는다 —\n' +
      '화면이 바뀌면 스크립트를 다시 돌린다.\n',
    'utf-8',
  )
  console.log('끝.')
}

await main()
