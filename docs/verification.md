# Verificación

Las 22 pruebas automatizadas pasan en Windows y Linux, con 98,36 % de cobertura sobre aplicación, configuración y persistencia. La prueba E2E del stack también pasó en runners hospedados y en Docker Desktop.

La [ejecución de referencia](https://github.com/jorgefprietol/distributed-observability-platform/actions/runs/37041870034) verificó las tres aplicaciones bajo una misma traza, logs correlacionados, cinco eventos de error del incidente controlado, 14 eventos de acceso HTTP, métricas de CPU y métricas de aplicación. El escaneo bloqueante pasó, generó el SBOM y publicó una imagen pública con atestación de procedencia. Los números de eventos son observaciones de esa ejecución, no benchmarks ni resultados productivos.

También se revisaron visualmente los seis paneles de Kibana, incluida la conversión de duración a milisegundos. Después de generar 30 activaciones normales y ejecutar los escenarios E2E, el evaluador SLO detectó una ventana con incidentes: `status=breach` y código de salida `2`. El reporte quedó en `artifacts/slo.json`; sus valores dependen del tráfico y recursos de esa ejecución.

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
