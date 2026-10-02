{{- define "gateway.parents" -}}
- group: gateway.networking.k8s.io
  kind: Gateway
  name: gateway
  sectionName: web
{{- if .Values.tls.enabled }}
- group: gateway.networking.k8s.io
  kind: Gateway
  name: gateway
  sectionName: websecure
{{- end }}
{{- end -}}

{{- define "gateway.backends" -}}
- group: ""
  kind: Service
  name: hello
  port: 80
  weight: {{ .Values.traffic.stableWeight }}
- group: ""
  kind: Service
  name: hello-canary
  port: 80
  weight: {{ .Values.traffic.canaryWeight }}
{{- end -}}
