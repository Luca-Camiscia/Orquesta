### Mapa

* [codigo.py](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/codigo.py): Contiene la función [`parse_duration`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/codigo.py#L4-L23), modificada para validar y convertir duraciones a segundos mediante expresiones regulares.
* [test_duration.py](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py): Nuevo archivo con pruebas unitarias parametrizadas para casos válidos ([`test_accepts_ordered_duration_components`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py#L6-L24)) y casos inválidos ([`test_rejects_invalid_durations_with_clear_error`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py#L27-L60)).
* [test_visible.py](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_visible.py): Test preexistente con una aserción básica sobre `"1h30m"`.
* [issue.md](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/issue.md): Especificación del requerimiento original del reporte de error.
* [proyecto.toml](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/.orquesta/proyecto.toml): Configuración del comando de pruebas automatizadas del entorno.

---

### Hallazgos

#### Severidad Media

* **Configuración de tests del proyecto desactualizada** — [.orquesta/proyecto.toml:2](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/.orquesta/proyecto.toml#L2)
  * *Cita textual*: `tests = "~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py"`
  * *Leído*: El comando configurado en [proyecto.toml](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/.orquesta/proyecto.toml#L2) ejecuta únicamente [test_visible.py](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_visible.py).
  * *Inferido*: El conjunto de 37 casos de prueba introducido en [test_duration.py](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py) no es ejecutado por el flujo automatizado del proyecto (`pytest` reporta 1 solo test ejecutado al correr dicho comando).

#### Severidad Baja

* **Casos de prueba no exhaustivos para valores nulos y unidades individuales** — [test_duration.py:8-21](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py#L8-L21) y [test_duration.py:28-55](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py#L28-L55)
  * *Cita textual*:
    ```python
    ("0s", 0),
    ("0h0m0s", 0),
    ```
    y
    ```python
    "h",
    "1h 2m",
    "1h\n",
    ```
  * *Leído*: En casos válidos se testea `"0s"` y `"0h0m0s"`, pero no `"0h"` ni `"0m"` individualmente, ni combinaciones con ceros intercalados (como `"1h0m"` o `"0m10s"`). En casos inválidos se prueba `"h"` pero no `"m"` ni `"s"` solos, ni caracteres de escape en medio de la cadena (como `\t`).
  * *Inferido*: La cobertura de aserciones en los tests tiene pequeñas brechas respecto a las variaciones permitidas/rechazadas por el regex.

* **Compilación repetida de la expresión regular en cada invocación** — [codigo.py:10](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/codigo.py#L10)
  * *Cita textual*: `re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)`
  * *Leído*: `re.fullmatch` se ejecuta pasando la cadena de patrón directamente en cada llamada a [`parse_duration`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/codigo.py#L4-L23).
  * *Inferido*: Aunque `re` mantiene una caché interna de patrones, en invocaciones de alto volumen es preferible compilar la expresión regular una sola vez a nivel de módulo con `re.compile`.

#### Severidad Informativa

* **Manejo de tipos no string con ValueError en lugar de TypeError** — [codigo.py:11-15](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/codigo.py#L11-L15)
  * *Cita textual*:
    ```python
    match = (
        re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)
        if isinstance(text, str) and text
        else None
    )
    if match is None:
        raise ValueError(
    ```
  * *Leído*: Cualquier valor que no sea instancia de `str` (o que sea cadena vacía) evalúa `match` a `None` y lanza `ValueError`.
  * *Inferido*: En las convenciones estándar de Python, pasar un tipo no soportado suele disparar `TypeError`. Sin embargo, [issue.md:1](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/issue.md#L1) pide explícitamente *"rechazar entradas inválidas con ValueError"* y los tests en [test_duration.py:52-54](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py#L52-L54) prueban `None`, `45` y `b"45s"` esperando `ValueError`, por lo que el comportamiento implementado responde a esa especificación.

---

### Qué NO encontré

* No encontré ningún caso de entrada dentro de la especificación o fuera de ella que rompa la función o produzca un cálculo erróneo; la expresión regular y la lógica de cálculo manejan correctamente todas las combinaciones válidas de `h`, `m` y `s`.
* No encontré riesgo de retroceso catastrófico (ReDoS): las expresiones `[0-9]+` van seguidas de literales fijos no ambiguos (`h`, `m`, `s`) en orden secuencial.
* No encontré inclusión del nuevo archivo [test_duration.py](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/test_duration.py) en [.orquesta/proyecto.toml](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/.orquesta/proyecto.toml#L2).

---

### Preguntas abiertas

1. ¿Debe actualizarse la configuración en [.orquesta/proyecto.toml](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/duracion-d-2-df7c60fe/codex-forjador--eval-duracion-d2/.orquesta/proyecto.toml#L2) para que el comando ejecute `pytest` completo sobre todos los archivos de test y no solo sobre `test_visible.py`?
2. ¿Se mantendrá `ValueError` para tipos no string como convención definitiva de la API o se prefiere discriminar `TypeError` en el futuro?

---

Veredicto: APROBAR CON CAMBIOS
RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: .orquesta/proyecto.toml:2
SIGUIENTE PASO: nada