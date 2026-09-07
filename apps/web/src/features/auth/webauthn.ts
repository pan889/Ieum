/**
 * 브라우저의 WebAuthn API 와 서버 사이의 번역 (auth.md 3절).
 *
 * 규격은 `ArrayBuffer` 로 주고받고 JSON 은 문자열만 담는다. 그래서 서버가 준
 * 옵션의 몇몇 자리를 버퍼로 바꿔 넣고, 인증기가 준 응답의 버퍼를 다시
 * 문자열로 되돌려야 한다. **base64url 이다** — 일반 base64 로 다루면 `+`·`/`
 * 가 섞인 값에서만 이따금 깨진다.
 *
 * 최신 크로미움에는 `parseCreationOptionsFromJSON` 이 있지만 쓰지 않는다:
 * 있는 곳과 없는 곳의 두 경로가 생기고, 그러면 한쪽은 시험되지 않는다.
 */

/** 사람이 인증기를 만지는 것은 브라우저만 할 수 있다. */
export function isSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.PublicKeyCredential === 'function' &&
    !!navigator.credentials
  )
}

export function toBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/**
 * base64url → 바이트.
 *
 * `ArrayBuffer` 를 먼저 만들고 그 위에 뷰를 얹는다. `new Uint8Array(n)` 의
 * 버퍼 타입은 `ArrayBufferLike` 라 규격의 `BufferSource` 로 바로 안 들어간다.
 */
export function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4))
  const bytes = new Uint8Array(new ArrayBuffer(binary.length))
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}

interface JsonDescriptor {
  id: string
  type: string
  transports?: string[]
}

function descriptors(list: JsonDescriptor[] | undefined): PublicKeyCredentialDescriptor[] {
  return (list ?? []).map((item) => ({
    id: fromBase64Url(item.id),
    type: 'public-key' as const,
    ...(item.transports ? { transports: item.transports as AuthenticatorTransport[] } : {}),
  }))
}

/**
 * 인증기를 등록하고 응답을 문자열로 돌려준다.
 *
 * 사람이 취소하면 `null` 이다 — 오류가 아니다. 던지면 화면이 빨간 경고를
 * 띄우고, 사람은 자기가 누른 취소 때문에 무언가 잘못됐다고 읽는다.
 */
export async function createCredential(optionsJson: string): Promise<string | null> {
  const options = JSON.parse(optionsJson) as {
    challenge: string
    rp: PublicKeyCredentialRpEntity
    user: { id: string; name: string; displayName: string }
    pubKeyCredParams: PublicKeyCredentialParameters[]
    timeout?: number
    excludeCredentials?: JsonDescriptor[]
    authenticatorSelection?: AuthenticatorSelectionCriteria
    attestation?: AttestationConveyancePreference
  }

  const credential = await navigator.credentials
    .create({
      publicKey: {
        challenge: fromBase64Url(options.challenge),
        rp: options.rp,
        user: {
          id: fromBase64Url(options.user.id),
          name: options.user.name,
          displayName: options.user.displayName,
        },
        pubKeyCredParams: options.pubKeyCredParams,
        ...(options.timeout ? { timeout: options.timeout } : {}),
        excludeCredentials: descriptors(options.excludeCredentials),
        ...(options.authenticatorSelection
          ? { authenticatorSelection: options.authenticatorSelection }
          : {}),
        ...(options.attestation ? { attestation: options.attestation } : {}),
      },
    })
    .catch(asCancelled)
  if (!credential) return null

  const response = (credential as PublicKeyCredential)
    .response as AuthenticatorAttestationResponse
  return JSON.stringify({
    id: credential.id,
    rawId: toBase64Url((credential as PublicKeyCredential).rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64Url(response.clientDataJSON),
      attestationObject: toBase64Url(response.attestationObject),
      // 서버가 화면에 보여 줄 정보다. **타입은 항상 있다고 말하지만** 나중에
      // 추가된 메서드라 옛 사파리·파이어폭스에는 없다 — 없는 것을 부르면
      // TypeError 로 등록 전체가 죽는다. 그래서 확인하고 부른다.
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
      transports: response.getTransports?.() ?? [],
    },
    clientExtensionResults: (credential as PublicKeyCredential).getClientExtensionResults(),
  })
}

/** 등록한 인증기로 인증하고 응답을 문자열로 돌려준다. 취소면 `null`. */
export async function getAssertion(optionsJson: string): Promise<string | null> {
  const options = JSON.parse(optionsJson) as {
    challenge: string
    rpId?: string
    timeout?: number
    allowCredentials?: JsonDescriptor[]
    userVerification?: UserVerificationRequirement
  }

  const credential = await navigator.credentials
    .get({
      publicKey: {
        challenge: fromBase64Url(options.challenge),
        ...(options.rpId ? { rpId: options.rpId } : {}),
        ...(options.timeout ? { timeout: options.timeout } : {}),
        allowCredentials: descriptors(options.allowCredentials),
        ...(options.userVerification ? { userVerification: options.userVerification } : {}),
      },
    })
    .catch(asCancelled)
  if (!credential) return null

  const response = (credential as PublicKeyCredential).response as AuthenticatorAssertionResponse
  return JSON.stringify({
    id: credential.id,
    rawId: toBase64Url((credential as PublicKeyCredential).rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64Url(response.clientDataJSON),
      authenticatorData: toBase64Url(response.authenticatorData),
      signature: toBase64Url(response.signature),
      userHandle: response.userHandle ? toBase64Url(response.userHandle) : null,
    },
    clientExtensionResults: (credential as PublicKeyCredential).getClientExtensionResults(),
  })
}

/**
 * 사람이 취소한 것과 진짜 오류를 가른다.
 *
 * 취소·시간초과는 `NotAllowedError` 로 온다. 이 둘을 오류로 올리면 화면이
 * 빨간 경고를 띄우고, 사람은 자기가 누른 취소 때문에 무언가 망가졌다고 읽는다.
 */
function asCancelled(error: unknown): null {
  if (error instanceof DOMException && error.name === 'NotAllowedError') return null
  throw error
}
