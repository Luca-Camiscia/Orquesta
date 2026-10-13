### Mapa (qué hay y dónde)

- [`issue.md:1`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/issue.md#L1): Requerimiento original del bug report. Exige aceptar combinaciones de horas, minutos y segundos en orden y rechazar entradas inválidas con `ValueError`.
- [`.orquesta/proyecto.toml:1-2`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/.orquesta/proyecto.toml#L1-L2): Configuración del comando de pruebas del proyecto (`~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py`).
- [`codigo.py:1-18`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/codigo.py#L1-L18): Implementación de [`parse_duration`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/codigo.py#L4-L18) utilizando expresión regular con grupos opcionales y control de flujo basado en `isinstance(text, str) and text`.
- [`test_visible.py:1-40`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/test_visible.py#L1-L40): Batería de 33 tests con pytest que cubren casos válidos, inválidos y tipos no cadena.

---

### Hallazgos (con citas)

#### Severidad Media: Cobertura incompleta de casos válidos en tests
- **Leído**: [`test_visible.py:10-23`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/test_visible.py#L10-L23) incluye:
  ```python
  ("2h", 7200), ("30m", 1800), ("45s", 45), ("1h45s", 3645),
  ("2m45s", 165), ("1h30m45s", 5445), ("0s", 0), ("0h0m0s", 0), ("90m", 5400)
  ```
  y [`test_visible.py:6-7`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/test_visible.py#L6-L7) evalúa únicamente `"1h30m"`.
- **Inferido (Diagnóstico)**: Faltan pruebas unitarias para unidades individuales en cero como `"0h"` o `"0m"` (solo se probaron `"0s"` y `"0h0m0s"`), duraciones con ceros a la izquierda (como `"01h05m"`), y valores enteros grandes (como `"100h"`). Aunque el código actual los procesa correctamente, la suite no previene regresiones en esas variantes.

#### Severidad Baja: Cobertura incompleta de entradas inválidas en tests
- **Leído**: [`test_visible.py:28-36`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/test_visible.py#L28-L36) prueba 23 casos no válidos (espacios, saltos de línea, orden invertido, signos, números flotantes y tipos no string).
- **Inferido (Diagnóstico)**: Faltan casos borde como tabulaciones internas (`"1h\t30m"`), números con signo explícito dentro de la cadena (`"1h+30m"`), ceros con signo negativo (`"-0s"`), y caracteres numéricos Unicode no ASCII (por ejemplo dígitos arábigos o full-width).

#### Severidad Baja: Acoplamiento de la validación de cadena vacía a la expresión booleana
- **Leído**: En [`codigo.py:10-14`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/codigo.py#L10-L14):
  ```python
  match = (
      re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)
      if isinstance(text, str) and text
      else None
  )
  ```
- **Inferido (Diagnóstico)**: Todos los grupos de la expresión regular son opcionales (`(?:...)?`). Si `text` fuese `""`, la expresión regular haría match completo devolviendo `(None, None, None)`, lo que resultaría en `0` en vez de fallar. El rechazo de `""` depende exclusivamente de la evaluación falsy `and text` en [`codigo.py:12`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/codigo.py#L12), no del patrón regex. Es un acoplamiento frágil ante refactors.

#### Severidad Baja: Contrato de excepciones para tipos no string
- **Leído**: En [`codigo.py:12-16`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/codigo.py#L12-L16), cuando `text` no es `str`, `match` es `None` y se lanza:
  ```python
  raise ValueError("Duración inválida: se esperan enteros con unidades h, m, s en orden")
  ```
  y en [`test_visible.py:34,38`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/test_visible.py#L34-L38) se espera explícitamente `ValueError` para `None` y `45`.
- **Inferido (Diagnóstico)**: Aunque la implementación cumple estrictamente con el test visible, en Python idiomático recibir un argumento de tipo inadecuado (como `None` o `int`) suele arrojar `TypeError` en lugar de `ValueError`.

#### Severidad Informativa: Recompilación de expresión regular por invocación
- **Leído**: [`codigo.py:11`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/codigo.py#L11) invoca `re.fullmatch` directamente con la cadena literal.
- **Inferido (Diagnóstico)**: Si bien `re` mantiene una caché LRU interna, compilar el patrón a nivel de módulo con `re.compile` es una práctica recomendada de claridad y eficiencia.

---

### Qué NO encontré

- No encontré ningún caso de entrada concreto que rompa o cause una falla no controlada en la implementación actual de [`codigo.py:10-18`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/codigo.py#L10-L18).
- No encontré especificación en [`issue.md:1`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/issue.md#L1) respecto a si se contempla soportar unidades de mayor escala como días (`d`), pese al nombre del proyecto `duracion-D-3`.
- No encontré pruebas en [`test_visible.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/test_visible.py) para caracteres de espaciado no estándar (como `\t` o `\v`) ni para dígitos en formatos numéricos no ASCII.

---

### Preguntas abiertas

- ¿El alcance del proyecto contempla soportar a futuro la unidad `d` (días), o está intencionalmente limitado a `h`, `m`, `s` según lo definido en [`issue.md:1`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/issue.md#L1)?
- ¿Se mantendrá como contrato definitivo que entradas con tipos distintos a `str` lancen `ValueError` (como requiere [`test_visible.py:34`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-3-8b4d1082/codex-forjador--eval-duracion-d3/test_visible.py#L34)), o se migrará a `TypeError` para alinearse con las convenciones estándar de Python?

---

Veredicto: APROBAR CON CAMBIOS

RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:10-18, test_visible.py:10-39
SIGUIENTE PASO: nada