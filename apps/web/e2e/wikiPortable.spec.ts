/**
 * `.md` 가져오기·내보내기와 **라운드트립**.
 *
 * M2 완료 조건이 여기 걸려 있다 (wiki-markdown.md 8절). 파서 단위 테스트는
 * 이미 있지만, 브라우저에서만 깨지는 게 따로 있다: multipart 본문에
 * `Content-Type` 을 우리가 붙이면 서버가 못 읽고, 액세스 토큰이 메모리에만
 * 있어 앵커에 서버 경로를 걸면 내려받기가 401 로 죽는다.
 *
 * 그래서 ZIP 을 파싱해 확인하지 않는다. **내보낸 ZIP 을 다른 스페이스로 다시
 * 올려서** 같은 문서가 나오는지 본다 — 그게 계약 그대로다.
 */

import { readFile } from 'node:fs/promises'

import type { Page } from '@playwright/test'

import { bodyField, createSpace, expect, signIn, test, uniqueKey } from './fixtures'

function spaceKey(): string {
  return uniqueKey('P')
}

/** 숨은 file input 을 직접 건드리지 않는다. 사람이 누르는 버튼을 누른다. */
async function importFile(
  page: Page,
  file: { name: string; mimeType: string; buffer: Buffer },
): Promise<void> {
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'Import .md' }).click()
  await (await chooser).setFiles(file)
}

test('`.md` 하나를 올리면 문서가 된다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await importFile(page, {
    name: 'deploy-runbook.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Deploy Runbook\n\n1. build\n2. ship\n', 'utf-8'),
  })

  // 제목은 첫 H1 에서 가져오고 본문에서는 뗀다 — 두 번 뜨면 안 된다.
  const link = page.getByRole('link', { name: 'Deploy Runbook' })
  await expect(link).toBeVisible()
  await link.click()
  await expect(page.getByRole('heading', { name: 'Deploy Runbook', level: 1 })).toHaveCount(1)
  await expect(page.getByRole('listitem').filter({ hasText: 'build' })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('폴더 구조가 문서 트리가 된다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await importFile(page, {
    name: 'guide.zip',
    mimeType: 'application/zip',
    buffer: zip([
      ['guide/index.md', '# Guide\n\n입구.\n'],
      ['guide/install.md', '# Install\n\n설치한다.\n'],
    ]),
  })

  // `index.md` 는 폴더를 대표한다 — 폴더 밑에 또 index 가 생기지 않는다.
  await page.getByRole('link', { name: 'Guide' }).click()
  await expect(page).toHaveURL(new RegExp(`/wiki/${key}/guide$`))
  await page.getByRole('link', { name: 'Install' }).click()
  await expect(page).toHaveURL(new RegExp(`/wiki/${key}/guide/install$`))

  expect(consoleErrors).toEqual([])
})

test('내보낸 ZIP 을 다시 올리면 같은 문서가 나온다', async ({ page, consoleErrors }) => {
  const source = spaceKey()
  await signIn(page)
  await createSpace(page, source)
  await page.goto(`/wiki/${source}`)

  await importFile(page, {
    name: 'notes.zip',
    mimeType: 'application/zip',
    buffer: zip([
      ['top.md', '---\ntitle: Top\nlabels: [runbook]\nowner: infra\n---\n\n표가 있다.\n\n| 항목 | 값 |\n| --- | --- |\n| a | 1 |\n'],
      ['top/deep.md', '# Deep\n\n- [ ] 안 함\n- [x] 함\n'],
    ]),
  })
  await expect(page.getByRole('link', { name: 'Deep' })).toBeVisible()

  const saved = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export ZIP' }).click()
  const archive = await (await saved).path()

  // 두 번째 스페이스로 되올린다. 여기까지 살아 돌아오면 계약이 지켜진 것이다.
  const target = spaceKey()
  await createSpace(page, target)
  await page.goto(`/wiki/${target}`)
  await importFile(page, {
    name: 'roundtrip.zip',
    mimeType: 'application/zip',
    buffer: await readFile(archive),
  })

  await page.getByRole('link', { name: 'Top', exact: true }).click()
  await expect(page.getByRole('columnheader', { name: '항목' })).toBeVisible()
  // 라벨도 front matter 로 실려 돌아온다.
  await expect(page.getByText('runbook')).toBeVisible()

  await page.getByRole('link', { name: 'Deep' }).click()
  await expect(page).toHaveURL(new RegExp(`/wiki/${target}/top/deep$`))
  await expect(page.getByRole('checkbox', { checked: true })).toBeVisible()

  expect(consoleErrors).toEqual([])
})

