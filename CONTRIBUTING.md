# Contribuir

Los cambios deben describir el contrato afectado, su impacto operacional y la evidencia de verificación. Ejecutar lint, formato y pruebas con las dependencias bloqueadas. Los cambios de instrumentación, pipelines, mapeos o dashboards requieren además la verificación E2E del stack.

Para actualizar dependencias, modificar `requirements.in` o `requirements-dev.in` y regenerar ambos lockfiles con `uv pip compile --python-version 3.12 --universal --generate-hashes`. Revisar la diferencia, auditar y verificar en Linux y Windows.

El generador `scripts/build-dashboard.py` es la fuente de los assets Kibana. Ejecutarlo después de modificar visualizaciones y versionar el NDJSON resultante.
