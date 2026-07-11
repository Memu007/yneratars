# Informe de validación — TARS Local 0.3.0

Fecha: 10 de julio de 2026

## Resultado

**56 de 56 pruebas automáticas aprobadas.**

La versión fue validada como núcleo local, servidor HTTP e interfaz web. Las pruebas no utilizan claves reales ni ejecutan operaciones sobre cuentas del usuario.

## Pruebas automáticas

Cobertura principal:

- Configuración, validación y permisos de archivos.
- Carga segura de `.env`.
- Keychain/almacenamiento local de secretos sin devolver valores.
- Evaluación de riesgo, negaciones y elevación automática.
- Bloqueo de compras, pagos y credenciales.
- Aprobaciones, rechazo, detención y reintento.
- Una sola misión activa.
- Recuperación después de cierres inesperados.
- Terminación de procesos hijos.
- Salida de worker superior a 2 MB sin bloqueo.
- Proveedores Gemini y OpenAI con respuestas simuladas.
- Contrato de Browser Use 0.13.3.
- Endpoints de estado, tablero, actividad, mensajes y conversaciones.
- Persistencia del historial de chat.
- Presencia de las cinco vistas del producto.
- Cabeceras locales y bloqueo de host no permitido.

Comando:

```bash
python3 -m unittest discover -s tests -v
```

Resultado:

```text
Ran 56 tests
OK
```

## Validación estática

- `python3 -m compileall`: aprobado.
- `node --check static/app.js`: aprobado.
- `bash -n` sobre scripts de inicio e instalación: aprobado.
- 116 identificadores HTML únicos; 0 duplicados.
- Llaves CSS equilibradas: 466 aperturas y 466 cierres.
- Marcadores funcionales comprobados: dashboard, historial, voz, detención global y despacho CASE/KIPP.

## Prueba HTTP real

Se levantó el servidor con Python y se verificó:

- `GET /api/health` → versión 0.3.0.
- `GET /api/dashboard` → salud, estadísticas, misiones, actividad, secretos enmascarados y estado del ejecutor.
- `GET /api/messages` sin `conversation_id` → rechazo 400 correcto.
- Interfaz HTML y recursos estáticos servidos localmente.

## Prueba visual en Chromium

La interfaz se renderizó en Chromium headless con respuestas API controladas para reproducir estados reales:

- Escritorio: 1440 × 1100.
- Móvil: 390 × 844.
- Vista Control.
- Vista Misiones.
- Vista Ajustes.
- Apertura y cierre del diálogo de nueva misión.

Resultado:

- 0 errores de consola.
- 0 excepciones de página.
- 0 desbordes horizontales.
- Navegación entre vistas correcta.
- Misiones, aprobaciones, agentes, actividad y conversación renderizados.

Las capturas de control se generaron fuera del paquete para no incluir datos simulados en la distribución final.

## Correcciones encontradas durante la auditoría

- Se agregó estado operativo agregado para evitar múltiples consultas inconsistentes.
- Se incorporó historial de conversación al iniciar la interfaz.
- Se corrigió el cambio entre proveedores para no sobrescribir el modelo del proveedor anterior.
- Se protegió el uso de síntesis de voz en navegadores sin esa API.
- Se validó la interfaz sin IDs repetidos ni ancho excedente en móvil.

## Lo que requiere la Mac del usuario

No puede validarse dentro de este entorno:

- API keys reales y modelos habilitados en las cuentas.
- Micrófono físico y permisos del navegador.
- Voces instaladas en macOS.
- Keychain real de macOS.
- Browser Use con Chrome visible y páginas externas.
- UI-TARS con Accesibilidad y Grabación de pantalla.

## Evaluación

La versión 0.3.0 está lista para una primera prueba local seria. El centro de control y sus acciones están conectados al backend disponible. Los ajustes esperables después de la prueba en Mac se limitan principalmente a proveedores, permisos del sistema y compatibilidad de voz/navegador.
