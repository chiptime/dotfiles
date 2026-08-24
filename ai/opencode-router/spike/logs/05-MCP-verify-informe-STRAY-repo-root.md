# Informe de Uso de Herramientas MCP

## 1. Resultado de `codegraph_explore` (query: `'mobile-proxy main'`)

- **Estado**: Disponible y ejecutada exitosamente.
- **Resumen**: Devolvió **48 símbolos** distribuidos en **2 archivos** principales, además del análisis de impacto / blast radius.
- **Archivos y símbolos principales devueltos**:
  - `src/mobile-proxy.ts`:
    - Constantes y configuración: `UPSTREAM`, `COPILOT_BACKEND`, `PORT`, `CACHE_BUST`, `MOBILE_CSS`.
    - Funciones y lógica de servidor: `detectActiveProfile`, `proxyServerError`, punto de entrada con `Bun.serve` en `import.meta.main` despachando a `handleProxyRequest`.
  - `antivirus/scan-file.py`:
    - Funciones: `_emit_report`, `_base_report`, `_env_str`, `_env_int`, `run_scan`, `main`.
    - Constantes y variables: `SCHEMA`, `DEFAULT_HOST`, `DEFAULT_MAX_SIZE`, códigos de salida (`EXIT_CLEAN`, `EXIT_INFECTED`, etc.) y tabla `_G3_EXIT`.
  - **Blast radius / dependencias detectadas**:
    - `proxyServerError` (`src/mobile-proxy.ts:1371`)
    - `MOBILE_CSS` (`src/mobile-proxy.ts:787`)
    - `main` en `antivirus/scan-ebook.py:148`, `scripts/r2-upload.py:17` y `antivirus/scan-file.py:294`.

## 2. Resultado de `mem_search` (query: `'antigravity spike'`)

- **Estado**: Disponible y ejecutada exitosamente.
- **Proyecto**: `ai-stack`
- **Memorias encontradas (1 resultado)**:
  - **ID**: `#7298`
  - **Título**: `Skill local antigravity-explore + wrapper AGY_EXTRA_DIRS`
  - **Tipo**: `config`
  - **Detalle**: Creación de la skill local de proyecto `.opencode/skills/antigravity-explore/SKILL.md` (exploración técnica vía `agy`) y extensión del wrapper `antigravity-spike/run-antigravity-task.sh` con la variable `AGY_EXTRA_DIRS` para incluir directorios extra en `--add-dir`.

## 3. Disponibilidad de herramientas

- `codegraph_explore` (Servidor `codegraph`): **Disponible** (sin errores).
- `mem_search` (Servidor `engram`): **Disponible** (sin errores).
