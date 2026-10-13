### Mapa (qué hay y dónde)

- [`codigo.py:1-9`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/codigo.py#L1-L9): Implementación de la función `slugify(text: str) -> str` que normaliza mediante `NFKD`, descarta caracteres fuera de ASCII con `.encode("ascii", "ignore").decode("ascii")` y sustituye secuencias no alfanuméricas por guiones con `re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")`.
- [`test_visible.py:1-25`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/test_visible.py#L1-L25): Suite de tests visible del proyecto que conserva `test_basico()` e incorpora `test_slug_para_urls()` parametrizado con 8 casos de prueba.
- [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/issue.md#L1): Reporte del problema original: `"Bug report: `slugify` en codigo.py genera slugs que no sirven en URLs con títulos en español. Arreglalo."`.
- [`.orquesta/proyecto.toml:1-2`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/.orquesta/proyecto.toml#L1-L2): Configuración del comando de ejecución de tests (`pytest -q -p no:cacheprovider test_visible.py`).

---

### Hallazgos (con citas)

#### Severidad Media: Eliminación silenciosa de separadores tipográficos no ASCII adyacentes a palabras
- **Fuente**: [`codigo.py:7-8`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/codigo.py#L7-L8)
- **Cita textual**:
  ```python
  text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
  return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
  ```
- **Lo que leí**: En la línea 7 se descartan todos los caracteres no ASCII mediante `.encode("ascii", "ignore")` sobre la forma `NFKD`, antes de que la expresión regular `re.sub(r"[^a-z0-9]+", "-", ...)` de la línea 8 reemplace los caracteres no alfanuméricos por guiones.
- **Lo que infiero (diagnóstico)**: Los signos tipográficos no ASCII utilizados en textos y títulos en español como separadores (por ejemplo, el guion medio/semirraya `–` `\u2013`, la raya `—` `\u2014`, o las comillas angulares `« »`) no se descomponen en caracteres ASCII bajo `NFKD`. Al descartarse sin reemplazo previo, si no tienen espacios a sus lados, fusionan indebidamente las palabras adyacentes:
  - `"Madrid–Barcelona"` (guion en-dash para trayectos/intervalos) resulta en `'madridbarcelona'` en lugar de `'madrid-barcelona'`.
  - `"2020–2026"` resulta en `'20202026'` en lugar de `'2020-2026'`.
  - `"Capítulo 1—Introducción"` (raya em-dash) resulta en `'capitulo-1introduccion'`.
  - `"pregunta¿verdad?"` resulta en `'preguntaverdad'`.

#### Severidad Baja: Efectos colaterales de la descomposición por compatibilidad (NFKD) en caracteres numéricos
- **Fuente**: [`codigo.py:7`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/codigo.py#L7)
- **Cita textual**:
  ```python
  text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
  ```
- **Lo que leí**: La normalización utiliza el modo `NFKD` (Compatibility Decomposition).
- **Lo que infiero (diagnóstico)**: `NFKD` descompone caracteres de compatibilidad como fracciones o superíndices en sus caracteres componentes. Por ejemplo, `"½"` (`\u00BD`) se descompone en `'1'`, `'\u2044'` (fraction slash) y `'2'`. Al descartar la barra fraccionaria no ASCII con `ignore`, `"1½"` se convierte en `'112'` (concatenando 1 y 2 sin separador) en vez de omitirlo o separarlo como `'1-2'`. De forma similar, `"2²"` se convierte en `'22'`.

#### Severidad Baja: Cobertura de pruebas incompleta para casos borde
- **Fuente**: [`test_visible.py:10-24`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/test_visible.py#L10-L24)
- **Cita textual**:
  ```python
  @pytest.mark.parametrize(
      "titulo, esperado",
      [
          ("Canción del año", "cancion-del-ano"),
          ("ÁÉÍÓÚ Ñ Ü", "aeiou-n-u"),
          ("¡Qué pingüino! ¿Cómo está?", "que-pinguino-como-esta"),
          ("  Hola\t mundo\n  ", "hola-mundo"),
          ("Guía__de---viaje / 2026", "guia-de-viaje-2026"),
          ("Cancio\u0301n del an\u0303o", "cancion-del-ano"),
          ("", ""),
          (" ¡! --- ", ""),
      ],
  )
  def test_slug_para_urls(titulo, esperado):
      assert slugify(titulo) == esperado
  ```
- **Lo que leí**: Los casos de prueba parametrizados verifican tildes, diéresis, eñes, mayúsculas, combinaciones NFD y signos básicos rodeados o aislados con espacios.
- **Lo que infiero (diagnóstico)**: No se incluyen casos de prueba para:
  - Guiones tipográficos Unicode (`—`, `–`) sin espacios entre palabras.
  - Caracteres de compatibilidad (fracciones, superíndices).
  - Comportamiento ante entradas no válidas (por ejemplo, si se pasa `None`, `unicodedata.normalize` eleva `TypeError` no controlado).

---

### Qué NO encontré

- No encontré tratamiento previo ni sustitución de caracteres de puntuación/separación Unicode (como rayas `—` o semirrayas `–`) antes del filtrado ASCII en [`codigo.py:7`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/codigo.py#L7).
- No encontré pruebas con guiones tipográficos o signos de puntuación adyacentes a palabras sin espacio en [`test_visible.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/slug-b-1-e6685505/codex-forjador--eval-slug-b1/test_visible.py).
- No encontré validación de tipos sobre el argumento `text` para asegurar manejo controlado de excepciones o conversión de tipo.

---

### Preguntas abiertas

- ¿Los títulos con rayas o semirrayas sin espacios (por ejemplo, `"2020–2026"` o `"Madrid–Barcelona"`) deben separar los términos con guion (`"2020-2026"`, `"madrid-barcelona"`)?
- ¿El comportamiento de descomposición `NFKD` sobre caracteres como fracciones o superíndices (por ejemplo, `"1½"` -> `'112'`) es admisible o se requiere normalizar con `NFD` o sanitizar previamente?
- ¿La función debe aceptar únicamente `str` o debe permitir coerción de otros tipos mediante `str(text)`?

---

Veredicto: APROBAR CON CAMBIOS
RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:7
SIGUIENTE PASO: Reemplazar o preservar separadores y guiones tipográficos no ASCII antes de la codificación ASCII y agregar casos de prueba complementarios en test_visible.py