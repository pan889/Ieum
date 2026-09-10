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
- name: IEUM_ENV
  value: production
- name: IEUM_DEBUG
  value: "false"
- name: IEUM_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "ieum.secretName" . }}
      key: IEUM_SECRET_KEY
- name: IEUM_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "ieum.secretName" . }}
      key: IEUM_DATABASE_URL
- name: IEUM_REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "ieum.secretName" . }}
      key: IEUM_REDIS_URL
{{- if eq .Values.search.backend "opensearch" }}
- name: IEUM_OPENSEARCH_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "ieum.secretName" . }}
      key: IEUM_OPENSEARCH_PASSWORD
      # 비밀번호를 요구하지 않는 클러스터도 있고, `existingSecret` 에 이 키가
      # 없을 수도 있다. `optional` 이 아니면 그때 파드가 아예 안 뜬다.
      optional: true
{{- end }}
{{- range $key, $_ := .Values.secrets.extra }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ include "ieum.secretName" $ }}
      key: {{ $key }}
{{- end }}
{{- end -}}

{{/*
설정 검사. **뜨기 전에 거절한다.**

빠뜨리면 조용히 잘못 도는 값들이다: CORS 오리진이 없으면 브라우저가 API 를
부르는 족족 프리플라이트에서 막혀 앱이 통째로 안 뜨고, 스토리지 공개 주소가
없으면 아무도 못 여는 첨부 링크를 계속 발급한다. 컴포즈 쪽도 같은 것을
`${VAR:?...}` 로 막고 있다(`deploy/compose/prod.yml`).
*/}}
{{- define "ieum.require" -}}
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
