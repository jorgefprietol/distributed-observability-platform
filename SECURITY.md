# Seguridad

Reportar vulnerabilidades mediante un aviso privado en GitHub Security Advisories. No publicar credenciales, datos personales o detalles explotables en issues públicos.

El perfil Compose es local. Para redes compartidas, configurar TLS, secretos externos y roles por servicio antes de desplegar. El bootstrap requiere credenciales administrativas únicamente durante la provisión. Las imágenes Elastic tienen ciclo de actualización propio; el escaneo de CI se aplica a la imagen de aplicación construida por este repositorio.

Los datos de negocio son sintéticos. Los logs omiten credenciales, cuerpos, query strings y direcciones reales de cliente. Mantener `.env` fuera del repositorio y controlar sus permisos en el sistema anfitrión.
