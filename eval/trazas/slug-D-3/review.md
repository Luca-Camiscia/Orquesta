### Mapa (qué hay y dónde)

- [issue.md:1](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/issue.md#L1): Reporte del problema original solicitando adaptar [`slugify`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L5-L8) para títulos en español.
- [.orquesta/proyecto.toml:1-2](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/.orquesta/proyecto.toml#L1-L2): Configuración del comando de test (`pytest` sin caché sobre [test_visible.py](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/test_visible.py)).
- [codigo.py:1-8](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L1-L8): Módulo principal con la función [`slugify`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L5-L8) modificada usando `unicodedata.normalize` y `re.sub`.
- [test_visible.py:1-25](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/test_visible.py#L1-L25): Suite de pruebas con el test básico original [`test_basico`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/test_visible.py#L6-L7) y un conjunto parametrizado nuevo [`test_slug_para_urls`](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/test_visible.py#L10-L25).

---

### Hallazgos (con citas)

#### Severidad Media: Fusión de palabras ante signos de puntuación no ASCII sin espacio contiguo
- **Evidencia textual**:
  En [codigo.py:7](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L7):
  ```python
  text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
  ```
  y en [codigo.py:8](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L8):
  ```python
  return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
  ```
- **Lo leído**: La decodificación ASCII descartando caracteres no representables (`encode("ascii", "ignore")`) se ejecuta en la línea 7, antes del reemplazo por guiones de la expresión regular en la línea 8.
- **Lo inferido (Diagnóstico)**: Signos tipográficos frecuentes en publicaciones en español (como la raya `—` U+2014, el guión corto `–` U+2013 o el apóstrofe curvado `’` U+2019) no se descomponen a letras ASCII en NFKD. Al descartarse silenciosamente en la línea 7 antes del regex, los términos adyacentes quedan fusionados sin ningún separador.
  - Ejemplo observado: `slugify("Madrid—Barcelona")` devuelve `"madridbarcelona"` en lugar de `"madrid-barcelona"`.
  - Ejemplo observado: `slugify("d’art")` devuelve `"dart"` en lugar de `"d-art"`.

#### Severidad Baja: Supresión completa de grafemas extranjeros o cultismos en español
- **Evidencia textual**:
  En [codigo.py:7](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L7):
  ```python
  text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
  ```
- **Lo leído**: La normalización NFKD seguida de ignorar no-ASCII solo preserva símbolos que poseen una descomposición compatible a un carácter ASCII estándar.
- **Lo inferido (Diagnóstico)**: Títulos en español que contienen latinismos o préstamos lingüísticos con ligaduras o grafemas no separables (`æ`, `œ`, `ø`, `ß`) sufren mutilación léxica en lugar de transliteración estándar:
  - Ejemplo observado: `slugify("curriculum vitæ")` produce `"curriculum-vit"` (se suprime `æ`).
  - Ejemplo observado: `slugify("cœur")` produce `"cur"` (se suprime `œ`).

#### Severidad Baja: Fracciones tipográficas Unicode colapsan dígitos contiguos
- **Evidencia textual**:
  En [codigo.py:7-8](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L7-L8).
- **Lo leído**: NFKD descompone `½` en `1` + barra de fracción U+2044 + `2`. La barra U+2044 no es ASCII.
- **Lo inferido (Diagnóstico)**: Al eliminarse la barra en la línea 7, los dígitos quedan pegados antes del regex:
  - Ejemplo observado: `slugify("½ kilo")` genera `"12-kilo"`.

#### Severidad Informativa: Cobertura de tests incompleta para casos de borde
- **Evidencia textual**:
  En [test_visible.py:10-22](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/test_visible.py#L10-L22):
  ```python
        ("¡Canción del año!", "cancion-del-ano"),
        ("ÁÉÍÓÚ Ü Ñ", "aeiou-u-n"),
        ("¡CANCIÓN DEL AÑO!", "cancion-del-ano"),
        ("Cancio\u0301n y nin\u0303ez", "cancion-y-ninez"),
        ("  Guía\tde\nEspaña  ", "guia-de-espana"),
        ("¿Qué tal? / 2026", "que-tal-2026"),
        ("--Hola___mundo---", "hola-mundo"),
        ("", ""),
        (" ¡¿?! / -- ", ""),
  ```
- **Lo leído**: Los casos parametrizados verifican vocales con tilde, diéresis, eñe, mayúsculas, cadenas NFD combinadas y espacios en blanco.
- **Lo inferido (Diagnóstico)**: No hay casos de prueba para signos de puntuación no ASCII sin espacio contiguo (como rayas `—`), caracteres no contemplados por NFKD directo, ni entradas numéricas/fracciones.

---

### Qué NO encontré

- No encontré en [issue.md:1](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/issue.md#L1) una especificación explícita sobre el tratamiento esperado de caracteres no alfanuméricos fuera del alfabeto español estándar (guiones tipográficos, apóstrofes curvados o latinismos).
- No encontré en [codigo.py:1-8](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/codigo.py#L1-L8) una etapa de preprocesamiento que transforme puntuación Unicode en separadores antes de la fase de descarte de bytes no ASCII.
- No encontré pruebas en [test_visible.py:1-25](file://~/.cache/orquesta-eval-bgkc9vx9/home/worktrees/slug-d-3-d15d1de8/codex-forjador--eval-slug-d3/test_visible.py#L1-L25) que desafíen la interacción entre caracteres Unicode no alfabéticos y los límites de palabra.

---

### Preguntas abiertas

1. ¿Se requiere que signos de puntuación Unicode sin espacio (por ejemplo la raya `—` o guión en títulos como `"Madrid—Barcelona"`) separen palabras con guión, o se considera aceptable el comportamiento actual de descarte silencioso?
2. ¿Debe el soporte de títulos en español contemplar transliteración de latinismos comunes con ligaduras (ej. `æ` a `ae`) o únicamente el alfabeto español estricto?

---

Veredicto: APROBAR CON CAMBIOS
RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:7-8
SIGUIENTE PASO: nada