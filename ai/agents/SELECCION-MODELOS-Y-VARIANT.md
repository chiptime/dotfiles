# Guía Arquitectónica: Selección de Modelos, Razonamiento (`variant`) y Calibración en OpenCode

Documento de referencia técnica sobre cómo se dimensiona la potencia de un modelo de lenguaje en entornos de desarrollo asistido por agentes, el impacto del parámetro `variant` (esfuerzo de razonamiento o *reasoning effort*), y la matriz de decisión para elegir y calibrar modelos según el rol.

---

## 1. Fundamentos: ¿Qué es realmente el `variant` (Reasoning Effort)?

En proveedores modernos (OpenAI o-series / Sol / Luna / Terra, GLM-Thinking, DeepSeek-R1, Claude Thinking, Gemini Thinking), los modelos no sólo generan texto hacia el usuario o llamadas a herramientas; disponen de una fase interna previa de **Test-Time Compute** (cadena de pensamiento o *Chain-of-Thought* / CoT).

En OpenCode, la propiedad `variant` (`low`, `medium`, `high`, `max` o `""`/default) mapea directamente este presupuesto:

- **`variant: ""` (Default / Dinámico)**:
  - **Mecánica**: El modelo usa su heurística nativa sin forzar techos ni suelos artificiales. Evalúa la complejidad del prompt y decide dinámicamente cuántos tokens de pensamiento emitir antes de cerrar el bloque `</think>`.
  - **Comportamiento**: Un saludo o una decisión directa consume 500-1.500 tokens de pensamiento y responde en 1-3 segundos. Un problema denso expande el razonamiento a demanda.
- **`variant: "low"`**:
  - **Mecánica**: Restringe severamente el presupuesto de CoT (típicamente < 1.500 tokens).
  - **Comportamiento**: Respuesta casi instantánea. Evita divagaciones.
- **`variant: "high"` / `variant: "max"`**:
  - **Mecánica**: Eleva drásticamente el umbral de exploración interno (Beam Search / MCTS), forzando al modelo a generar entre 8.000 y 16.000+ tokens de pensamiento aunque la respuesta final sea de una sola línea.
  - **Comportamiento**: Alta latencia (20-60s por turno) y consumo exponencial de cuota.

---

## 2. La Falacia del "Modelo Mini en Max" y el Fenómeno de Overthinking

Existe una intuición común pero errónea: *"Si tengo un modelo ligero y barato (como GPT-6 Luna, GLM-5.3-Flash o Haiku), ponerle `variant: "max"` lo convertirá en un modelo Frontier ahorrando dinero"*.

### ¿Por qué esto falla técnicamente?
1. **Capacidad de representación base vs. Cómputo en inferencia**:
   - Un modelo ligero tiene una densidad de parámetros y una ventana de abstracción comprimida.
   - Si no comprende intrínsecamente un patrón arquitectónico complejo o una regla sutil, **darle más tiempo para pensar sólo hace que recorra en bucle las mismas hipótesis limitadas**. No genera magia deductiva nueva.
2. **Overthinking en Agentic Tool Calling (Decisión de herramientas)**:
   - A diferencia de resolver un problema matemático cerrado (donde más pensamiento casi siempre mejora el resultado), en la **orquestación con herramientas** (`task`, `read`, `edit`, `git`), un `variant: "max"` induce entropía:
     - El modelo empieza a plantearse dudas contrafácticas: *"¿Y si el usuario quería X en vez de Y?", "¿Y si el subagente falla?", "¿Debería leer este otro directorio antes de delegar?"*.
     - Resultado: **Parálisis por análisis**, bucles de autoverificación y llamadas erróneas o retrasadas a herramientas.
3. **Quema de Cuotas Semanales**:
   - Planes como Z.ai Coding Plan o límites semanales de tokens contabilizan los tokens de razonamiento (`reasoning_tokens`) como tokens de salida.
   - En una sesión típica de 25 turnos interactivos, un orquestador en `max` puede consumir fácilmente más de 300.000 tokens solo en "pensar qué herramienta llamar", agotando la cuota en horas.

---

## 3. Matriz de Roles: Dónde forzar `variant` y dónde dejar `default`

