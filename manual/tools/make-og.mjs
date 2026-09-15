/**
 * 링크를 붙였을 때 뜨는 그림(`og:image`)을 만든다.
 *
 * 손으로 그린 그림은 **화면이 바뀌면 낡는다.** 이 저장소가 이미 겪은 종류의
 * 거짓말이라서, 여기서도 만드는 방법을 코드로 둔다: 제품의 실제 화면 사진과
 * 저장소가 싣는 글꼴로 카드를 조판하고 브라우저로 찍는다.
 *
 *   node manual/tools/make-og.mjs
 *
 * 글꼴을 밖에서 가져오지 않는 것이 중요하다. 이 카드는 `apps/web/public` 을
 * 뿌리로 하는 작은 서버 위에서 그려지고, Pretendard 는 그 안에서 온다 —
 * 제품이 쓰는 것과 **같은 파일**이다. 시스템 글꼴에 맡기면 이 컨테이너에서는
 * 한글이 중국어 글꼴로 찍힌다(실제로 그랬다).
 *
 * 크기 1200×630 은 카카오톡·슬랙·트위터·페이스북이 공통으로 잘 다루는 비율이다.
 */

import { mkdir, copyFile, writeFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { extname, join, normalize } from 'node:path'

import { chromium } from 'playwright'

const REPO = new URL('../..', import.meta.url).pathname
const ROOT = join(REPO, 'apps/web/public')
const TMP = join(ROOT, '__og')
// **위키 쪽을 쓴다.** 카드가 말하는 것("셋을 하나로 잇는다")을 한 장으로
// 보여 주는 화면이기 때문이다 — 문서 본문 안에 이슈 표가 살아서 그려져 있다.
// 첫 화면 사진은 "내게 배정된" 목록이라 이 주장을 못 보여 준다.
const SHOT = join(REPO, 'assets/readme/05-wiki.png')
const OUT = join(REPO, 'manual/docs/assets/og.png')
const PORT = 8931

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
}

/** 카드. 글자는 왼쪽, 제품 화면은 오른쪽에서 잘려 나간다. */
const CARD = `<!doctype html>
<meta charset="utf-8">
<link rel="stylesheet" href="./pretendard.css">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    width: 1200px; height: 630px; overflow: hidden;
    display: flex; align-items: center;
    font-family: 'Pretendard Variable', Pretendard, sans-serif;
    background: #0d2f31;
    color: #e6f4f1;
  }
  .left { width: 480px; padding: 0 0 0 64px; flex: none; }
  .mark { font-size: 68px; font-weight: 800; letter-spacing: -0.03em; }
  .mark small { font-size: 30px; font-weight: 600; color: #7fd4c8; margin-left: 12px; letter-spacing: -0.01em; }
  .tag { margin-top: 16px; font-size: 27px; font-weight: 600; line-height: 1.45; color: #bfe6df; }
  .sub { margin-top: 22px; font-size: 19px; font-weight: 500; color: #7fa8a4; }
  .shot {
    flex: 1; height: 100%; position: relative; overflow: hidden;
    border-left: 1px solid rgba(127, 212, 200, 0.25);
  }
  /* 사이드바와 문서 트리를 지나 **본문이 보이는 자리**로 밀어 넣는다.
     왼쪽 끝부터 보여 주면 카드의 절반이 메뉴가 된다.
     배율은 표가 통째로 들어오는 선에서 정했다 — 이슈 키가 잘리면
     "WEB-1" 이 "B-1" 로 보이고, 그건 제품을 잘못 보여 주는 것이다. */
  .shot img {
    position: absolute; top: -26px; left: -472px;
    width: 1224px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
  }
</style>
<div class="left">
  <div class="mark">Ieum<small>이음</small></div>
  <div class="tag">이슈 · 위키 · 서비스데스크를<br>하나로 잇습니다</div>
  <div class="sub">셀프호스팅 · 데이터는 여러분의 서버에만</div>
</div>
<div class="shot"><img src="./shot.png" alt=""></div>
`

async function serve() {
  const server = createServer(async (req, res) => {
    // 경로를 뿌리 밖으로 못 나가게 자른다. 잠깐 뜨는 서버지만, 밖으로
    // 나가는 길을 열어 두는 습관은 어디서든 한 번은 물린다.
    const path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname))
    const file = join(ROOT, path)
    if (!file.startsWith(ROOT)) {
      res.writeHead(403).end()
      return
    }
    try {
      await stat(file)
      res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream' })
      res.end(await readFile(file))
    } catch {
      res.writeHead(404).end()
    }
  })
  await new Promise((done) => server.listen(PORT, '127.0.0.1', done))
  return server
}

async function main() {
  await mkdir(TMP, { recursive: true })
  await mkdir(join(REPO, 'manual/docs/assets'), { recursive: true })
  // 글꼴 규칙은 제품이 쓰는 그 파일이다. 안의 `url()` 은 `/fonts/...` 로
  // 절대 경로라, 뿌리가 `apps/web/public` 인 이 서버에서 그대로 풀린다.
  await copyFile(join(REPO, 'apps/web/src/styles/pretendard.css'), join(TMP, 'pretendard.css'))
  await copyFile(SHOT, join(TMP, 'shot.png'))
  await writeFile(join(TMP, 'card.html'), CARD, 'utf-8')

  const server = await serve()
  const browser = await chromium.launch({
    ...(process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {}),
  })
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 630 } })
    await page.goto(`http://127.0.0.1:${PORT}/__og/card.html`)
    // **글꼴이 실제로 온 뒤에 찍는다.** 안 기다리면 대체 글꼴로 한 번 그려진
    // 화면이 찍히고, 그건 우리가 고른 글꼴이 아니다.
    await page.evaluate(() => document.fonts.ready)
    await page.waitForTimeout(300)
    await page.screenshot({ path: OUT })
    console.log(`${OUT} (1200×630)`)
  } finally {
    await browser.close()
    server.close()
    await rm(TMP, { recursive: true, force: true })
  }
}

await main()
