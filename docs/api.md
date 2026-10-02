# Contrato HTTP

`POST /api/v1/activations` requiere `Authorization: Bearer <API_TOKEN>` y `Idempotency-Key` de 8 a 80 caracteres alfanuméricos, guion o guion bajo.

```json
{"plan":"fiber-100","region":"north","scenario":"normal"}
```

Planes: `fiber-100`, `fiber-500`. Regiones: `north`, `south`. Escenarios: `normal` (por defecto), `latency`, `failure`. Los campos adicionales se rechazan. Los escenarios diferentes a `normal` requieren `ENABLE_FAULT_INJECTION=true`.

| Respuesta | Significado |
| --- | --- |
| 201 | Activación terminada; incluye `activation_id`, `status: active` y plan |
| 401 | Credencial ausente o inválida |
| 403 | Escenario de fallo deshabilitado |
| 409 | Clave con otro cuerpo o solicitud pendiente de reconciliación |
| 422 | Entrada fuera de contrato o clave ausente/inválida |
| 503 | Fallo de dependencia; incluye `activation_id` y `status: requires_reconciliation` |

Una repetición con clave y cuerpo iguales conserva el código y cuerpo originales y añade `Idempotency-Replayed: true`. La traza de la repetición es independiente salvo que el cliente conserve su `traceparent`. `X-Trace-ID` permite buscar cada petición.

Inventario acepta `POST /internal/reservations`; aprovisionamiento acepta `POST /internal/provisions`. Ambos reciben el cuerpo de activación más un `activation_id` generado por la API y requieren autenticación. Sus puertos permanecen en la red Compose.

`GET /health/live` informa del proceso. `GET /health/ready` confirma configuración y acceso a la base local. Estos endpoints no trazan peticiones ni verifican dependencias: la disponibilidad de los downstream se observa en el flujo funcional. No existe un endpoint `/metrics`; las métricas de aplicación se exportan mediante OTLP.
