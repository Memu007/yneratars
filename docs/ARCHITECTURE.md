# Arquitectura

```text
Navegador local
  ├─ Chat + voz Web Speech
  ├─ Configuración y Keychain
  └─ Cola / aprobaciones
          ↓ HTTP local
Servidor Python estándar
  ├─ SQLite: misiones, eventos, conversaciones
  ├─ Adaptador Gemini generateContent
  ├─ Adaptador OpenAI Responses
  └─ Ejecutor de un trabajo
          ↓ proceso separado
  ├─ Worker de conversación
  └─ Worker CASE / Browser Use / Chrome
```

## Decisiones

- Python estándar para que el tablero arranque aun cuando Browser Use falle.
- Browser Use como dependencia opcional y aislada.
- Proceso por misión para permitir detención real.
- Keychain antes que `.env`.
- Voz local antes que Gemini Live: menos costo y menos superficie de error.
- UI-TARS reservado para aplicaciones de escritorio; no se mezcla todavía con la navegación web.
