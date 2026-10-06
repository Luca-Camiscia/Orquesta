# Investigación: estado del arte (2024–2026) y qué se incorporó

Dos investigadores de familias distintas trabajaron sobre el mismo pedido: `gemini-investigador` (agy) y
un agente Claude con búsqueda web que verificó contra los abstracts de arXiv. Coincidieron en lo
central. Las citas que solo trajo uno y no se pudieron confirmar figuran abajo como dudosas.

## Hallazgos con respaldo (los confirmaron los dos, o se verificaron en arXiv)

| Tema | Paper | Resultado | Qué hace orquesta |
|---|---|---|---|
| Fallas multiagente | MAST, Cemri et al. 2025 ([2503.13657](https://arxiv.org/abs/2503.13657)) | 14 modos de falla: diseño 41,8%, desalineación 32,3%, verificación 26%. Verificar contra el objetivo dio +15,6% | Contrato de salida, criterio de corte, tests del proyecto antes de la review |
| Debate vs. voto | Choi, Zhu, Li 2025 ([2508.17536](https://arxiv.org/abs/2508.17536)) | La votación explica casi toda la ganancia del debate, que por sí solo es una martingala | El modo crítica arranca con una ronda independiente |
| Debate selectivo | iMAD 2026 ([2511.11306](https://arxiv.org/abs/2511.11306)) | Hasta 92% menos tokens, hasta +13,5% de precisión | Detector de desacuerdo: con consenso, no se debate (probado: 72 s contra 250 s) |
| Conformidad | [2605.00914](https://arxiv.org/abs/2605.00914), [2509.23055](https://arxiv.org/abs/2509.23055) | Hasta 85,5% de conformidad y hasta 70% de razonamientos correctos que se rompen al leer a los pares | Una sola ronda de crítica; los agentes declaran si cambiaron y por qué |
| Identidad y posición | [2510.07517](https://arxiv.org/abs/2510.07517), [2406.07791](https://arxiv.org/abs/2406.07791) | La complacencia con los pares es más común que el sesgo propio; anonimizar la reduce | **Todas** las reuniones son anónimas, con etiquetas en orden aleatorio |
| Autopreferencia | Panickssery et al. 2024 ([2404.13076](https://arxiv.org/abs/2404.13076)) | Los modelos reconocen y favorecen sus propios textos | Review ciega al autor y de otra familia; juez de una familia que no participa |
| Largo | Dubois et al. 2024 ([2404.04475](https://arxiv.org/abs/2404.04475)) | Controlar el largo sube la correlación con humanos de 0,94 a 0,98 | El juez tiene la instrucción explícita de no premiar el largo |
| Confianza verbalizada | Xiong et al. 2024 ([2306.13063](https://arxiv.org/abs/2306.13063)); Prasad y Nguyen 2025 ([2505.19184](https://arxiv.org/abs/2505.19184)) | Sobreconfianza: 72,9% de confianza promedio donde lo racional es 50% | La confianza sirve solo como umbral de escalado (menos de 75 pide confirmación). Se calibra por backend y no pondera votos |
| Tests no alcanzan | [2503.15223](https://arxiv.org/abs/2503.15223) | 7,8% de los parches "correctos" fallan con todos los tests; 29,6% se comportan distinto del parche real | El reviewer recibe el resultado de los tests y el aviso de que no prueban que el cambio esté bien |
| Review automática | SWR-Bench 2025 ([2509.01494](https://arxiv.org/abs/2509.01494)) | Precisión menor a 10%; agregar reviews sube el F1 a 43,7% | Solo se puede RECHAZAR con un hallazgo concreto (archivo:línea y caso) |
| Multi-turno | Laban et al. 2025 ([2505.06120](https://arxiv.org/abs/2505.06120)) | 39% de caída en conversaciones de varios turnos | Contexto acotado: los últimos 8 mensajes y los veredictos |
| Heterogeneidad | [2410.12853](https://arxiv.org/abs/2410.12853) | Equipo diverso 91% contra 82% de tres copias del mismo modelo | Se mantiene para revisar y criticar |
| Agentes de código | Agentless ([2407.01489](https://arxiv.org/abs/2407.01489)) | Localizar → reparar → validar: 32% en SWE-bench Lite por US$0,70 | Las personas del forjador y el tester piden diff mínimo y tests reales |
| Clarificación | Ambig-SWE, ICLR 2026 ([2502.13069](https://arxiv.org/abs/2502.13069)); Edwards y Schuster 2026 ([2603.26233](https://arxiv.org/abs/2603.26233)); SAGE-Agent ([2511.08798](https://arxiv.org/abs/2511.08798)); RegretBench ([2607.21143](https://arxiv.org/abs/2607.21143)) | Los modelos no distinguen bien un pedido ambiguo; preguntar mejora hasta 74%. Un detector separado del ejecutor rinde más y pregunta poco en tareas simples. Priorizar por valor de información usa 1,5–2,7× menos preguntas. Saber cuándo parar importa tanto como preguntar | `claude-interrogador`: lee el repo antes de preguntar, hasta 4 preguntas por ronda ordenadas por impacto, corta cuando nada cambia el plan y entrega un brief |

## Matices y desacuerdos entre las fuentes

- **Self-MoA** ([2502.00674](https://arxiv.org/abs/2502.00674)): varias muestras del mejor modelo pueden ganarle
  a mezclar modelos *para redactar*. La heterogeneidad se usa para revisar, no necesariamente para producir.
- **Varias soluciones en paralelo con agregación** (MoA, [2406.04692](https://arxiv.org/abs/2406.04692)): la
  propuso gemini. No se incorporó porque es el "modo carrera" que el usuario descartó.
- La mayor parte de la evidencia sobre debate viene de preguntas de QA y matemática, no de diseño de
  software. Los papers de 2026 son preprints.

## Interfaces (2026-10-05)

`gemini-investigador` relevó UIs de agentes (Conductor, Devin, OpenHands, Vibe Kanban, Cursor, Zed, Jules,
Copilot agent). Patrón dominante: tres paneles (sesiones y tareas | conversación | diff o inspector).
Respaldo verificado: Magentic-UI ([2507.22358](https://arxiv.org/abs/2507.22358)): co-planning, co-tasking y
action guards; Vasconcelos et al. ([2402.01694](https://arxiv.org/abs/2402.01694)): un porcentaje de
confianza suelto no calibra. El usuario prefiere explorar una interfaz de terminal (plugin de Claude
Code) en vez de rediseñar la UI web.

## Citas dudosas (no usar sin verificar)

Solo las trajo `gemini-investigador` y no se pudieron confirmar: `arXiv:2501.12345` (parece un número
inventado), las cifras de "15-25% de sesgo de familia" y "58-72% de sesgo de posición", y los
porcentajes atribuidos a HCP-MAD y a ConfidenceBench. En la ronda de interfaces: "16 plataformas" (listó 11), un mood "preciso" inexistente y el paper de *cognitive forcing* atribuido a van Berkel (es de Buçinca et al. 2021). En la de modelos gratis: supuso GPU dedicada (la PC tiene una AMD Barcelo integrada) y citó modelos que Groq ya no lista. Además atribuye "Debate or Vote" a autores
anónimos, cuando es de Choi, Zhu y Li. Es un buen ejemplo de por qué se contrasta entre familias.

## Pendiente (no implementado)

- Evaluar con el juez en los dos órdenes (A-B y B-A). Duplica el costo; conviene solo para decisiones
  pareadas importantes.
- Testing diferencial al estilo PatchDiff ([2503.15223](https://arxiv.org/abs/2503.15223)).
- Cascada por costo: tareas triviales a modelos baratos (RouteLLM, [2406.18665](https://arxiv.org/abs/2406.18665)).
- Cuarta familia de modelos por API gratis (plan a futuro, decisión del usuario 2026-10-05): Groq con
  `qwen/qwen3.8-27b` como juez o revisor de otra familia y para tareas triviales. Límites del free tier
  verificados en [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits): 30 RPM,
  1K RPD, 8K TPM, 200K TPD (el TPM es corto para agentes que leen mucho contexto). Integración prevista:
  un perfil de Codex CLI (`codex exec -p groq`, proveedor compatible con OpenAI) en `backends.yaml`, sin
  CLI nuevo; la key en el llavero (`secret-tool`), nunca en archivos. Rompe el principio "sin API keys".
  Descartados por ahora: modelos locales (sin GPU dedicada) y OpenRouter `:free` (50 pedidos por día).
