# Decisiones del Hub — cerradas por Bruno (2026-09-14)

Complementa `MAPA_INGESTAS.md` (relojes) y `doc/control-hub-architecture.md` (ADR).
Fuente viva del roadmap: PRD `prd/hub-integration` (Engram, project ai-stack).

## Decisión 1 — Disparadores de triaje del drain-inbox · **CERRADA**

Qué hace que el drain-inbox abra pregunta por Telegram:

1. **Proyecto nuevo** detectado como `uncatalogued` por el sensor.
2. **Discrepancia de estado**: el `status_local` difiere de la nota del hub.
3. **Trabajo zombi**: dirty > 20 ficheros sostenido durante una semana.

Cadencia (matiz de Bruno): **casi diaria, según entren datos no clasificables** —
el drain ya corre a diario 07:30; si un día no entra nada clasificable, no pregunta.

## Decisión 2 — Precedencia del espejo hub→dashboard · **CERRADA**

- **El estado del hub manda** siempre que el proyecto tenga nota.
- `status_local` (projects.yaml) se pinta **solo como fallback** cuando no existe nota.
- Nunca se muestran ambos como autoridad; la caché local deja de mantenerse via
  convención una vez el proyecto tiene nota del hub.

## Implementación

Ambas corresponden a las Fases 2 y 3 del PRD `prd/hub-integration` (sesión ai-stack).
Los disparadores los consume el drain-inbox; la precedencia, el renderizador del
dashboard (`scripts/projects-html.py`) cuando exista `hub-state.json`.