test('문서 하나를 `.md` 로 내려받는다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  await importFile(page, {
    name: 'single.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# Single\n\n본문이다.\n', 'utf-8'),
  })
  await page.getByRole('link', { name: 'Single' }).click()

  const saved = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export .md' }).click()
  const download = await saved
  expect(download.suggestedFilename()).toBe('single.md')

  const text = await readFile(await download.path(), 'utf-8')
  // 제목은 front matter 에만 있다. H1 으로도 쓰면 다시 올릴 때 두 번 뜬다.
  expect(text).toContain('title: Single')
  expect(text).not.toContain('# Single')
  expect(text).toContain('본문이다.')

  expect(consoleErrors).toEqual([])
})

test('한글 제목 문서도 내려받힌다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  // 한글 제목은 slug 도 한글이다 (로마자로 옮기지 않는다). 파일명을 헤더에
  // 그대로 실으면 latin-1 인코딩에서 터진다 — 한국어 사용자가 첫 문서에서
  // 바로 500 을 봤다.
  await importFile(page, {
    name: 'runbook.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# 배포 절차\n\n본문이다.\n', 'utf-8'),
  })
  await page.getByRole('link', { name: '배포 절차' }).click()

  const saved = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export .md' }).click()
  const download = await saved
  expect(download.suggestedFilename()).toBe('배포-절차.md')
  expect(await readFile(await download.path(), 'utf-8')).toContain('title: 배포 절차')

  expect(consoleErrors).toEqual([])
})

/**
 * 최소한의 ZIP 라이터 (무압축 store).
 *
 * 의존성을 하나 더 들이지 않는다 — 여기서 필요한 건 "서버가 읽을 수 있는
 * ZIP" 뿐이고, 그건 30 줄이면 된다.
 */
/** 1x1 PNG. 진짜 그림이라야 브라우저가 실제로 그린다. */
const DOT_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

test('묶음 안의 그림이 첨부가 되고 실제로 뜬다', async ({ page, consoleErrors }) => {
  const key = spaceKey()
  await signIn(page)
  await createSpace(page, key)
  await page.goto(`/wiki/${key}`)

  // 상대경로를 그대로 두면 ZIP 에 그림을 같이 넣었는데도 깨진 링크가 된다.
  await importFile(page, {
    name: 'assets.zip',
    mimeType: 'application/zip',
    buffer: zip([
      ['docs/guide.md', '# 안내\n\n![그림](images/dot.png)\n'],
      ['docs/images/dot.png', DOT_PNG],
    ]),
  })

  await page.getByRole('link', { name: '안내' }).click()
  const image = page.locator('article img').first()
  await expect(image).toBeVisible()
  // 서명된 주소로 바뀌어야 한다. `attachment:` 를 그대로 두면 안 뜬다.
  await expect(image).not.toHaveAttribute('src', /^attachment:/)
  // 그리고 실제로 픽셀이 있어야 한다 — 주소만 맞고 못 받는 경우가 있다.
  await expect
    .poll(async () => image.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0))
    .toBe(true)

  // 본문 정본에는 스킴이 남는다. 서명된 주소를 저장하면 몇 분 뒤 죽는다.
  await page.getByRole('button', { name: /^edit$/i }).click()
  await expect(await bodyField(page)).toHaveValue(/!\[그림\]\(attachment:[0-9a-f-]+\/dot\.png\)/)

  expect(consoleErrors).toEqual([])
})

function zip(files: [string, string | Buffer][]): Buffer {
  const locals: Buffer[] = []
  const centrals: Buffer[] = []
  let offset = 0

  for (const [name, content] of files) {
    const nameBytes = Buffer.from(name, 'utf-8')
    const data = Buffer.isBuffer(content) ? content : Buffer.from(content, 'utf-8')
    const crc = crc32(data)

    const local = Buffer.alloc(30 + nameBytes.length)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4) // 필요 버전
    local.writeUInt16LE(0x0800, 6) // 이름이 UTF-8 이다
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(data.length, 18)
    local.writeUInt32LE(data.length, 22)
    local.writeUInt16LE(nameBytes.length, 26)
    nameBytes.copy(local, 30)
    locals.push(local, data)

    const central = Buffer.alloc(46 + nameBytes.length)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(20, 4) // 만든 버전
    central.writeUInt16LE(20, 6)
    central.writeUInt16LE(0x0800, 8)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(data.length, 20)
    central.writeUInt32LE(data.length, 24)
    central.writeUInt16LE(nameBytes.length, 28)
    central.writeUInt32LE(offset, 42)
    nameBytes.copy(central, 46)
    centrals.push(central)

    offset += local.length + data.length
  }

  const directory = Buffer.concat(centrals)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(files.length, 8)
  end.writeUInt16LE(files.length, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...locals, directory, end])
}

function crc32(data: Buffer): number {
  let crc = 0xffffffff
  for (const byte of data) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1
    }
  }
  return (crc ^ 0xffffffff) >>> 0
}
