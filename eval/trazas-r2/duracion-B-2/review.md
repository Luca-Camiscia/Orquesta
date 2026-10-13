### Mapa (qué hay y dónde)

- [issue.md](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/issue.md#L1): Especificación del requerimiento. Describe el comportamiento esperado ante formatos no estándar y la obligación de lanzar `ValueError`.
- [codigo.py](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/codigo.py#L4-L16): Implementación de la función [`parse_duration`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/codigo.py#L4-L16) mediante `re.fullmatch`.
- [test_visible.py](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_visible.py#L1-L6): Test unitario original del proyecto ([`test_basico`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_visible.py#L4-L5)) que valida únicamente `"1h30m" == 5400`.
- [test_parse_duration.py](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L1-L56): Nueva suite de pruebas agregada en el diff con 34 casos parametrizados ([`test_accepts_ordered_duration_components`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L6-L23) y [`test_rejects_invalid_duration_with_clear_error`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L26-L56)).
- [.orquesta/proyecto.toml](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/.orquesta/proyecto.toml#L1-L2): Configuración del comando de pruebas del evaluador del entorno.

---

### Hallazgos (con citas)

#### Severidad Alta (Bugs bloqueantes)
- **Ninguno detectado**: No se halló ningún caso de prueba donde una entrada válida sea rechazada o una entrada inválida sea aceptada según los requisitos textuales de [issue.md](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/issue.md#L1).

#### Severidad Media (Integración y Cobertura)
1. **Desconexión de la nueva suite en el runner del proyecto**:
   - **Evidencia textual**:
     > [.orquesta/proyecto.toml:2](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/.orquesta/proyecto.toml#L2): `tests = "~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py"`
   - **Diagnóstico**: La configuración oficial de pruebas del entorno solo ejecuta [test_visible.py](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_visible.py#L1-L6). La nueva suite [test_parse_duration.py](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L1-L56) no es invocada por el comando automatizado del proyecto, quedando invisibilizada en la evaluación por defecto salvo que se invoque `pytest` sin especificar archivo.
   *(Inferido: si el harness evalúa solo mediante el comando en `proyecto.toml`, las pruebas agregadas no garantizan regresión en CI).*

2. **Casos borde omitidos en la suite de pruebas**:
   - **Evidencia textual**:
     > [test_parse_duration.py:16-17](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L16-L17): `("0s", 0), ("0h0m0s", 0),`
     > [test_parse_duration.py:32](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L32): `"h",`
     > [test_parse_duration.py:39-40](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L39-L40): `"2m1h", "1s2m",`
   - **Diagnóstico**:
     - No se testean ceros individuales para horas o minutos (`"0h"`, `"0m"`, `"0h30m"`).
     - En los casos de rechazo de unidades solitarias sin dígitos se prueba `"h"`, pero no `"m"` ni `"s"`.
     - En los casos de orden invertido no se incluye una secuencia completa invertida de 3 componentes como `"1s2m3h"` o un salto desordenado `"1s2h"`.

#### Severidad Baja (Eficiencia y Mantenibilidad)
1. **Compilación repetida de la expresión regular**:
   - **Evidencia textual**:
     > [codigo.py:7](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/codigo.py#L7): `re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)`
   - **Diagnóstico**: La expresión regular se define inline dentro del cuerpo de la función en lugar de compilarse una sola vez a nivel de módulo mediante `re.compile`. Si bien Python tiene un caché interno en `re`, la práctica recomendada para funciones utilitarias es precompilar a nivel de módulo para evitar comprobaciones de caché y parsing redundante.

2. **Tipificación de excepciones para valores de tipo no string**:
   - **Evidencia textual**:
     > [codigo.py:8-12](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/codigo.py#L8-L12):
     ```python
     if isinstance(text, str)
     else None
     if match is None or not text:
         raise ValueError("Duración inválida: usá números enteros con h, m, s en orden")
     ```
   - **Diagnóstico**: El código captura tipos incompatibles (`None`, `int`, `bytes`) y los canaliza a `ValueError` con el mismo mensaje que una cadena sintácticamente incorrecta.
   *(Leído: en [issue.md:1](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/issue.md#L1) se pidió "rechazar entradas inválidas con ValueError", por lo que responde al requerimiento estricto; inferido: a nivel idiomático de Python, un tipo incorrecto en el argumento de entrada se suele señalar con `TypeError`).*

---

### Qué NO encontré

- No encontré ningún caso de entrada que rompa la función o genere una excepción inesperada (la lógica de `match.groups()` combinada con `not text` cubre cadenas vacías, valores `None` y permutaciones inválidas).
- No encontré configuración de formateo ni linters en el repositorio (archivos como `pyproject.toml`, `setup.cfg`, `ruff.toml`).
- No encontré en [issue.md](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/issue.md#L1) especificación de soporte para espacios en blanco (e.g. `"1h 30m"`), números negativos ni números flotantes, por lo que su rechazo estricto en [test_parse_duration.py:41-47](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/test_parse_duration.py#L41-L47) se asume consistente con el diseño.

---

### Preguntas abiertas

1. ¿Se debe modificar [.orquesta/proyecto.toml:2](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/.orquesta/proyecto.toml#L2) para que el comando de prueba ejecute toda la suite con `pytest` en vez de limitar a `test_visible.py`?
2. ¿Se prefiere mantener `ValueError` para todos los tipos incompatibles como solicita [issue.md:1](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-2-5b7cf1e0/codex-forjador--eval-duracion-b2/issue.md#L1), o se contempla admitir `TypeError` cuando `text` no sea una instancia de `str`?

---

Veredicto: APROBAR CON CAMBIOS
RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:7
SIGUIENTE PASO: actualizar .orquesta/proyecto.toml para ejecutar test_parse_duration.py y precompilar el patrón regex en codigo.py