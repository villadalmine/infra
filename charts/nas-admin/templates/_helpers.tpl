{{- define "nas-admin.fullname" -}}
{{- .Release.Name }}
{{- end }}

{{- define "nas-admin.labels" -}}
app.kubernetes.io/name: nas-admin
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "nas-admin.selectorLabels" -}}
app.kubernetes.io/name: nas-admin
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Port exposed by the service — 4180 when oauth2-proxy is in front, 8080 otherwise */}}
{{- define "nas-admin.servicePort" -}}
{{- ternary 4180 8080 (eq .Values.auth.mode "oidc") }}
{{- end }}
