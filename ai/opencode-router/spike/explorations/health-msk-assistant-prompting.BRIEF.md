Investiga en internet y produce una exploracion tecnica sobre COMO DISEÑAR el prompting de un asistente de IA personal que ayude con temas de salud, con foco en tendinitis, dolores articulares y molestias musculoesqueleticas cronicas.

CONTEXTO: el asistente es personal (corre en local/VPS del usuario) y conversa en español. El objetivo NO es que diagnostique ni recete. El objetivo es que sea un acompañante util y SEGURO: que ayude a entender, registrar sintomas, preparar consultas medicas y escalar cuando hay señales de alarma.

Investiga y responde con evidencia:

1. Patrones de prompting documentados para IA en salud: que funciona, que esta publicado, y que patrones son peligrosos o estan desaconsejados.
2. Guardarrailes de seguridad: como se estructura un system prompt para que NUNCA de diagnostico definitivo ni indique dosis de farmacos, y para que escale a profesional cuando toca.
3. Señales de alarma (red flags) musculoesqueleticas que obligan a derivar a un profesional, especificamente para tendinopatia y dolor articular.
4. Fuentes de evidencia publicas que un asistente puede citar y como fundamentar respuestas en ellas (por ejemplo NICE, NHS, Cochrane, AAOS, PubMed, MedlinePlus).
5. Estructuras de registro y seguimiento de sintomas con evidencia real para tendinopatias (por ejemplo modelos de monitorizacion del dolor y manejo de carga progresiva).
6. Un borrador concreto de system prompt en español (scaffolding) listo para meter en un asistente personal, incluyendo sus guardarrailes.

FORMATO DE SALIDA: escribe el resultado en exploration.md con esta estructura EXACTA:

## Exploration: {tema}
### Current State
### Affected Areas
### Approaches
### Recommendation
### Risks
### Ready for Proposal

Cita las URLs de todas las fuentes que consultes, al final del archivo. No hagas nada mas.
