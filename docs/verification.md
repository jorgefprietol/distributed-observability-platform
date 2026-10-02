# Verificación

## Criterios de aceptación

- Pruebas de contratos, autenticación, escenarios de fallo y persistencia de idempotencia.
- Una solicitud HTTP debe conservar el mismo `trace.id` en API, inventario y aprovisionamiento.
- Los logs de esas aplicaciones deben compartir la traza.
- Un fallo controlado debe producir 503, logs de error y spans con resultado fallido.
- Elasticsearch debe recibir métricas propias por OTLP y CPU por Metricbeat.
- Los eventos de acceso HTTP deben tener códigos numéricos después de grok; un fixture con IP pública debe enriquecerse por GeoIP y otro malformado debe llegar a cuarentena.
- Kibana debe tener tres data views y un dashboard de seis paneles.
- La cuenta de ingestión debe tener acceso de escritura sin poder administrar usuarios.
- Los logs deben estar gestionados por ILM.

El pipeline conserva JUnit, cobertura, `e2e.json`, estado Compose, diagnóstico de servicios, reporte de vulnerabilidades y SBOM como artifacts. Los resultados de una ejecución corresponden a su commit concreto; el badge del README muestra el estado de la rama principal.

La cobertura reportada corresponde a aplicación, configuración y persistencia. El módulo de exportación de telemetría queda fuera de la cifra de cobertura y se verifica mediante el stack real y pruebas de formato ECS.
