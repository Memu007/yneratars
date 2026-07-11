# Arquitectura de voz

## Objetivo

TARS debe funcionar en Brave sin depender de Web Speech API y ofrecer un modo manos libres continuo.

## Flujo Realtime

1. `static/voice.js` solicita el micrófono con `getUserMedia`.
2. El navegador crea una oferta WebRTC y la envía como `application/sdp` a `/api/voice/realtime/session`.
3. `app/voice.py` crea la sesión en OpenAI usando la clave almacenada en Keychain.
4. El navegador recibe audio remoto y eventos por el data channel.
5. Semantic VAD decide cuándo terminó el turno y permite interrupciones.
6. Las herramientas de voz despachan misiones a la misma API local y conservan las reglas de riesgo.

## Fallback

`MediaRecorder` captura un turno y un analizador Web Audio detecta el silencio. `/api/voice/transcribe` utiliza OpenAI o Gemini; la respuesta vuelve al chat normal. `/api/voice/tts` genera audio natural cuando hay una clave de OpenAI, y la UI conserva `speechSynthesis`/`say` como último respaldo.

## Seguridad

- Las API keys nunca se envían al navegador.
- Los endpoints de voz exigen `X-TARS-Client: dashboard` y host loopback.
- El SDP está limitado a 1 MB; el audio a 25 MB; TTS a 8.000 caracteres.
- Las herramientas de voz no eluden la evaluación de riesgo ni las aprobaciones.
- Compras, pagos, transferencias, contraseñas y desactivación de seguridad continúan bloqueados.

## Prueba manual mínima

1. Configurar una clave OpenAI.
2. Seleccionar `Natural · Realtime` y `Marin`.
3. Activar manos libres.
4. Decir: “Dame el estado del sistema”.
5. Interrumpir la respuesta diciendo: “Pará, mejor decime cuántas misiones hay”.
6. Decir: “CASE, abrí example.com y decime el título”.
7. Confirmar que aparece una misión y que CASE no puede saltarse aprobaciones.
