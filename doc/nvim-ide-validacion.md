# Checklist de validación — Entorno IDE Neovim

Validación interactiva del change `nvim-ide-environment` (P0 vtsls + P1 terminales/bufferline + P2 commit IA). Los 4 items marcados ⏳ son los PENDING-USER del verify-report; el resto re-valida en vivo lo que ya pasó headless.

**Cómo usarlo:** abrí nvim en tu repo TS más pesado y recorré en orden. Marcá ✅ o anotá el error exacto (`:messages`, `:LspInfo`). Al final hay plan de rollback por slice.

---

## 0. Preparación (una vez)

- [ ] Abrir nvim en el repo de aceptación TS (el más pesado/diario)
- [ ] `:Lazy` → sync limpio, sin plugins fallidos (toggleterm y bufferline presentes)
- [ ] `:Mason` → `vtsls` instalado
- [ ] `:TSInstallInfo` → `typescript` y `tsx` instalados
- [ ] Pulsar `<leader>` y esperar → which-key muestra los grupos; **no deben aparecer** `tt`, `tT`, `tc` bajo el grupo `t`

## 1. ⏳ P0 — Paridad TypeScript (vtsls)

- [ ] Abrir un `.tsx` → `:LspInfo` muestra cliente **vtsls** adjunto
- [ ] Inlay hints visibles (tipos de parámetros/retorno inline, sin pulsar nada)
- [ ] `gd` sobre un símbolo importado → salta a la definición en otro archivo
- [ ] `gD` → declaración
- [ ] `gr` → lista de referencias
- [ ] `gi` → implementación
- [ ] `K` sobre una variable/función → hover con tipos completos
- [ ] `<leader>rn` sobre una función → rename propaga a todos los usos
- [ ] `<leader>ca` con el cursor sobre un error → code action / quick fix
- [ ] En insert, escribir un símbolo no importado → completion ofrece **auto-import** (al aceptar agrega el import)
- [ ] `[d` / `]d` navegan diagnósticos; `<leader>cd` abre el float

**Umbral de éxito (spec):** paridad plena con VSCode — si gd/rename/hover/completion responden sin lag perceptible, P0 vale.

## 2. Búsqueda y navegación (preexistente — depende de vtsls para símbolos)

- [ ] `<leader>ff` → buscar archivos (con iconos git)
- [ ] `<leader>fg` → live grep de texto
- [ ] `<leader>fb` → listar buffers abiertos
- [ ] `<leader>fs` → símbolos del documento actual
- [ ] `<leader>fS` → símbolos del workspace (con vtsls deben aparecer los TS)
- [ ] `<leader>e` → explorer / `<leader>E` → reveal del archivo actual
- [ ] `<leader>fo` → open editors
- [ ] `<leader>fG` → grug-far (search & replace del IDE)
- [ ] `[b` / `]b` → ciclar editores centrales
- [ ] `<leader>m` → maximize/restore del panel

## 3. ⏳ P1 — Terminales (toggleterm + perfiles)

Creación y ciclo:
- [ ] Al abrir nvim: **cero terminales** corriendo (no auto-start)
- [ ] `<leader>tn` → crea la primera terminal (única visible)
- [ ] `<leader>tn` de nuevo → segunda terminal; sigue habiendo UNA visible
- [ ] `<leader>t]` / `<leader>t[` → cicla t1→t2→t1; nunca se ven dos ventanas de terminal
- [ ] `<leader>to` → oculta la visible; `to` de nuevo → reaparece
- [ ] Ocultar/mostrar es **silencioso**: sin notificaciones ni mensajes

Persistencia del proceso:
- [ ] Levantar el dev server en una terminal (`pnpm dev` / `npm run dev`)
- [ ] `to` para ocultar → `to` para volver → el server sigue corriendo (mismo puerto, HMR vivo)

Perfil por repo:
- [ ] `<leader>tp` en el repo (sin perfil) → abre shell simple, no bloquea
- [ ] Crear `.nvim/terminals.json` en la raíz del repo:
```json
{
  "version": 1,
  "terminals": [
    { "name": "web", "cmd": "pnpm dev" },
    { "name": "tests", "cmd": "pnpm test --watch" }
  ]
}
```
- [ ] `<leader>tp` → levanta ambas terminales con sus comandos, una visible
- [ ] JSON roto (borrar una comilla) → `tp` lo trata como ausente (shell) + un solo WARN

