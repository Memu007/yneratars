# Diseño de interfaz — TARS Local 0.3.0

## Objetivo

Transformar el tablero técnico anterior en un centro de control operativo, sin separar la estética de las funciones reales.

## Estructura

### Control

- Métricas del servidor, misión activa, aprobaciones y tokens de sesión.
- Estado visual de TARS: online, escuchando, pensando, hablando, trabajando o error.
- Conversación e historial local.
- Micrófono, manos libres y comandos rápidos.
- Agentes, aprobaciones y actividad reciente.
- Vista resumida de la cola.

### Misiones

- Filtros por estado.
- Búsqueda por título, agente, modelo o descripción.
- Crear, aprobar, rechazar, detener y reintentar.
- Progreso visual y detalle completo.
- Línea temporal de eventos.

### Agentes

- Capacidades y límites de TARS, CASE y KIPP.
- Estado operativo en tiempo real.
- Integraciones: Browser Use, UI-TARS, voz y Keychain.

### Resultados

- Misiones completadas, fallidas o bloqueadas.
- Resultado o error asociado.
- Contador de aplicaciones ejecutables, preparado para la fase de workspace de KIPP.

### Ajustes

- Proveedor y modelos.
- Gestión segura de API keys.
- Voz, velocidad y manos libres.
- Humor y personalidad.
- Cola, CASE, dominios permitidos y reglas de seguridad.

## Arquitectura técnica

```text
HTML/CSS/JavaScript
        ↓ HTTP local
Servidor Python
        ├─ /api/dashboard
        ├─ /api/chat
        ├─ /api/messages
        ├─ /api/missions
        ├─ /api/activity
        ├─ /api/settings
        └─ /api/providers
        ↓
SQLite + ejecutor de misiones + proveedores + Browser Use
```

## Decisiones

- Sin Electron durante esta fase.
- Sin bibliotecas de frontend ni recursos externos.
- Interfaz responsive y autocontenida.
- Actualización por polling conservador cada 2,5 segundos.
- Todos los textos se insertan con `textContent` para evitar HTML inyectado.
- Componentes no disponibles se muestran como no conectados; no se simulan acciones.
