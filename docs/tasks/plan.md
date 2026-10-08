# Implementation Plan: Unificación del repositorio en un único microservicio `pdfextractor`

## Overview

El repositorio mezcla dos microservicios en un solo proyecto con identidad del orquestador: el
orquestador `src/paperextractor` (RFC 9457, streaming, cliente downstream) y el extractor
`src/pdfextractor` (FastAPI PDF → JSON). Objetivo: dejar **únicamente el microservicio
extractor** — un único directorio de código `pdfextractor`, sin código orquestador, sin
duplicidades (dos carpetas `contract/`, tests del orquestador incrustados), y con la identidad
del proyecto (pyproject, CI, docs, `.env.example`) renombrada a `pdfextractor`.

Contexto del sistema: tres microservicios (Orquestador, Persistencia, Extractor). Este
repositorio es el **Extractor**: componente tonto y especializado, cero disco, memoria
constante, sin bases de datos ni lógica de negocio.

## Línea de base (recuperación)

- El branch `main` fue `git reset --hard` a un commit pre-épica (`eccacee`), desapareciendo de
  los refs ~14 commits con todo `pdfextractor` + tests. Restaurado desde el commit huérfano
  **`64fc7d7`** (reflog `HEAD@{1}`): estado vigente = ambos microservicios + suite (324 tests).
- El plan previo de endurecimiento (§6.x) queda **superado** (decisión del usuario) y está
  archivado en `docs/tasks/archive/`.

## Architecture Decisions

- **AD1 — El repo contiene únicamente el extractor.** Se eliminan (quedan en git history):
  `src/paperextractor/`, `tests/unit/`, `tests/integration/`, `tests/harness.py`,
  `tests/fakes.py`, `tests/contract/`, `tests/extractor/contract/`, `docs/SPEC-paperextractor.md`.
- **AD2 — Un único árbol de código `src/pdfextractor`** con sus capas actuales
  (presentation/application/infrastructure). Tests unificados bajo `tests/extractor/`.
- **AD3 — `pyproject.toml`:** `name = "pdfextractor"`, wheel solo `["src/pdfextractor"]`,
  `known-first-party = ["pdfextractor"]`.
- **AD4 — Identidad coherente:** README, `.env.example` (bloque `PDFEXTRACTOR_*`), CI y docs
  sin rastro de `paperextractor`. Al final, `grep -r paperextractor` vacío.
- **AD5 — No hay código de bases de datos** en el repo (auditado: cero referencias a
  sqlite/postgres/mongo/alembic). Eliminable la preocupación de DB.

## Task List

### Paso 0: Recuperación ✅ (completado)
- [x] Restaurar la épica desde `64fc7d7`; validar con pytest (324 tests recopilados).
- [x] Archivar el plan previo en `docs/tasks/archive/`.

### Paso 1: Auditoría y Estructura (COMPLETADO)
- [x] **TASK-01: Test de identidad estructural/funcional (ROJO)** — el repo es
      únicamente el microservicio `pdfextractor`: ni `src/paperextractor/` ni sus tests/docs;
      `pyproject` se identifica como `pdfextractor`; la API levanta y responde el contrato
      básico con identidad extractor (`/health` → `service: pdfextractor`).
- [x] **TASK-02: Eliminar el orquestador** — borrar `src/paperextractor/`, `tests/unit/`,
      `tests/integration/`, `tests/harness.py`, `tests/fakes.py`, `tests/contract/`,
      `tests/extractor/contract/`, `docs/SPEC-paperextractor.md` → el test estructural
      y funcional del identidad vira a verde; la suite completa sigue verde. El assert de
      metadata (`pyproject name = pdfextractor`) queda rojo hasta TASK-03.

### Checkpoint CP-1: Estructura
- [x] Suite completa + ruff + mypy verdes; test de identidad verde; revisión con humano.

### Paso 2: Identidad del proyecto
- [x] **TASK-03: `pyproject.toml` → `pdfextractor`** (name, description, wheel packages,
      known-first-party) y `.env.example` con bloque `PDFEXTRACTOR_*`.
- [x] **TASK-04: CI** (`.github/workflows/ci.yml`) apuntando solo a `pdfextractor`.

### Checkpoint CP-2: Identidad
- [x] Build sano, `grep -r paperextractor` vacío, revisión con humano.

### Paso 3: Documentación, contrato y cierre
- [x] **TASK-05: Documentación** — README/docs de un único microservicio y contrato REST
      documentado (`POST /api/v1/extract` → 200 JSON estricto; errores en `{"error", ...}`).
- [x] **TASK-06: Verificación integral** — suite completa + lint + types + `-m memory` verdes.

### Checkpoint: Complete
- [x] Todas las acceptance criteria cumplidas; listo para PR único.

## Riesgos y Mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Reset/deriva del branch otra vez | Alto | Cada paso se commitea solo con visto bueno del humano (regla de pausa obligatoria) |
| El test de identidad pase por accidente al borrar de más | Medio | Assertions estructurales explícitas + suite completa en cada paso |
| El orquestador se necesite en el futuro | Bajo | Vive en git history (`origin/main`/`eccacee`); recuperable a voluntad |
| El test estructural dependa de `parents[N]` frágil del path | Bajo | `ROOT` derivado de `__file__` con una sola raíz `src`/`tests` |

## Open Questions

1. Re-aplicar el endurecimiento §6.x perdido (reset, sin commit): **parcialmente resuelto** — en
   TASK-06 el memory-gate de identidad zero-copy quedó rojo y se re-aplicó el ancho `bytes |
   bytearray` (puerto/servicio/adaptador/pool + ruta sin copia); `-m memory` verde (2/2).
2. ¿El directorio `graphify-out/` del repo es del usuario o generado? → No se toca.