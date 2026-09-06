import { describe, expect, it } from 'vitest'

import { copiedFromEditor, htmlToMarkdown, pastedMarkdown } from './html'

describe('htmlToMarkdown', () => {
  it('제목과 강조를 옮긴다', () => {
    expect(htmlToMarkdown('<h2>제목</h2><p>보통 <strong>굵게</strong> <em>기울임</em></p>')).toBe(
      '## 제목\n\n보통 **굵게** *기울임*',
    )
  })

  it('여러 칸 공백과 줄바꿈을 한 칸으로 편다', () => {
    // 원문의 들여쓰기를 그대로 옮기면 마크다운에서 코드 블록이 된다.
    expect(htmlToMarkdown('<p>앞\n   뒤 끝</p>')).toBe('앞 뒤 끝')
  })

  it('빈 문단은 만들지 않는다', () => {
    expect(htmlToMarkdown('<p>하나</p><p>  </p><p>둘</p>')).toBe('하나\n\n둘')
  })

  it('링크를 옮긴다', () => {
    expect(htmlToMarkdown('<p><a href="https://example.com">예시</a></p>')).toBe(
      '[예시](https://example.com)',
    )
  })

  it('우리 주소는 스킴으로 바꾸고 이름을 붙인다', () => {
    // 호스트를 그대로 저장하면 주소가 바뀌는 순간 전부 죽는다.
    expect(htmlToMarkdown('<p><a href="http://app/issues/DEV-1">http://app/issues/DEV-1</a></p>'))
      .toBe('[DEV-1](issue:DEV-1)')
  })

  it('우리 주소라도 사람이 쓴 링크 글자는 지킨다', () => {
    expect(htmlToMarkdown('<p><a href="http://app/wiki/ENG/deploy">배포 문서</a></p>')).toBe(
      '[배포 문서](page:ENG/deploy)',
    )
  })

  it('항목마다 문단 하나면 촘촘한 목록이다', () => {
    // 구글 문서·컨플루언스는 항목을 늘 `<p>` 로 감싼다. 그걸 성근 목록으로
    // 읽으면 원본에서 붙어 있던 목록이 전부 빈 줄로 벌어진다.
    expect(htmlToMarkdown('<ul><li>하나</li><li>둘</li></ul>')).toBe('- 하나\n- 둘')
    expect(htmlToMarkdown('<ul><li><p>하나</p></li><li><p>둘</p></li></ul>')).toBe('- 하나\n- 둘')
  })

  it('덩이가 둘 이상인 항목이 있으면 성근 목록이다', () => {
    expect(htmlToMarkdown('<ul><li><p>하나</p><p>덧붙임</p></li><li><p>둘</p></li></ul>')).toBe(
      '- 하나\n\n  덧붙임\n\n- 둘',
    )
  })

  it('구글 문서가 통째로 감싼 껍데기를 파고든다', () => {
    // 문서 전체가 `<b style="font-weight:normal">` 안에 들어온다. 인라인으로
    // 읽으면 제목도 목록도 표도 한 문단으로 뭉개진다.
    const html =
      '<meta charset="utf-8"><b style="font-weight:normal" id="docs-internal-guid-1">' +
      '<h1><span style="font-weight:700">릴리스</span></h1>' +
      '<p><span style="font-weight:400">보통 </span><span style="font-weight:700">굵게</span></p>' +
      '<ul><li><p><span>가</span></p></li><li><p><span>나</span></p></li></ul></b>'
    expect(htmlToMarkdown(html)).toBe('# 릴리스\n\n보통 **굵게**\n\n- 가\n- 나')
  })

  it('모르는 태그가 감싸고 있어도 파고든다', () => {
    // 스프레드시트는 `<google-sheets-html-origin>` 으로 감싸서 준다.
    const html =
      '<google-sheets-html-origin><table><tbody><tr><td>분기</td><td>매출</td></tr>' +
      '<tr><td>Q1</td><td style="text-align:right">1,200</td></tr></tbody></table>' +
      '</google-sheets-html-origin>'
    expect(htmlToMarkdown(html)).toBe('| 분기 | 매출 |\n| --- | --- |\n| Q1 | 1,200 |')
  })

  it('제목 전체에 걸린 굵게는 걷어낸다', () => {
    // 제목은 이미 굵다. `## **제목**` 은 원문에 없던 별표다.
    expect(htmlToMarkdown('<h2><b>제목</b></h2>')).toBe('## 제목')
    // 일부만 굵은 것은 뜻이 있다.
    expect(htmlToMarkdown('<h2>앞 <b>굵게</b></h2>')).toBe('## 앞 **굵게**')
  })

  it('번호 목록의 시작 번호를 지킨다', () => {
    expect(htmlToMarkdown('<ol start="3"><li>셋</li><li>넷</li></ol>')).toBe('3. 셋\n4. 넷')
  })

  it('체크박스 목록을 옮긴다', () => {
    const html =
      '<ul><li><input type="checkbox" checked>했다</li><li><input type="checkbox">안 했다</li></ul>'
    expect(htmlToMarkdown(html)).toBe('- [x] 했다\n- [ ] 안 했다')
  })

  it('표를 옮긴다 — 정렬도 따라간다', () => {
    const html =
      '<table><thead><tr><th>이름</th><th style="text-align:right">수</th></tr></thead>' +
      '<tbody><tr><td>가</td><td>1</td></tr></tbody></table>'
    expect(htmlToMarkdown(html)).toBe('| 이름 | 수 |\n| --- | ---: |\n| 가 | 1 |')
  })

  it('코드 블록의 언어를 읽는다', () => {
    expect(htmlToMarkdown('<pre><code class="language-ts">const a = 1\n</code></pre>')).toBe(
      '```ts\nconst a = 1\n```',
    )
  })

  it('코드 블록 안의 들여쓰기는 그대로 둔다', () => {
    expect(htmlToMarkdown('<pre><code>if (a) {\n  b()\n}</code></pre>')).toBe(
      '```\nif (a) {\n  b()\n}\n```',
    )
  })

  it('인용을 옮긴다', () => {
    expect(htmlToMarkdown('<blockquote><p>인용</p></blockquote>')).toBe('> 인용')
  })

  it('이미지 주소를 적힌 그대로 쓴다', () => {
    // `src` 프로퍼티로 읽으면 브라우저가 절대 주소로 바꿔 놓는다.
    expect(htmlToMarkdown('<p><img src="attachment:abc/a.png" alt="그림"></p>')).toBe(
      '![그림](attachment:abc/a.png)',
    )
  })

  it('구글 문서가 씌우는 굵게 껍데기에 속지 않는다', () => {
    const html = '<b style="font-weight:normal" id="docs-internal-guid-1"><p>보통 글</p></b>'
    expect(htmlToMarkdown(html)).toBe('보통 글')
  })

  it('스타일로만 준 굵게·기울임도 읽는다', () => {
    const html = '<p><span style="font-weight:700">굵게</span><span style="font-style:italic">기울임</span></p>'
    expect(htmlToMarkdown(html)).toBe('**굵게***기울임*')
  })

  it('껍데기 div 안의 블록을 파고든다', () => {
    expect(htmlToMarkdown('<div><div><h3>안쪽</h3></div><div>글자</div></div>')).toBe(
      '### 안쪽\n\n글자',
    )
  })

  it('블록 사이에 흩어진 글자를 잃지 않는다', () => {
    expect(htmlToMarkdown('<div>앞<p>가운데</p>뒤</div>')).toBe('앞\n\n가운데\n\n뒤')
  })

  it('script 와 style 은 버린다', () => {
    expect(htmlToMarkdown('<p>글</p><script>alert(1)</script><style>p{color:red}</style>')).toBe(
      '글',
    )
  })

  it('줄바꿈 태그는 강제 개행이 된다', () => {
    expect(htmlToMarkdown('<p>앞<br>뒤</p>')).toBe('앞\\\n뒤')
  })
})

