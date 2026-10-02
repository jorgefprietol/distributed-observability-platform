# Contrato HTTP

`POST /api/v1/activations` requiere `Authorization: Bearer <API_TOKEN>` y `Idempotency-Key` de 8 a 80 caracteres alfanuméricos, guion o guion bajo.

```json
{"plan":"fiber-100","region":"north","scenario":"normal"}
```

Planes: `fiber-100`, `fiber-500`. Regiones: `north`, `south`. Escenarios: `normal` (por defecto), `latency`, `failure`, `response_loss`. Los campos adicionales se rechazan. Los escenarios diferentes a `normal` requieren `ENABLE_FAULT_INJECTION=true`. `response_loss` devuelve 503 después de guardar el efecto de aprovisionamiento para simular un acuse perdido.

| Respuesta | Significado |
| --- | --- |
| 201 | Activación terminada; incluye `activation_id`, `status: active` y plan |
| 401 | Credencial ausente o inválida |
| 403 | Escenario de fallo deshabilitado |
| 409 | Clave con otro cuerpo o solicitud pendiente de reconciliación |
| 422 | Entrada fuera de contrato o clave ausente/inválida |
| 503 | Fallo de dependencia; incluye `activation_id` y `status: requires_reconciliation` |

Una repetición con clave y cuerpo iguales devuelve el resultado persistido y añade `Idempotency-Replayed: true`. Una reconciliación confirmada actualiza ese resultado de 503 a 201; una reconciliación bloqueada conserva el resultado anterior. La traza de la repetición es independiente salvo que el cliente conserve su `traceparent`. `X-Trace-ID` permite buscar cada petición.

## Consulta y reconciliación

Ambas operaciones requieren el mismo bearer token y una clave de 8 a 80 caracteres válida:

- `GET /api/v1/activations/{key}` devuelve ID estable, solicitud guardada, estado, resultado y última evidencia de reconciliación. Una clave desconocida devuelve 404.
- `POST /api/v1/activations/{key}/reconcile` no recibe cuerpo. Consulta los comprobantes de inventario y aprovisionamiento mediante GET. Sólo devuelve 200 y `status: active` si ambos confirman el mismo ID, plan, región, estado esperado y `effect_count: 1`. Guarda el resultado activo y la evidencia en una transacción local.
- Si falta evidencia o una dependencia no responde, devuelve 409 con `status: requires_reconciliation` y evidencia `missing`, `unavailable`, `invalid` o `unknown`. No ejecuta reservas ni aprovisionamiento.
- Una activación que ya está activa devuelve su resultado sin consultar dependencias. Las solicitudes antiguas sin ID/solicitud persistidos se mantienen para investigación manual; no se adivina su identidad.

Un estado `pending` indica una solicitud reclamada sin resultado definitivo persistido. Después de una terminación inesperada puede reconciliarse si existen ambos comprobantes; de lo contrario sigue bloqueada y se conserva el registro del intento.

Inventario acepta `POST /internal/reservations`; aprovisionamiento acepta `POST /internal/provisions`. Ambos reciben el cuerpo de activación más un `activation_id` generado por la API y requieren autenticación. Sus puertos permanecen en la red Compose.

Los efectos sintéticos se almacenan una sola vez por ID. Un mismo ID con otro plan o región devuelve 409. `GET /internal/reservations/{activation_id}` y `GET /internal/provisions/{activation_id}` devuelven comprobantes autenticados con plan, región, estado y `effect_count`; 404 significa que ese servicio no tiene un efecto confirmado. Cada servicio conserva su propia base en un volumen durable.

`GET /health/live` informa del proceso. `GET /health/ready` confirma configuración y acceso a la base local. Estos endpoints no trazan peticiones ni verifican dependencias: la disponibilidad de los downstream se observa en el flujo funcional. No existe un endpoint `/metrics`; las métricas de aplicación se exportan mediante OTLP.