La regla de oro de la arquitectura de agentes es: **los nodos hoja ejecutan y profundizan; los nodos intermedios coordinan y fluyen**.

| Rol en el Sistema | Naturaleza del Trabajo | Variant Recomendado | Justificación Técnica |
|---|---|---|---|
| **Orquestador (`gentle-orchestrator`, Root)** | Coordinación, diálogo, ruteo ODD, delegación | **`default` (`""`)** o **`low`** | Debe responder rápido, seguir esquemas de herramientas de forma estricta y deliberar contigo sin latencias de 40s ni bucles internos. |
| **Writer de Código (`general`, `sdd-apply`)** | Implementación, lógica, tipos, tests | **`high` / `max`** *(en modelos medianos/grandes)* o **`default`** | Tiene `task: deny` (no puede delegar ni hablarte). Vuelca todo el cómputo en escribir código robusto y contemplar edge cases en un solo pase. |
| **Explorador / Lector (`explore`, `sdd-explore`)** | Mapeo de repositorios, lectura AST, call paths | **`default` (`""`)** | La tarea es recuperar y sintetizar contexto. Forzar CoT pesado no aporta nada a la extracción de imports y llamadas. |
| **Auditor de Riesgo / Adversarial (`review-risk`, `jd-judge`)** | Threat modeling, condiciones de carrera, refutación | **`high`** | Aquí sí es crítico el pensamiento profundo para detectar vulnerabilidades sutiles, suposiciones falsas y vectores de ataque. |
| **Tareas Mecánicas (`branch-pr`, `issue-creation`, `git`)** | Formateo estricto, conventional commits, metadatos | **`low`** | Tareas 100% deterministas. Forzar `low` garantiza ejecución en 1 segundo con consumo testimonial de tokens. |

---

## 4. Cómo detectar qué tipo de modelo tienes y qué variant asignarle

Antes de configurar un modelo en OpenCode, clasifícalo en una de estas tres familias:

### Familia A: Modelos Frontier Densos (Claude 3.5/3.7/5.5 Sonnet & Opus, GPT-6.1 Sol, GPT-5.6 Terra)
- **Características**: Gran capacidad de generalización, disciplina estricta de esquemas y comprensión profunda de arquitectura.
- **Uso ideal**:
  - Como **Orquestador**: con `variant: ""` (`default`). Brillan en deliberación arquitectónica contigo sin atascarse.
  - Como **Writer**: con `variant: "high"`. Escriben código limpio, idiomático y testeado a la primera.

### Familia B: Modelos Ligeros / Rápidos (GLM-5.3-Flash, GPT-6 Luna, DeepSeek Flash, Gemini Flash)
- **Características**: Diseñados para baja latencia, alto throughput y coste mínimo.
- **Uso ideal**:
  - Como **Orquestador**: únicamente con `variant: ""` (`default`) o `variant: "low"`.
  - Como **Operadores Mecánicos**: con `variant: "low"`.
  - **Peligro**: **NUNCA** configurar con `high` o `max`. Se degradan en rendimiento, latencia y devoran la cuota semanal.

### Familia C: Modelos Especializados de Razonamiento Puro (DeepSeek-R1, o3-mini-high, GLM-Thinking puro)
- **Características**: Creados específicamente para matemáticas, algoritmos densos y refutación lógica.
- **Uso ideal**:
  - Como **Jueces adversariales** (`jd-judge`, `review-refuter`) o **Fixers de bugs de concurrencia** (`jd-fix-agent`).
  - **Peligro**: Evitar totalmente en el rol de orquestador interactivo principal debido a la latencia y verbosidad del pensamiento.

---

## 5. Resumen Práctico de Configuración

1. **Si vas a deliberar y coordinar**:
   - Usa un modelo Frontier o intermedio balanceado (`gpt-6.1-sol`, `claude-sonnet-5.5`, `glm-5.3-flash`).
   - Mantén `variant: ""` (`default`).
2. **Si vas a delegar escritura pesada de código**:
   - Asigna un modelo Frontier al subagente ejecutor (`general`).
   - Sube la variante a `high` para asegurar que refine tests y edge cases antes de entregar.
3. **Si el modelo es ligero (Flash / Luna)**:
   - Déjalo en `default` o fuérzalo a `low`. Forzar `max` es contraproducente.
