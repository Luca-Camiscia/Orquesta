### Mapa (qué hay y dónde)

* [`codigo.py:1-9`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L1-L9): contiene la implementación de [`slugify`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L5-L9). Normaliza el texto de entrada en forma `NFKD`, filtra los caracteres combinables (`unicodedata.combining`) y convierte caracteres no alfanuméricos en guiones con `re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")`.
* [`test_visible.py:1-25`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/test_visible.py#L1-L25): suite de pruebas con `pytest`. Conserva [`test_basico`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/test_visible.py#L6-L7) e incorpora [`test_slug_para_urls`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/test_visible.py#L23-L24) parametrizado con 8 casos de prueba en [`test_visible.py:10-22`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/test_visible.py#L10-L22).
* [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/issue.md#L1): requerimiento original reportando el problema: `"Bug report: `slugify` en codigo.py genera slugs que no sirven en URLs con títulos en español. Arreglalo."`.
* [`.orquesta/proyecto.toml:1-2`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/.orquesta/proyecto.toml#L1-L2): configuración del comando de ejecución de pruebas (`~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py`).

---

### Hallazgos (con citas)

#### 1. Severidad Media: Pérdida o mutilación de ligaduras latinas no descomponibles bajo NFKD
* **Fuente:** [`codigo.py:8-9`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L8-L9)
* **Cita textual:**
  ```python
  text = "".join(char for char in normalized if not unicodedata.combining(char))
  return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
  ```
* **Distinción:**
  * *Lo que leí:* En la línea 8 se preservan todos los caracteres donde `unicodedata.combining(char)` es `0`. En la línea 9, `re.sub(r"[^a-z0-9]+", "-", text.lower())` sustituye cualquier carácter que no esté en `[a-z0-9]` por un guion `-`.
  * *Lo que infiero (diagnóstico):* Ligaduras latinas utilizadas en expresiones o préstamos del español (por ejemplo, `æ` `\u00E6` en `"currículum vitæ"` o `œ` `\u0153` en `"ex æquo"`) no tienen marcas combinables (`combining == 0`) ni se descomponen a letras ASCII bajo NFKD. Al llegar a la línea 9, no coinciden con `[a-z0-9]`, por lo que son sustituidas por guiones y eliminadas por `.strip("-")` al final de la palabra. Esto produce `"curriculum-vit"` (en vez de `"curriculum-vitae"`) o `"ex-quo"` (en vez de `"ex-aequo"`), mutilando la palabra.

#### 2. Severidad Baja: Descomposición de compatibilidad (NFKD) en superíndices y caracteres numéricos
* **Fuente:** [`codigo.py:7`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L7)
* **Cita textual:**
  ```python
  normalized = unicodedata.normalize("NFKD", text)
  ```
* **Distinción:**
  * *Lo que leí:* La normalización utiliza el modo `NFKD` (Compatibility Decomposition).
  * *Lo que infiero (diagnóstico):* `NFKD` descompone caracteres de compatibilidad numéricos:
    * Los superíndices numéricos (como `²`, `³`) se convierten en dígitos ASCII estándar (`'2'`, `'3'`), haciendo que `"Fórmula x²"` o `"Piso 2²"` generen `"formula-x2"` o `"piso-22"`.
    * Las fracciones compuestas (como `"½"`) se descomponen en `'1'`, barra de fracción `'\u2044'` y `'2'`. La barra de fracción se reemplaza por guion, pero el entero y el numerador contiguos se concatenan: `"Capítulo 1½"` resulta en `'capitulo-11-2'`.

#### 3. Severidad Baja: Fragilidad de tipos en tiempo de ejecución ante entradas no `str`
* **Fuente:** [`codigo.py:5`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L5) y [`codigo.py:7`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L7)
* **Cita textual:**
  ```python
  def slugify(text: str) -> str:
      """Convierte un título en un slug ASCII sin acentos para URLs."""
      normalized = unicodedata.normalize("NFKD", text)
  ```
* **Distinción:**
  * *Lo que leí:* La función anota `text: str`, pero no realiza coerción ni verificación defensiva de tipo antes de invocar `unicodedata.normalize`.
  * *Lo que infiero (diagnóstico):* Si se recibe un valor no cadena en tiempo de ejecución (por ejemplo, `None` o un entero), `unicodedata.normalize` interrumpe la ejecución arrojando `TypeError: normalize() argument 2 must be str, not ...`.

#### 4. Severidad Baja: Cobertura incompleta en la suite de pruebas para signos tipográficos del español
* **Fuente:** [`test_visible.py:10-22`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/test_visible.py#L10-L22)
* **Cita textual:**
  ```python
  @pytest.mark.parametrize(
      ("titulo", "esperado"),
      [
          ("ÁÉÍÓÚ áéíóú Ññ Üü", "aeiou-aeiou-nn-uu"),
          ("¡Canción del año!", "cancion-del-ano"),
          ("Cancio\u0301n del an\u0303o", "cancion-del-ano"),
          ("  Niño\tpingüino / 2026\n", "nino-pinguino-2026"),
          ("Guía: café & té? #1", "guia-cafe-te-1"),
          ("--hola---mundo--", "hola-mundo"),
          ("", ""),
          (" \t¡!¿?\n", ""),
      ],
  )
  ```
* **Distinción:**
  * *Lo que leí:* Los casos parametrizados prueban vocales acentuadas, diéresis, eñes, formas combinadas NFD, espacios variados, puntuación básica (`!`, `¡`, `?`, `¿`, `:`, `&`, `/`, `#`), guiones repetidos y entradas vacías o compuestas exclusivamente de signos/espacios.
  * *Lo que infiero (diagnóstico):* Faltan casos de prueba para:
    * Separadores tipográficos habituales en títulos en español como la raya (`—`) o semirraya (`–`) adyacentes a palabras sin espacio (ej. `"Madrid–Barcelona"` o `"Resumen—2026"`). Aunque la implementación en `codigo.py` los maneja convirtiéndolos en guion mediante `re.sub`, no están cubiertos por la suite para prevenir regresiones.
    * Comillas tipográficas angulares (`« »`).
    * Indicadores ordinales comunes en español (`1.º`, `1.ª`, `1º`, `1ª`).
    * Manejo de tipos de entrada inválidos.

#### 5. Informativa: Compilación de la expresión regular en cada invocación
* **Fuente:** [`codigo.py:9`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L9)
* **Cita textual:**
  ```python
  return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
  ```
* **Distinción:**
  * *Lo que leí:* `re.sub` recibe el patrón como literal de cadena en cada invocación.
  * *Lo que infiero (diagnóstico):* Aunque Python mantiene una caché interna en el módulo `re`, compilar la expresión regular a nivel de módulo es una práctica más idiomática y explícita para funciones de utilidad.

---

### Qué NO encontré

1. **Especificación formal de requisitos de slug:** En [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/issue.md#L1) solo se enuncia: `"Bug report: `slugify` en codigo.py genera slugs que no sirven en URLs con títulos en español. Arreglalo."`. No se detallan especificaciones de negocio sobre el tratamiento de locuciones latinas, símbolos monetarios o el valor esperado si un título consiste únicamente en caracteres no alfanuméricos.
2. **Mapeo o expansión previa de ligaduras:** No encontré ningún paso de sustitución o reemplazo de ligaduras (`æ` -> `ae`, `œ` -> `oe`) antes del filtrado en [`codigo.py:7-9`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L7-L9).
3. **Manejo defensivo de tipos:** No encontré conversión preventiva como `str(text)` ni control de `TypeError` en [`codigo.py:5-9`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L5-L9).

---

### Preguntas abiertas

1. ¿Es aceptable que locuciones o préstamos latinos utilizados en títulos en español como `"currículum vitæ"` o `"ex æquo"` pierdan la ligadura resultando en `"curriculum-vit"` y `"ex-quo"`, o deben expandirse a `"curriculum-vitae"` y `"ex-aequo"`?
2. ¿Debe [`slugify`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-3-cf96a86d/codex-forjador--eval-slug-b3/codigo.py#L5) aceptar coerción de tipos no string mediante `str(text)` o es requisito estricto fallar con `TypeError`?
3. Cuando un título contiene únicamente signos o emojis y genera `""`, ¿es correcto que retorne una cadena vacía o debe existir un valor por defecto / identificador alternativo?

---

Veredicto: APROBAR CON CAMBIOS
RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:7-9, test_visible.py:10-24, pytest (9 passed)
SIGUIENTE PASO: nada