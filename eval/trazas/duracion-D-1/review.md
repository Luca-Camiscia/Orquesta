### Mapa (qué hay y dónde)

- [`issue.md`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/issue.md): Especificación del requerimiento y reporte de error inicial ([línea 1](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/issue.md#L1)).
- [`.orquesta/proyecto.toml`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/.orquesta/proyecto.toml): Configuración del comando de pruebas del proyecto ([línea 2](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/.orquesta/proyecto.toml#L2)).
- [`test_visible.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_visible.py): Prueba unitaria original preexistente ([líneas 4-5](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_visible.py#L4-L5)).
- [`codigo.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py): Implementación de [`parse_duration`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py#L4-L20) mediante expresión regular y conversión de unidades.
- [`test_duration.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py): Nueva suite de pruebas parametrizadas para casos válidos ([líneas 6-24](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py#L6-L24)) e inválidos ([líneas 27-59](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py#L27-L59)).

---

### Hallazgos (con citas)

#### 1. Configuración de tests desincronizada (Severidad Media)
- **Fuente**: [`.orquesta/proyecto.toml:2`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/.orquesta/proyecto.toml#L2)
- **Cita textual**:
  ```toml
  tests = "~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py"
  ```
- **Leído**: El comando definido ejecuta exclusivamente [`test_visible.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_visible.py).
- **Inferido**: Las 37 pruebas agregadas en [`test_duration.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py) no son ejecutadas por el runner automatizado del proyecto, por lo que quedan fuera de la verificación automática configurada.

#### 2. Compilación en cada llamada de la expresión regular (Severidad Baja)
- **Fuente**: [`codigo.py:10`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py#L10)
- **Cita textual**:
  ```python
  re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)
  ```
- **Leído**: El patrón de regex se pasa como literal de cadena en cada invocación de [`parse_duration`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py#L4-L20).
- **Inferido**: Aunque Python cuenta con un caché interno en `re`, la práctica idiomática para funciones utilitarias de parsing frecuente es compilar el patrón a nivel de módulo una sola vez.

#### 3. Manejo de tipos no string mediante excepción de valor (Severidad Baja)
- **Fuente**: [`codigo.py:11-15`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py#L11-L15)
- **Cita textual**:
  ```python
  if isinstance(text, str)
  else None
  )
  if match is None or not text:
      raise ValueError(
  ```
- **Leído**: Si la entrada no es de tipo `str` (por ejemplo `None`, enteros o bytes), la función genera `ValueError` (evaluado en [`test_duration.py:52-54`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py#L52-L54)).
- **Inferido**: En Python idiomático con anotación `text: str`, pasar un tipo incompatible suele elevar `TypeError`, aunque el autor decidió conscientemente unificar todas las fallas bajo `ValueError`.

#### 4. Documentación potencialmente confusa sobre opcionalidad (Severidad Baja)
- **Fuente**: [`codigo.py:7`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py#L7)
- **Cita textual**:
  ```python
  Las unidades son opcionales; un formato inválido genera ValueError.
  ```
- **Leído**: Se declara que las unidades son opcionales, pero una entrada sin ninguna unidad (como `"123"`) genera `ValueError` ([`test_duration.py:32`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py#L32)).
- **Inferido**: La redacción sugiere que se podría omitir toda unidad, cuando en realidad se refiere a que cada unidad individual (`h`, `m` o `s`) es opcional dentro de la combinación, requiriendo al menos una.

#### 5. Cobertura incompleta en casos borde de cero y dígitos múltiples (Severidad Baja)
- **Fuente**: [`test_duration.py:9-20`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py#L9-L20)
- **Leído**: Se prueba `("0h0m0s", 0)` y `("0s", 0)`, pero no se parametrizan `"0h"` ni `"0m"` individuales.
- **Inferido**: Aunque el código actual los procesa y devuelve 0, la suite de pruebas no los tiene fijados explícitamente para evitar regresiones.

---

### Qué NO encontré

- No encontré ningún caso de prueba donde [`parse_duration`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py#L4-L20) falle o arroje un cálculo incorrecto frente a la especificación de [`issue.md:1`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/issue.md#L1).
- No encontré compilación estática a nivel de módulo del objeto regex (`re.compile`) en [`codigo.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/codigo.py).
- No encontré referencia ni ejecución de [`test_duration.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_duration.py) en el archivo de configuración [`.orquesta/proyecto.toml`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/.orquesta/proyecto.toml).

---

### Preguntas abiertas

- ¿Es intención del equipo que [`.orquesta/proyecto.toml`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/.orquesta/proyecto.toml) ejecute toda la suite de pruebas del proyecto (`pytest` general) o debe mantenerse limitado exclusivamente a [`test_visible.py`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-1-f884092a/codex-forjador--eval-duracion-d1/test_visible.py)?
- ¿Se prefiere mantener `ValueError` para tipos no string o alinearlo con la convención estándar de Python (`TypeError`)?

---

Veredicto: APROBAR CON CAMBIOS

RESULTADO: OK
CONFIANZA: 100
EVIDENCIA: codigo.py:9-20
SIGUIENTE PASO: nada