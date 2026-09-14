# Approved format example — week 13–17/8/2026

Final version approved by the user. Replicate this style exactly.

```tsv
Fecha	Tarea	Horas Estimadas	Horas Reales con la IA	IA	Modelo usado	Comentarios
13/8/2026	Desarrollo de herramienta interna para entornos de trabajo paralelos	20	6	Codex	GPT-5.5	Planificación + ejecución autónoma
13/8/2026	Organización del backlog de mantenimiento post-vacaciones	5	2	Codex	GPT-5.5	Volcado del correo de pendientes a lista priorizada
14/8/2026	Análisis y corrección de incidencia en el envío de correos a entidades	24	13	Codex	GPT-5.5	Tarea principal de la semana; horas extra
15/8/2026	Pruebas de envío masivo de correos (incidencia)	24	8	Codex	GPT-5.5	Jornada de sábado
16/8/2026	Pruebas reales de lotes de 100 envíos	12	6	Codex	GPT-5.5	Domingo, tras aplicar los cambios en la plataforma
17/8/2026	Traslado del envío a la nueva plataforma y validación final	16	12	Codex	GPT-5.5	100% de correos entregados; cierre de la incidencia
17/8/2026	Soporte y gestión de REUS	4	4	Codex	GPT-5.5	Recurrente durante la semana
17/8/2026	Revisión del trabajo acumulado en repositorios	6	2	Codex	GPT-5.5	Solo consulta
```

Totales: 111h estimadas vs 53h reales.

## Style checklist

- [ ] Tarea = one line, verb first, functional outcome (what the business gets)
- [ ] No jargon: never SDD, backoff, worktree, refactor, MCP, API, retry
- [ ] Comentarios ≤ 10 words, casual, can repeat between rows
- [ ] Hours: `6` or `6,5`, never `6:30`
- [ ] Real hours of a full day ≈ 8 + extras; a dominant task absorbs most of the day
- [ ] Estimated ≈ 2–4x real, plausible for the task type
- [ ] IA = Codex, Modelo = GPT-5.5 (single, unless user overrides)
- [ ] TSV block pasteable, header optional
