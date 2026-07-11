# TARS Local 0.3.0

Asistente táctico local para macOS con una interfaz web completa, conversación mediante Gemini u OpenAI, voz del navegador/macOS, cola de misiones, aprobaciones y operador web basado en Browser Use.

## Inicio rápido

1. Descomprimí la carpeta completa.
2. Hacé doble clic en `ABRIR_TARS.command`.
3. La primera ejecución prepara el entorno, instala Browser Use y Chromium, e inicia el servidor local.
4. Se abre automáticamente `http://127.0.0.1:8765`.

Si macOS bloquea el archivo:

```bash
chmod +x ABRIR_TARS.command scripts/*.command scripts/*.sh
./ABRIR_TARS.command
```

## La interfaz 0.3.0

El tablero ahora funciona como centro de control completo:

- Consola central de conversación con estado visual de TARS.
- Entrada por texto y micrófono.
- Nueva conversación e historial local.
- Agentes TARS, CASE y KIPP con estados en vivo.
- Misiones en cola, ejecutando, esperando aprobación, completadas o fallidas.
- Panel de aprobación humana para acciones sensibles.
- Actividad reciente y línea de eventos por misión.
- Resultados e informes terminados.
- Configuración de proveedor, modelos, claves, voz, humor y seguridad.
- Estado de Browser Use, UI-TARS, voz y Keychain.
- Botón global `DETENER TODO`.
- Diseño responsive para escritorio y móvil.

La interfaz no es un mockup aislado: consulta los endpoints reales del servidor Python y ejecuta las acciones disponibles en el backend.

## Primera configuración

1. Abrí **Ajustes**.
2. Elegí Google Gemini u OpenAI.
3. Pegá la API key y pulsá **Guardar**.
4. Pulsá **Probar conexión**.
5. Pulsá **Consultar modelos** y elegí uno habilitado en tu cuenta.
6. Guardá los cambios.
7. Probá la voz.
8. Volvé a **Control** y conversá con TARS.

La suscripción de ChatGPT o de la aplicación Gemini no reemplaza necesariamente una clave de API. La facturación de cada API se administra por separado.

## Órdenes y agentes

- Conversación normal: escribí directamente en la consola.
- Navegación web: empezá con `CASE:`.
- Trabajo técnico: empezá con `KIPP:`.

Ejemplo seguro:

```text
CASE: abrí wikipedia.org y decime el título de la página principal, sin iniciar sesión ni enviar formularios
```

### TARS

Conversación, análisis, coordinación, memoria de sesión y despacho de misiones.

### CASE

Navegación mediante Browser Use. En riesgo bajo funciona en modo lectura y tiene deshabilitadas por código las acciones de escritura, envío y carga.

### KIPP

Análisis técnico y propuestas de programación. En 0.3.0 todavía no modifica repositorios ni ejecuta comandos automáticamente.

## Voz

No requiere una API de voz adicional:

- Entrada: Web Speech API del navegador, idioma `es-AR`.
- Salida: voces disponibles en el navegador/macOS.
- Respaldo en macOS: comando nativo `say`.
- Emergencia: decir **detener**, **pará todo** o pulsar **DETENER TODO**.

Chrome suele ofrecer la compatibilidad más consistente. Según el navegador, el reconocimiento puede utilizar servicios del proveedor y no ser completamente local.

## Seguridad

- El servidor escucha sólo en `127.0.0.1`.
- Las claves se guardan en Keychain de macOS cuando está disponible.
- Las claves nunca se devuelven completas al navegador.
- Sólo se ejecuta una misión por vez.
- Formularios, cuentas, mensajes, publicaciones, cargas e instalaciones requieren aprobación.
- Compras, pagos, transferencias, cambios de contraseña, campañas y desactivación de seguridad están bloqueados.
- Cada misión corre en un proceso separado y puede terminarse.

## Diagnóstico

```bash
./scripts/doctor.sh
```

Pruebas automáticas:

```bash
python3 -m unittest discover -s tests -v
```

## UI-TARS opcional

Para instalar UI-TARS Desktop:

```bash
./scripts/install_ui_tars.sh
```

La interfaz detecta si está instalado, pero el puente para compartir pantalla y controlar aplicaciones de escritorio todavía no está conectado. Browser Use cubre la navegación web.

## Límites conocidos

- Las llamadas reales dependen de las API keys y modelos habilitados en tu cuenta.
- CAPTCHA, 2FA y defensas antibot pueden requerir intervención manual.
- El micrófono y Keychain necesitan validación final en tu Mac.
- KIPP no edita archivos automáticamente.
- El botón **Compartir pantalla** muestra el estado de UI-TARS, pero el control general del escritorio queda para la siguiente fase.

Consultá `TEST_REPORT.md` para el detalle de validación y `docs/INTERFACE.md` para la arquitectura visual.
