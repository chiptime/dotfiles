# Guía de Configuración de AI Quotas (`config.json`)

Esta guía explica de forma muy sencilla y paso a paso cómo personalizar el panel de **AI Quotas** usando el archivo de configuración `config.json`, **sin necesidad de saber programar ni tocar código**.

---

## 1. ¿Dónde está el archivo y cómo se edita?

El archivo se encuentra en tu carpeta personal en la siguiente ruta:
```text
~/.config/ai-quotas/config.json
```

### ¿Cómo editarlo?
Puedes abrirlo con cualquier editor de texto que te guste:
```bash
# Ejemplo con nano (en terminal):
nano ~/.config/ai-quotas/config.json

# O con VS Code / tu editor gráfico:
code ~/.config/ai-quotas/config.json
```

### ⚡ ¡Sin reiniciar el ordenador ni el servidor!
Una vez que guardes los cambios en el archivo, simplemente **recarga la página del navegador** (`F5` o `Ctrl+R`) en `http://127.0.0.1:47623` y verás los cambios aplicados inmediatamente.

---

## 2. El archivo completo de un vistazo

Este es el aspecto que tiene el archivo:

```json
{
  "order": [
    "claude",
    "zai",
    "chatgpt",
    "opencode",
    "gemini",
    "gemini-3p",
    "deepseek"
  ],
  "capacity": {
    "zai": 20.0,
    "claude": 1.0,
    "gemini": 1.0,
    "gemini-3p": 1.0
  },
  "file_providers": [
    { "id": "gemini", "display_name": "Google Gemini" },
    { "id": "gemini-3p", "display_name": "Antigravity (Claude)" }
  ],
  "main_labels": [
    "5h window",
    "weekly",
    "monthly",
    "daily",
    "3p 5h window",
    "3p weekly"
  ],
  "weekly_labels": {
    "gemini-3p": "3p weekly"
  }
}
```

---

## 3. ¿Qué significa cada parte? (Explicación para no técnicos)

### 📌 `order` — El orden de las columnas en pantalla
Define **qué proveedor va primero, cuál segundo, etc.** de izquierda a derecha en tu pantalla.

* Los nombres que pongas arriba en la lista aparecerán más a la izquierda.
* Cualquier proveedor que tengas y **no esté en esta lista**, no se pierde: simplemente aparecerá al final de todo, ordenado por orden alfabético.

---

### 📌 `file_providers` — Tarjetas fijas (aunque no tengan datos)
Normalmente, una IA solo aparece en el panel si hay un archivo con sus datos. Pero, ¿qué pasa si quieres tener su casilla fija en la pantalla para saber que existe, aunque hoy no la hayas usado todavía?

Aquí es donde defines esas casillas fijas:
* `"id"`: El identificador técnico en minúsculas (ej: `"gemini"`, `"mistral"`).
* `"display_name"`: El nombre bonito y con mayúsculas que saldrá escrito en la tarjeta grande (ej: `"Google Gemini"`, `"Mistral Le Chat"`).

> **Efecto:** Si no hay ningún archivo de datos en tu ordenador para esa IA, en lugar de no salir nada, el panel te mostrará una tarjeta limpia que pone **"Sin datos"** con su nombre bonito.

---

### 📌 `main_labels` — Tarjetas VIP vs "Otras cuotas"
Cada IA puede tener varias cuotas (ej: cuota de 5 horas, cuota semanal, saldo en dólares, etc.).

* Las cuotas cuyos nombres estén en **`main_labels`** se mostrarán arriba, bien grandes y visibles (en la fila principal).
* Las cuotas que tengan cualquier otro nombre que **no** esté aquí se guardarán abajo en una sección desplegable llamada *"Otras cuotas"* para no estorbar.

---

### 📌 `capacity` — Peso para el ritmo de gasto
En la parte superior del panel hay una barra de ritmo ("Ritmo semanal: gasta las más atrasadas") que te avisa de qué cuota estás desaprovechando.

Si una suscripción es mucho más cara o tiene mucha más capacidad que otra (por ejemplo, Z.ai que da 20 veces más cuota que una suscripción estándar), le pones un número mayor aquí (ej: `"zai": 20.0`) para que el panel te recuerde gastar antes esa cuota para no tirar dinero.

---

### 📌 `weekly_labels` — Nombre de tu cuota semanal
El panel calcula el ritmo semanal buscando una cuota que se llame `"weekly"`. Si algún proveedor tuyo la llama de forma diferente (como Antigravity que la llama `"3p weekly"`), aquí le dices: *"para este proveedor, su cuota semanal es esta"*.

---

## 4. Recetas prácticas (Ejemplos paso a paso)

### Receta 1: Quiero que ChatGPT aparezca el primero de todos a la izquierda

1. Abre `~/.config/ai-quotas/config.json`.
2. En la lista `"order"`, mueve `"chatgpt"` a la primera posición:
```json
  "order": [
    "chatgpt",
    "claude",
    "zai",
    "opencode",
    "gemini",
    "gemini-3p",
    "deepseek"
  ]
```
3. Guarda el archivo y recarga la web con `F5`. ¡Listo!

---

### Receta 2: Quiero añadir una nueva IA (ej. Mistral) y que aparezca en el panel

Imagina que quieres añadir Mistral:
1. En `"file_providers"`, añade una nueva línea con su ID y su nombre bonito:
```json
  "file_providers": [
    { "id": "gemini", "display_name": "Google Gemini" },
    { "id": "gemini-3p", "display_name": "Antigravity (Claude)" },
    { "id": "mistral", "display_name": "Mistral Le Chat" }
  ]
```
2. Si además quieres que aparezca en una posición concreta de la pantalla, añádelo en `"order"`:
```json
  "order": [
    "claude",
    "mistral",
    "chatgpt"
  ]
```
3. Guarda el archivo. Aunque todavía no hayas configurado ningún script para Mistral, ya verás su tarjeta en el panel diciendo **"Sin datos"** esperando a que le envíes información.

---

### Receta 3: Mi script usa un nombre raro como "Ventana 4 horas" y me sale abajo en "Otras cuotas"

Si tu tarjeta se va abajo a *"Otras cuotas"* porque tiene un nombre que el sistema no reconoce:
1. Mira el nombre exacto de la tarjeta (ejemplo: `"Ventana 4h"`).
2. Añádelo en `"main_labels"` (en minúsculas):
```json
  "main_labels": [
    "5h window",
    "weekly",
    "monthly",
    "daily",
    "ventana 4h"
  ]
```
3. Guarda y recarga. La tarjeta subirá automáticamente a la fila principal.

---

## 5. Cuidados al escribir el archivo (Errores comunes)

El formato JSON es muy estricto con la puntuación:

1. **Las comas (`,`):**
   * Debe haber una coma entre cada elemento.
   * **No pongas coma después del último elemento** de una lista o bloque:
   ```json
   // ❌ MAL (coma en el último):
   ["claude", "gemini", ]

   // ✅ BIEN:
   ["claude", "gemini"]
   ```
2. **Las comillas:**
   * Usa siempre comillas dobles `"texto"`, nunca comillas simples `'texto'`.
3. **Mayúsculas y minúsculas:**
   * Los `id` de los proveedores siempre deben ser en minúsculas (ej: `"gemini"`, `"claude"`).
   * Los nombres bonitos (`display_name`) pueden llevar las mayúsculas, espacios y símbolos que quieras (ej: `"Antigravity (Claude)"`).
