/**
 * 받은 바이트를 파일로 저장한다.
 *
 * `<a href="...">` 로 서버 경로를 걸 수 없다 — 액세스 토큰이 메모리에만 있어
 * 앵커 클릭에는 Authorization 헤더가 안 붙는다. 그래서 fetch 로 받아
 * blob 으로 내려준다 (첨부 다운로드와 같은 이유).
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  // 문서에 붙였다 뗀다. 떼어 둔 앵커의 클릭을 무시하는 브라우저가 있다.
  anchor.style.display = 'none'
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  // 바로 revoke 하면 아직 시작 중인 저장이 취소될 수 있다. 다음 틱에 푼다.
  setTimeout(() => { URL.revokeObjectURL(url) }, 0)
}
