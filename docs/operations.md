# Operación e investigación de incidentes

## Comprobar el estado

```powershell
docker compose ps -a
docker compose logs --tail 100 bootstrap collector apm-server logstash
Invoke-RestMethod http://127.0.0.1:18090/health/ready
```

El bootstrap debe terminar con código cero. Si falla, revisar su salida antes de reiniciarlo. Cambiar credenciales en `.env` exige actualizar los usuarios de Elasticsearch mediante bootstrap y recrear los servicios consumidores. No borrar volúmenes para resolver un problema de autenticación.

## Investigar una activación

1. Recuperar `X-Trace-ID` y `activation_id` de la respuesta.
2. En Discover, seleccionar `Platform logs` y buscar `trace.id: "<id>"`.
3. En APM, abrir `activation-api` y localizar la transacción correspondiente. El waterfall muestra spans clientes y operaciones de inventario y aprovisionamiento.
4. Filtrar `business.activation.id: "<id>"` en logs para relacionar señales de negocio.
5. Revisar `log.level: "error"`, `event.outcome: "failure"` y estado HTTP. Una activación 503 conserva su resultado y exige reconciliación; repetirla no ejecuta de nuevo las dependencias. Consultar el estado y ejecutar la reconciliación autenticada para buscar comprobantes autoritativos.

El escenario `latency` añade 350 ms a `provisioning.activate`. El escenario `failure` genera un 503 en aprovisionamiento y otro en la API, dejando evidencia en los spans y logs de ambas aplicaciones.

`response_loss` guarda el aprovisionamiento antes de devolver 503. La reconciliación consulta los dos servicios y distingue este caso de un efecto ausente: sólo el primero puede confirmarse como activo. Ver el [ejercicio reproducible de incidentes](incident-exercise.md).

## Consultas

KQL para errores:

```text
service.name: "activation-api" and http.response.status_code >= 500
```

ES|QL para resumen por servicio:

```text
FROM logs-platform-local
| WHERE @timestamp >= NOW() - 15 minutes AND http.response.status_code IS NOT NULL
| STATS requests = COUNT(*), latency_p95_ns = PERCENTILE(event.duration, 95) BY service.name
| SORT requests DESC
```

## Geolocalización

El pipeline GeoIP enriquece `source.geo` cuando la IP existe en la base de datos y la plantilla define `source.geo.location` como `geo_point`. En Kibana Maps, añadir una capa de documentos con el data view `Platform logs` y ese campo para inspeccionar orígenes. `scripts/seed-access.py` genera un evento sintético con IP pública para comprobar la integración; los logs reales del flujo usan `127.0.0.1` por privacidad. No atribuir esos puntos a usuarios o tráfico de una empresa.

## SLO y alertas locales

`python scripts/check-slo.py` evalúa cinco minutos de solicitudes de la API. Objetivos: disponibilidad de 99 % y latencia p95 menor o igual a 500 ms. Requiere al menos 20 solicitudes para declarar un resultado; con menos tráfico informa `insufficient_traffic`.

El comando escribe `artifacts/slo.json` y devuelve 2 ante incumplimiento, 0 cuando hay salud o tráfico insuficiente y error de ejecución ante una consulta fallida. Permite integrarse con un scheduler o sistema de notificaciones de cada entorno. No hay un envío de notificaciones externo ni una alerta nativa de Kibana activados por defecto.

```powershell
python scripts/traffic.py --requests 30 --scenario normal
python scripts/traffic.py --requests 5 --scenario failure
# Esperar a que los logs estén indexados antes de evaluar.
python scripts/check-slo.py
```

## Retención y recuperación

Consultar `GET /logs-platform-local/_ilm/explain` con autenticación para revisar rollover y eliminación. Los índices diarios de infraestructura tienen su propia política. APM conserva las políticas instaladas por Elastic; no hereda la retención personalizada de logs.

`docker compose down` detiene el stack y conserva volúmenes. `docker compose up -d --wait --wait-timeout 600` permite recuperarlo. No usar `down --volumes` si se necesita preservar telemetría y el ledger de idempotencia.

Para actualizar la aplicación, establecer `APP_IMAGE` con un digest validado, conservar el digest anterior y recrear los tres servicios. Un rollback restaura el digest previo; las migraciones de persistencia requieren revisión separada. Este proyecto no aplica cambios de esquema destructivos durante el arranque.

## Límites de capacidad

La red usa `10.203.74.0/24` para no depender de pools Docker agotados por otros proyectos. Si coincide con una ruta o red existente, definir `PLATFORM_SUBNET` en `.env` con un CIDR disponible antes de iniciar el stack.

Vigilar `docker stats`, espacio de volúmenes, colas Filebeat y Collector, y errores de exportación. Las colas son acotadas; una caída prolongada o el agotamiento de disco puede producir pérdidas. El bootstrap necesita acceso al registro de paquetes Elastic para instalar los assets APM. En un entorno aislado debe prepararse un registro o bundle compatible.
