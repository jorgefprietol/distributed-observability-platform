# Diseño de la plataforma

## Contexto y responsabilidad

Una solicitud de activación cruza la API, la evaluación de inventario y el aprovisionamiento. Un fallo en la última dependencia debe ser identificable desde la respuesta HTTP hasta la operación responsable, sin registrar credenciales ni información personal.

Cada servicio expone únicamente su contrato. La API guarda la clave de idempotencia, una huella del cuerpo, el ID estable, la solicitud y el resultado HTTP en SQLite con WAL. La identidad se persiste antes de invocar dependencias. La adquisición de una clave usa una restricción única. Una solicitud concurrente con resultado pendiente recibe 409; tras reiniciar permanece bloqueada hasta obtener evidencia suficiente.

Los errores de transporte, timeouts, respuestas no satisfactorias, JSON inválido o comprobantes que no coinciden producen `requires_reconciliation`. Inventario y aprovisionamiento tienen bases y volúmenes propios. El efecto sintético es una fila única por ID: se guarda junto con su comprobante en una única escritura y se rechaza reutilizar el ID con otro plan o región.

La reconciliación consulta mediante GET ambos comprobantes autenticados. Verifica ID, plan, región, estado y un único efecto; sólo entonces actualiza el ledger a activo y registra la evidencia en una transacción. Nunca vuelve a ejecutar efectos. Si un efecto falta, es inválido o no puede consultarse, el caso sigue bloqueado. Un error tardío de la solicitud original no puede sobrescribir un estado activo confirmado.

El escenario `response_loss` demuestra un efecto guardado cuyo acuse falla. Los comprobantes permiten recuperarlo incluso después de reiniciar las tres aplicaciones. El esquema anterior se migra agregando columnas y tablas; registros históricos sin identidad o solicitud no se completan por inferencia. Los efectos son sintéticos: un proveedor externo necesitaría idempotencia y comprobantes autoritativos propios; estas bases no crean una transacción distribuida.

## Trazas y correlación

La capa HTTP extrae el contexto W3C antes de abrir el span servidor. Cada llamada saliente crea un span cliente e inyecta el contexto vigente. Los spans de negocio incluyen el identificador de activación y dimensiones acotadas de plan y región. La respuesta expone la traza para consultar APM y Discover.

La telemetría automática del framework está deshabilitada para mantener una única instrumentación explícita y controlar las dimensiones y los datos capturados.

El OpenTelemetry Collector valida un bearer token, limita memoria, agrupa exportaciones y utiliza una cola persistente con reintentos hacia APM Server. Esta cola reduce pérdidas durante indisponibilidades temporales; no garantiza entrega ilimitada. Los SDK emplean buffers acotados y la instrumentación no bloquea el negocio esperando al backend.

La implementación usa los SDK upstream de OpenTelemetry y APM Server standalone de Elastic 8.19. La integración APM se instala antes de iniciar los emisores. Este modo permite controlar de forma explícita cada etapa del pipeline; futuras migraciones pueden adoptar Elastic Agent/EDOT según la compatibilidad del entorno.

## Logs y almacenamiento

Los servicios rotan sus logs locales. Filebeat mantiene estado y una cola en disco, y envía logs JSON y formato de acceso HTTP a Logstash. El pipeline normaliza fechas y tipos y enruta fallos de parseo hacia `logs-quarantine-local`. Los logs del JSON original contienen campos ECS y correlación de traza; el acceso HTTP utiliza rutas acotadas y una IP sintética.

Las plantillas definen tipos de timestamp, IDs, estado HTTP y duración antes de recibir datos. Los logs usan data streams con ILM: rollover después de un día o 1 GB por shard, y eliminación siete días después del rollover. Las métricas de infraestructura usan índices diarios con eliminación a los siete días de creación. Las políticas de los streams APM pertenecen a la integración Elastic y se gestionan por separado.

## Seguridad y operación

La API y el receptor OTLP usan credenciales diferentes. Elasticsearch conserva seguridad habilitada. La cuenta `kibana_system` conecta Kibana al clúster; `telemetry_ingest` puede escribir en los patrones de telemetría y consultar estado, pero no administrar usuarios. El bootstrap administrativo termina tras completar la provisión.

APM Server tiene permiso de lectura sobre el índice restringido `.apm-agent-configuration`, necesario para su caché de configuración central. Este permiso se limita a ese índice, conforme a los [roles de APM Server](https://www.elastic.co/docs/solutions/observability/apm/create-assign-feature-roles-to-apm-server-users).

Los servicios propios ejecutan UID 10001, sin capabilities, con raíz de sólo lectura, límites de procesos y recursos. Los puertos publicados son exclusivamente locales. La red interna usa HTTP; el perfil está destinado a operación en una estación de trabajo. El acceso por una red compartida requiere TLS y un gateway autenticado.

## Decisiones

| Decisión | Motivo | Límite |
| --- | --- | --- |
| Una imagen para tres roles | Reutilizar instrumentación y reducir variación del empaquetado | Cada servicio tiene configuración y proceso propios |
| Instrumentación explícita | Mostrar spans de negocio y controlar etiquetas | Nuevas operaciones deben instrumentarse |
| SQLite WAL por servicio | Persistencia, exclusión de claves y comprobantes durables | Un proceso por servicio; evolución a almacenamiento compartido para escalado |
| Captura de logs por archivos | Procesamiento Filebeat/Logstash sin montar el socket Docker | Comparte un volumen local de logs |
| Metricbeat sin privilegios de host | Métricas sin acceso al socket Docker | No representa una captura completa del host Windows |
| Dashboard importable | Provisión reproducible y revisión en Git | Cambios de versión deben validar migraciones de objetos Kibana |

Referencias técnicas: [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/exporters/), [SDK upstream con Elastic APM](https://www.elastic.co/guide/en/observability/8.19/apm-open-telemetry-direct.html), [instalación de la integración APM](https://www.elastic.co/guide/en/observability/8.19/get-started-with-fleet-apm-server.html).
