# Experiencia técnica demostrable

## Presentación profesional

**Distributed Observability Platform — proyecto independiente de ingeniería de observabilidad.** Diseño e implementación de una plataforma para correlacionar logs, métricas y trazas de un flujo distribuido de activación de servicios, con ELK, OpenTelemetry y automatización de entrega en GitHub Actions.

Descripción reutilizable para portafolio o sección de proyectos:

> Diseñé e implementé una plataforma de observabilidad para tres microservicios, integrando Elasticsearch, Logstash, Kibana, Filebeat, Metricbeat y OpenTelemetry. Instrumenté trazas distribuidas y logs correlacionados, automaticé dashboards y políticas de retención, y desarrollé escenarios de incidentes para verificar latencia y fallos. Implementé reconciliación mediante comprobantes persistidos, con recuperación tras reinicios y prevención de efectos duplicados. Contenericé los servicios y configuré CI con pruebas, análisis de dependencias, SBOM y publicación de imágenes verificadas en GHCR.

## Capacidades verificables

| Capacidad | Evidencia |
| --- | --- |
| Diseño distribuido | Tres contratos HTTP, separación de roles y flujo de activación |
| Trazabilidad | Propagación W3C y comprobación E2E de una traza en tres servicios |
| Procesamiento de logs | JSON ECS, grok, tipos definidos y cuarentena |
| Instrumentación de métricas | Counter de peticiones e histograma de duración exportados por OTLP |
| Operación Elastic | Data streams, plantillas, ILM, data views y seis visualizaciones provisionadas |
| Resiliencia observable | Timeouts acotados, estado de reconciliación y escenarios de latencia/fallo |
| Persistencia y recuperación | Identidad estable, ledger SQLite WAL, comprobantes por servicio y reconciliación verificada tras reinicios |
| Seguridad | Credenciales aleatorias, mínimo acceso de ingestión y contenedores propios sin privilegios |
| Entrega | Lockfiles con hashes, CI Windows/Linux, prueba del stack y publicación sin reconstruir |

Las evidencias se consultan en las pruebas, informes E2E y ejecuciones de Actions. Este proyecto acredita trabajo técnico propio sobre un sistema sintético. No implica experiencia laboral para una empresa, un despliegue productivo ni resultados de disponibilidad medidos a largo plazo.