describe('pastedMarkdown', () => {
  it('구조가 있으면 변환한다', () => {
    expect(pastedMarkdown('<h1>제목</h1>')).toBe('# 제목')
  })

  it('문단은 손댈 것으로 센다', () => {
    // 사람이 문단으로 쓴 것이다. `div` 와 달리 있다는 사실 자체가 뜻이 있다.
    expect(pastedMarkdown('<p>한 줄</p><p>두 줄</p>')).toBe('한 줄\n\n두 줄')
  })

  it('색깔만 입힌 div·span 은 건드리지 않는다', () => {
    // 편집기에서 코드를 복사하면 이렇게 온다. 변환하면 들여쓰기가 사라진다.
    const html = '<div><span style="color:#001080">const</span> a = 1</div>'
    expect(pastedMarkdown(html)).toBeNull()
  })

  it('빈 것은 건드리지 않는다', () => {
    expect(pastedMarkdown('')).toBeNull()
    expect(pastedMarkdown('   ')).toBeNull()
  })

  it('구조는 있는데 글자가 없으면 건드리지 않는다', () => {
    expect(pastedMarkdown('<p><a href="#"></a></p>')).toBeNull()
  })
})

describe('copiedFromEditor', () => {
  it('ProseMirror 조각을 알아본다', () => {
    expect(copiedFromEditor('<p data-pm-slice="1 1 []">글</p>')).toBe(true)
    expect(copiedFromEditor('<p>글</p>')).toBe(false)
  })
})