Limpieza y layout:
- [ ] `<leader>tt`, `<leader>tT`, `<leader>tc` y `:Terminal` → **no existen** (nada pasa)
- [ ] `<leader>tq` → cierra y mata la terminal actual
- [ ] `<leader>ts` → picker de terminales (nota: con cero terminales el comportamiento depende de toggleterm — suggestion pendiente)
- [ ] Con el sidebar abierto (`<leader>e`): togglear terminales varias veces → el ancho del sidebar y el editor central no cambian
- [ ] En terminal-mode: `<Esc><Esc>` → vuelve a normal-mode

## 4. ⏳ Bufferline (tabs)

- [ ] Línea de buffers visible arriba
- [ ] Click en un tab → navega al buffer
- [ ] Cerrar un tab **sin** cambios → cierra directo
- [ ] Cerrar un tab **con** cambios sin guardar → pide confirmación (nunca pierde silencioso)
- [ ] Con el sidebar abierto → la línea respeta el offset (no se dibuja debajo del árbol)
- [ ] La línea refleja lo mismo que `<leader>fb`

## 5. Git (preexistente — flujo 2 de la migración)

Hunks y blame (gitsigns):
- [ ] `]h` / `[h` → siguiente/anterior hunk modificado
- [ ] `<leader>gp` → preview del hunk
- [ ] `<leader>gs` → stage del hunk (probar también en visual, seleccionando)
- [ ] `<leader>gu` → deshacer stage del hunk
- [ ] `<leader>gr` → reset del hunk
- [ ] `<leader>gb` → blame flotante de la línea
- [ ] `<leader>gB` → toggle blame en columna virtual (estilo GitLens)
- [ ] `<leader>gD` → diff del archivo actual
- [ ] `<leader>gQ` → hunks al quickfix
- [ ] `<leader>gt` → mostrar líneas borradas

Diff review e histórico (diffview):
- [ ] `<leader>gd` → diff review completo (panel de archivos + diff lado a lado)
- [ ] `<leader>gH` → histórico del archivo actual (equivalente al Timeline de agy)
- [ ] `<leader>gq` → cerrar diffview
- [ ] `<leader>gC` → panel Source Control (neogit)
- [ ] `<leader>gG` → git graph (flog)

## 6. ⏳ P2 — Commit con IA (`<leader>gc`)

- [ ] Stagear un hunk con `<leader>gs` → `<leader>gc` → mensaje conventional en inglés aparece en el buffer COMMIT_EDITMSG
- [ ] Editar el mensaje a gusto → `:wq` → `git log -1` muestra el commit con tu texto editado (solo lo staged, aunque el worktree esté sucio)
- [ ] Buffer abierto → `:q` → descarta; el índice queda intacto (nada commiteado)
- [ ] Sin nada staged → `<leader>gc` → aviso y no abre nada
- [ ] (Opcional) Diff gigante: stagear >64KB → el mensaje se genera del resumen stat+headers (menos detalle, pero funciona)
- [ ] (Opcional) Sin red/backend de opencode caído → `<leader>gc` → aviso y abort limpio

## 7. Formato y lint (preexistente)

- [ ] `<leader>cf` → formatea el buffer TS
- [ ] `<leader>cl` → lint del buffer
- [ ] Variantes (`cF`, `cL`) → verificar significado exacto con which-key (`<leader>` + `c`)

## 8. Cierre — la motivación original

- [ ] Cerrar Antigravity y VSCode → comparar RAM (Task Manager en Windows, `free -h` en WSL)
- [ ] Trabajar una semana completa sin abrir las GUIs → si no hay fricción real, el criterio de éxito (paridad funcional de los 4 flujos) se cumple y podés desinstalarlas

---

## Si algo falla

1. Anotá el item, el error exacto y `:messages` + `:LspInfo` si aplica
2. Rollback por slice (cada uno es independiente):
   - P0: `git revert 25697a9` (vuelve a ts_ls)
   - P1: `git revert 40de1fb 0c6f697` (vuelve a las terminales de usar-y-tirar)
   - P2: `git revert 182fe21` (quita `<leader>gc`)
3. Los perfiles `.nvim/terminals.json` no guardan estado — borrarlos no rompe nada

## Referencia rápida — keymaps nuevos de este cambio

| Keymap | Acción |
|---|---|
| `<leader>tp` | Levantar perfil de terminales del repo (sin perfil → shell) |
| `<leader>to` | Toggle terminal visible |
| `<leader>tn` | Nueva terminal |
| `<leader>t]` / `<leader>t[` | Ciclo siguiente/anterior terminal |
| `<leader>tq` | Cerrar terminal actual |
| `<leader>ts` | Picker de terminales |
| `<leader>gc` | Generar mensaje de commit con opencode (requiere staging) |
