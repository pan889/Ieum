{{/*
이름 만들기. 쿠버네티스 라벨은 63자까지라 잘라 준다 — 안 자르면 릴리스
이름이 긴 설치에서 매니페스트가 통째로 거절된다.
*/}}
{{- define "ieum.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ieum.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "ieum.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ieum.labels" -}}
helm.sh/chart: {{ include "ieum.chart" . }}
{{ include "ieum.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "ieum.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ieum.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ieum.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "ieum.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* 비밀이 담긴 Secret 의 이름. 기존 것을 가리키면 그것을 쓴다. */}}
{{- define "ieum.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "ieum.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
마이그레이션 훅이 쓰는 Secret 의 이름.

**훅은 일반 리소스보다 먼저 돈다.** `pre-install` 시점에는 차트가 만드는
`-secrets` 가 아직 없다 — 그것을 가리키면 첫 설치에서 Job 파드가 영영 못 뜬다
(`CreateContainerConfigError`). 그래서 값으로 받은 비밀은 훅 전용 사본
(`-migrate-secrets`)을 가리킨다. `existingSecret` 은 차트 밖에서 이미 만들어져
있으므로 그대로 가리킨다. 자세한 것은 `migrate-job.yaml` 머리 주석.
*/}}
{{- define "ieum.migrateSecretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-migrate-secrets" (include "ieum.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
마이그레이션 훅의 서비스 어카운트. 차트가 만드는 것은 훅보다 늦게 생기므로
같은 이유로 훅 전용 사본을 쓴다. 차트가 안 만드는 경우(`serviceAccount.create:
false`)는 이미 있는 것이라 그대로 쓴다.
*/}}
{{- define "ieum.migrateServiceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- printf "%s-migrate" (include "ieum.fullname" .) -}}
{{- else -}}
{{- include "ieum.serviceAccountName" . -}}
{{- end -}}
{{- end -}}

{{/*
ConfigMap 의 `data`. **본 ConfigMap 과 마이그레이션 훅의 사본이 같은 것을 편다**
— 두 벌을 손으로 적으면 한쪽만 고쳐지는 날이 온다.

차트가 정하는 키가 `config.extra` 보다 **앞선다.** 같은 키를 둘 다에 적으면
예전에는 YAML 에 같은 키가 두 번 찍혔다. `merge` 는 앞의 것을 남긴다.
*/}}
{{- define "ieum.configData" -}}
{{- $data := dict
  "IEUM_BASE_URL" .Values.config.baseUrl
  "IEUM_CORS_ORIGINS" .Values.config.webOrigins
  "IEUM_S3_PUBLIC_ENDPOINT_URL" .Values.config.s3PublicEndpointUrl
  "IEUM_S3_BUCKET" .Values.config.s3Bucket
  "IEUM_S3_REGION" .Values.config.s3Region
  "IEUM_SEARCH_BACKEND" .Values.search.backend
  "IEUM_TRUSTED_PROXY_HOPS" .Values.config.trustedProxyHops
-}}
{{- with .Values.config.s3EndpointUrl -}}
{{- $_ := set $data "IEUM_S3_ENDPOINT_URL" . -}}
{{- end -}}
{{- if eq .Values.search.backend "opensearch" -}}
{{- $_ := set $data "IEUM_OPENSEARCH_URL" .Values.search.opensearch.url -}}
{{- $_ := set $data "IEUM_OPENSEARCH_INDEX" .Values.search.opensearch.index -}}
{{- $_ := set $data "IEUM_OPENSEARCH_ANALYZER" .Values.search.opensearch.analyzer -}}
{{- with .Values.search.opensearch.username -}}
{{- $_ := set $data "IEUM_OPENSEARCH_USERNAME" . -}}
{{- end -}}
{{- end -}}
{{- $data = merge $data .Values.config.extra -}}
{{- range $key, $value := $data }}
{{ $key }}: {{ $value | toString | quote }}
{{- end -}}
{{- end -}}

{{/*
값으로 받은 비밀(`existingSecret` 이 빌 때)의 `stringData`. 본 Secret 과
마이그레이션 훅의 사본이 같이 쓴다. 고르는 값은 **있을 때만** 넣는다 —
`existingSecret` 에 그 키가 없는 것과 같은 모양이 되어야 `optional` 참조가
양쪽에서 똑같이 돈다.
*/}}
{{- define "ieum.secretData" -}}
{{- $data := dict
  "IEUM_SECRET_KEY" .Values.secrets.secretKey
  "IEUM_DATABASE_URL" .Values.secrets.databaseUrl
  "IEUM_REDIS_URL" .Values.secrets.redisUrl
-}}
{{- with .Values.secrets.opensearchPassword -}}
{{- $_ := set $data "IEUM_OPENSEARCH_PASSWORD" . -}}
{{- end -}}
{{- with .Values.secrets.s3AccessKey -}}
{{- $_ := set $data "IEUM_S3_ACCESS_KEY" . -}}
{{- end -}}
{{- with .Values.secrets.s3SecretKey -}}
{{- $_ := set $data "IEUM_S3_SECRET_KEY" . -}}
{{- end -}}
{{- with .Values.secrets.seedAdminPassword -}}
{{- $_ := set $data "SEED_ADMIN_PASSWORD" . -}}
{{- end -}}
{{- $data = merge $data .Values.secrets.extra -}}
{{- range $key, $value := $data }}
{{ $key }}: {{ $value | toString | quote }}
{{- end -}}
{{- end -}}

{{- define "ieum.apiImage" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{- define "ieum.webImage" -}}
{{- printf "%s:%s" .Values.web.image.repository (default .Chart.AppVersion .Values.web.image.tag) -}}
{{- end -}}

{{/*
API·워커가 함께 쓰는 환경. **비밀은 값으로 넣지 않는다** — Secret 을
가리키기만 한다. 값으로 넣으면 `kubectl describe pod` 에 그대로 찍힌다.
*/}}
{{- define "ieum.env" -}}
{{- include "ieum.envFor" (dict "root" . "secretName" (include "ieum.secretName" .)) -}}
{{- end -}}

{{/*
`ieum.env` 의 본체. 가리킬 Secret 이름을 받는다 — 마이그레이션 훅은 본 Secret
이 아니라 훅 전용 사본을 가리켜야 하기 때문이다(`ieum.migrateSecretName`).
*/}}
{{- define "ieum.envFor" -}}
{{- $root := .root -}}
{{- $secret := .secretName -}}
- name: IEUM_ENV
  value: production
- name: IEUM_DEBUG
  value: "false"
- name: IEUM_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: IEUM_SECRET_KEY
- name: IEUM_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: IEUM_DATABASE_URL
- name: IEUM_REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: IEUM_REDIS_URL
{{- /*
스토리지 열쇠. **`optional` 이다** — IAM 역할(IRSA·워크로드 아이덴티티)로
붙는 S3 는 열쇠가 없고, 앱은 빈 열쇠를 받으면 boto 의 기본 자격 증명 체인으로
간다(`core/storage.py`). 없을 때 파드가 안 뜨게 하면 그 설치가 막힌다.
*/}}
{{- range $key := list "IEUM_S3_ACCESS_KEY" "IEUM_S3_SECRET_KEY" }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ $key }}
      optional: true
{{- end }}
{{- if eq $root.Values.search.backend "opensearch" }}
- name: IEUM_OPENSEARCH_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: IEUM_OPENSEARCH_PASSWORD
      # 비밀번호를 요구하지 않는 클러스터도 있고, `existingSecret` 에 이 키가
      # 없을 수도 있다. `optional` 이 아니면 그때 파드가 아예 안 뜬다.
      optional: true
{{- end }}
{{- /* 위에서 이미 넣은 이름은 건너뛴다. 같은 이름이 두 번이면 뒤의 것이 앞의 것을 가린다. */}}
{{- $taken := list "IEUM_ENV" "IEUM_DEBUG" "IEUM_SECRET_KEY" "IEUM_DATABASE_URL" "IEUM_REDIS_URL" "IEUM_S3_ACCESS_KEY" "IEUM_S3_SECRET_KEY" "IEUM_OPENSEARCH_PASSWORD" }}
{{- range $key, $_ := $root.Values.secrets.extra }}
{{- if not (has $key $taken) }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ $key }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
설정 검사. **뜨기 전에 거절한다.**

빠뜨리면 조용히 잘못 도는 값들이다: 화면 주소가 없으면 앱은 개발 기본값으로
떠서 메일 링크·SSO 의 돌아올 주소가 localhost 를 가리키고, CORS 오리진이
없으면 브라우저가 API 를 부르는 족족 프리플라이트에서 막혀 앱이 통째로 안
뜨고, 스토리지 공개 주소가 없으면 아무도 못 여는 첨부 링크를 계속 발급한다.
컴포즈 쪽도 같은 것을 `${VAR:?...}` 로 막고 있다(`deploy/compose/prod.yml`).
*/}}
{{- define "ieum.require" -}}
{{- if not .Values.config.baseUrl -}}
{{- fail "config.baseUrl 이 필요하다. 사람이 브라우저에 치는 주소다 (예: https://ieum.example.com). 없으면 앱이 개발 기본값(http://localhost:5173)으로 떠서 메일 안의 링크와 SSO 의 돌아올 주소가 전부 localhost 를 가리킨다." -}}
{{- end -}}
{{- if not .Values.config.webOrigins -}}
{{- fail "config.webOrigins 가 필요하다. 브라우저가 앱을 여는 주소다 (예: https://ieum.example.com). 없으면 CORS 가 모든 요청을 막는다." -}}
{{- end -}}
{{- if not .Values.config.s3PublicEndpointUrl -}}
{{- fail "config.s3PublicEndpointUrl 이 필요하다. 브라우저가 첨부를 여는 주소다 (예: https://files.example.com). 없으면 아무도 못 여는 링크를 발급한다." -}}
{{- end -}}
{{- if eq .Values.search.backend "opensearch" -}}
{{- if not .Values.search.opensearch.url -}}
{{- fail "search.backend 를 opensearch 로 두면 search.opensearch.url 이 필요하다. 없으면 앱은 멀쩡히 뜨고 검색만 조용히 0건이 된다." -}}
{{- end -}}
{{- end -}}
{{- if not .Values.secrets.existingSecret -}}
{{- if not .Values.secrets.secretKey -}}
{{- fail "secrets.existingSecret 이나 secrets.secretKey 가 필요하다. 운영에서는 existingSecret 을 쓴다 — values 에 적으면 그 파일이 비밀 창고가 된다." -}}
{{- end -}}
{{- if not .Values.secrets.databaseUrl -}}
{{- fail "secrets.databaseUrl 이 필요하다. 이 차트는 Postgres 를 만들지 않는다 (README 참조)." -}}
{{- end -}}
{{- if not .Values.secrets.redisUrl -}}
{{- fail "secrets.redisUrl 이 필요하다. 이 차트는 Redis 를 만들지 않는다 (README 참조)." -}}
{{- end -}}
{{- end -}}
{{- end -}}
