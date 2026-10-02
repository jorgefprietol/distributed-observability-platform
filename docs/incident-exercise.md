# Investigación de una activación incierta

## Objetivo y preparación

Determinar si un 503 representa un efecto ausente o un acuse perdido, usando trazas, logs y comprobantes durables. La recuperación debe conservar el ID de activación y evitar nuevas reservas o aprovisionamientos.

El stack debe estar activo y `ENABLE_FAULT_INJECTION=true`. Este ejercicio genera dos activaciones sintéticas y reinicia únicamente `activation-api`, `inventory` y `provisioning`; conservar todos los volúmenes.

```powershell
.\.venv\Scripts\python scripts/incident-drill.py --restart
```

El comando guarda `artifacts/incident-drill.json`. Para practicar el diagnóstico sin reinicios, omitir `--restart`. Actions ejecuta el ejercicio con reinicios antes de publicar la imagen.

## Caso 1: el efecto existe, pero su acuse falla

1. La solicitud con `scenario: response_loss` devuelve 503 y un `activation_id`. El ID y el cuerpo ya estaban guardados antes de contactar inventario.
2. En Discover, filtrar `trace.id` con `initial_trace_id` del informe. La traza muestra inventario confirmado y un 503 en aprovisionamiento; buscar el log `Service provisioned` del mismo ID antes del error HTTP. Esa secuencia propone la hipótesis de un acuse perdido, pero no basta por sí sola para confirmar el efecto.
3. Después del reinicio de las tres aplicaciones, la consulta de la API conserva la identidad y el resultado incierto. La reconciliación consulta ambos comprobantes mediante GET, comprueba que corresponden a la solicitud y registra `outcome: confirmed`.
4. Buscar `reconciliation_trace_id` en APM. El span `activation.reconcile` contiene consultas GET a los dos servicios, sin POST a sus operaciones de negocio.
5. La reconciliación devuelve 200; repetir la solicitud original devuelve 201 con `Idempotency-Replayed: true`. Cada comprobante indica `effect_count: 1`.

Conclusión esperada: el fallo estuvo en el acuse después de un commit durable. La activación puede confirmarse sin repetir el efecto.

## Caso 2: el efecto de aprovisionamiento falta

1. Una nueva solicitud con `scenario: failure` devuelve 503. Consultar la segunda traza del informe y los logs de aprovisionamiento.
2. La reconciliación obtiene `inventory: confirmed` y `provisioning: missing`; devuelve 409 y registra `outcome: blocked`.
3. Repetir la solicitud original conserva el 503 y el mismo ID. No se ejecuta otro aprovisionamiento.

Conclusión esperada: no hay prueba de un efecto aplicado en aprovisionamiento. Mantener el caso para una decisión operativa; el reconciliador no inventa un éxito ni ejecuta compensaciones.

## Evidencias y criterios de cierre

| Evidencia | Acuse perdido | Efecto ausente |
| --- | --- | --- |
| HTTP inicial | 503 | 503 |
| Comprobante de inventario | Confirmado | Confirmado |
| Comprobante de aprovisionamiento | Confirmado tras reiniciar | Ausente |
| Reconciliación | 200, activo | 409, bloqueado |
| Repetición con la clave original | 201, resultado persistido | 503, resultado incierto persistido |

Registrar ambos IDs y trazas, resultado de las consultas y explicación del fallo. No cerrar un caso sólo por un log de éxito: el cierre exige comprobantes compatibles y persistidos. Una dependencia no disponible o un comprobante incompatible conserva el bloqueo y el último intento de reconciliación para investigación.

Las pruebas automatizadas también simulan una terminación después de guardar ambos efectos y antes de guardar el resultado de la API, respuestas inválidas, migración de registros históricos y solicitudes concurrentes. El ejercicio usa filas SQLite como efectos de negocio sintéticos; una integración externa necesita evidencia equivalente proporcionada por el sistema que aplica el efecto.
