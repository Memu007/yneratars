# Modelo de seguridad

## Bloqueado

Compras, pagos, transferencias, tarjetas, cuentas bancarias, contraseñas, desactivación de seguridad, campañas publicitarias y borrado fuera del proyecto.

## Requiere aprobación

Enviar formularios, correos o mensajes; publicar; subir archivos; instalar software; iniciar sesión o editar archivos importantes.

## Automático

Lectura y navegación pública, búsqueda, extracción, conversación y creación de resultados dentro del sistema.

## Claves

1. Variables de entorno, cuando existen.
2. Keychain de macOS.
3. Archivo local `config/.secrets.json` con permisos `0600` como respaldo fuera de macOS.

Nunca se devuelven claves completas por la API local.
