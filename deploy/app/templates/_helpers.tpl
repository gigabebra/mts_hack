{{- define "app.labels" -}}
app.kubernetes.io/name: hello
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
