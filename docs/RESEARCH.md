# Investigación técnica utilizada

Revisión realizada el 10 de julio de 2026.

## Modelos y APIs

- La documentación oficial de Gemini enumera `gemini-3.5-flash` como modelo estable de la familia Flash. TARS Local lo utiliza como valor inicial, pero el tablero permite consultar y seleccionar los modelos habilitados en la cuenta del usuario.
- La documentación oficial de OpenAI enumera `gpt-5.6-luna` como la variante económica de GPT-5.6. Se usa como valor inicial, con selector de modelos disponible desde el tablero.
- La conversación textual usa `generateContent` para Gemini y Responses API para OpenAI.
- La voz inicial usa capacidades del navegador/macOS para reducir costo y complejidad. Gemini Live u OpenAI Realtime pueden incorporarse después como módulos opcionales.

## Operador web

- Browser Use requiere Python 3.11 o superior.
- El proyecto fija `browser-use==0.13.3`, la versión estable publicada que se auditó para este paquete.
- Se verificaron en el código oficial las interfaces utilizadas por el worker: `Agent`, `Tools(exclude_actions=...)`, `ChatGoogle`, `ChatOpenAI`, `BrowserProfile`, `BrowserSession` y `Agent.run(max_steps=...)`.
- El comando oficial `browser-use install` instala Chromium y las dependencias necesarias mediante Playwright.

## Escritorio

- UI-TARS Desktop puede operar navegador y computadora, pero necesita permisos gráficos de macOS y una prueba en la máquina real.
- La documentación oficial advierte que las primeras pruebas son más confiables con un solo monitor.

## Fuentes oficiales

- https://ai.google.dev/gemini-api/docs/models
- https://developers.openai.com/api/docs/models
- https://github.com/browser-use/browser-use
- https://github.com/bytedance/UI-TARS-desktop

TARS Local no copia código de estos proyectos. Browser Use se consume como dependencia y UI-TARS se instala por separado.
