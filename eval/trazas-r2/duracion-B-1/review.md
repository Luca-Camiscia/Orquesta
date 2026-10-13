### Mapa (qué hay y dónde)

- [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/issue.md#L1): Especificación del requerimiento: *«Bug report: `parse_duration` in codigo.py se rompe con entradas que no son exactamente "XhYm" (por ejemplo "45s" anda raro, y con basura no da error claro). Hacela robusta: debe aceptar combinaciones de horas, minutos y segundos en ese orden, y rechazar entradas inválidas con ValueError.»*
- [`.orquesta/proyecto.toml:1-2`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/.orquesta/proyecto.toml#L1-L2): Configuración de las pruebas automatizadas del proyecto: `tests = "~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py"`.
- [`test_visible.py:1-6`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_visible.py#L1-L6): Prueba visible básica preexistente que verifica `assert parse_duration("1h30m") == 5400`.
- [`codigo.py:1-19`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/codigo.py#L1-L19): Implementación modificada de `parse_duration` que valida tipo y no-vacío, aplica la expresión regular `(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?`, levanta `ValueError` ante fallos de coincidencia y computa los segundos totales.
- [`test_duration.py:1-36`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_duration.py#L1-L36): Nueva suite de tests con 9 casos válidos en `test_accepts_ordered_duration_components` y 22 casos inválidos en `test_rejects_invalid_durations`.

---

### Hallazgos (con citas)

#### Severidad Baja: Desconexión de `test_duration.py` en la configuración del proyecto
- **Fuente**: [`.orquesta/proyecto.toml:2`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/.orquesta/proyecto.toml#L2).
- **Leído**:
  ```toml
  tests = "~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py"
  ```
- **Inferido**: El comando de test automatizado configurado para el repositorio ejecuta exclusivamente [`test_visible.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_visible.py#L1-L6). Los 31 casos de prueba introducidos en [`test_duration.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_duration.py#L1-L36) quedan fuera de la ejecución por defecto del runner configurado en el proyecto.

#### Severidad Baja: Casos borde no cubiertos en la suite de pruebas
- **Fuente**: [`test_duration.py:6-35`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_duration.py#L6-L35).
- **Leído**:
  - En [`test_duration.py:16`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_duration.py#L16) se testea `("0h0m0s", 0)`.
  - En [`test_duration.py:27-30`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_duration.py#L27-L30) se testean entradas inválidas como `""`, `"basura"`, `"123"`, `"h"`, `"1hxyz"`, `"xyz1h"`, `"1h30"`, `"1m2h"`, `"1s2m"`, `"1h2h"`, `"1m2m"`, `"1s2s"`, `"-1h"`, `"+1h"`, `"1.5h"`, `" 1h"`, `"1h "`, `"1h 2m"`, `"1s\n"`, `"1H"`, `None`, `45`.
- **Inferido**: Faltan aserciones específicas para:
  1. Componentes cero aislados: `"0h"`, `"0m"`, `"0s"` (solo se prueba la combinación completa `"0h0m0s"`).
  2. Componentes con ceros a la izquierda (ej. `"01h02m"` o `"00h"`).
  3. Mayúsculas en unidades de minutos o segundos (`"1M"`, `"1S"`; únicamente se prueba `"1H"`).
  4. Caracteres de espacio o control al inicio o aislados (ej. `"   "`, `"\t1h"`, `"\n1h"`).
  5. Desorden con tres componentes o no contiguos (ej. `"1s1h"`, `"1s2m3h"`, `"1h1s1m"`).
  6. Repetición de unidad separada (ej. `"1h2m3h"`).
  7. Tipos adicionales no string (ej. `bool`, `float`, colecciones).

#### Severidad Informativa: Expresión regular no precompilada
- **Fuente**: [`codigo.py:10-14`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/codigo.py#L10-L14).
- **Leído**:
  ```python
  match = (
      re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)
      if isinstance(text, str) and text
      else None
  )
  ```
- **Inferido**: El patrón se evalúa en cada invocación de `parse_duration` en lugar de precompilarse a nivel de módulo con `re.compile`. Si bien el módulo `re` de Python implementa caché interna, precompilar el patrón es una convención usual para claridad y micro-optimización.

#### Severidad Informativa: Conformidad lógica del parser frente a la especificación
- **Fuente**: [`codigo.py:10-18`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/codigo.py#L10-L18) y [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/issue.md#L1).
- **Leído**:
  - `isinstance(text, str) and text` descarta tipos no `str` y cadenas vacías `""`.
  - La expresión `re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)` exige que los caracteres consumidos correspondan a bloques ordenados terminados en `h`, `m` o `s`. Para toda cadena no vacía, esto garantiza que al menos una unidad esté presente.
  - Si `match is None`, se lanza `ValueError("Duración inválida: usá enteros con unidades h, m, s en ese orden")`.
  - Los grupos ausentes se resuelven como `0` mediante `int(value or 0)`.
- **Inferido**: El código procesa correctamente las 7 combinaciones posibles de componentes en orden (`h`, `m`, `s`, `hm`, `hs`, `ms`, `hms`), maneja valores numéricos no negativos y rechaza entradas con unidades desordenadas, duplicadas, no numéricas o de tipo incorrecto con `ValueError`. No se observan fallos lógicos en los casos esperados.

---

### Qué NO encontré

1. No encontré ningún caso de prueba ni entrada concreta que rompa la implementación o incumpla lo requerido en [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/issue.md#L1).
2. No encontré la inclusión de [`test_duration.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/test_duration.py) en el comando de pruebas configurado en [`.orquesta/proyecto.toml:2`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/.orquesta/proyecto.toml#L2).
3. No encontré tests unitarios para componentes aislados en cero (`"0h"`, `"0m"`, `"0s"`).
4. No encontré precompilación a nivel de módulo del patrón regex (`re.compile`).

---

### Preguntas abiertas

1. ¿Se debe actualizar la configuración en [`.orquesta/proyecto.toml`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/.orquesta/proyecto.toml) para que la suite de pruebas del proyecto ejecute tanto `test_visible.py` como `test_duration.py`?
2. ¿Se considera necesario precompilar la expresión regular a nivel de módulo en [`codigo.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-1-db86ea4f/codex-forjador--eval-duracion-b1/codigo.py) para optimizar invocaciones sucesivas?

---

Veredicto: APROBAR CON CAMBIOS
RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:1-18, test_duration.py:1-35
SIGUIENTE PASO: nada