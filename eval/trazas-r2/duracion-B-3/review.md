### Mapa (qué hay y dónde)

- [`codigo.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/codigo.py): Implementación de la función [`parse_duration`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/codigo.py#L4-L16) utilizando expresiones regulares para convertir cadenas de tiempo con unidades `h`, `m`, `s` a segundos.
- [`test_visible.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py): Suite de pruebas pytest con [`test_basico`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L6-L7), [`test_combinaciones_validas`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L10-L28) (12 casos parametrizados) y [`test_entradas_invalidas`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L31-L42) (24 casos parametrizados).
- [`issue.md`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/issue.md): Especificación del requerimiento/bug report original.
- [`.orquesta/proyecto.toml`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/.orquesta/proyecto.toml): Configuración del comando de test del entorno (`pytest -q -p no:cacheprovider test_visible.py`).

---

### Hallazgos (con citas)

#### Severidad Media: Casos borde y cobertura incompleta en la suite de pruebas
- **Evidencia textual ([`test_visible.py:10-26`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L10-L26) y [`test_visible.py:31-39`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L31-L39)):**
  ```python
  @pytest.mark.parametrize(
      ("text", "expected"),
      [
          ("2h", 7200),
          ("30m", 1800),
          ("45s", 45),
          ...
      ],
  )
  ...
  @pytest.mark.parametrize(
      "text",
      [
          "", "basura", "123", "h", "1hbasura", "1h30mextra", "1h30",
          ...
          "-1h", "+1m", "1.5h", " 1h", "1h ", "1h 2m", "1h\n", "1H",
          None, 45,
      ],
  )
  ```
- **Lo que leí:** La suite cubre combinaciones básicas y casos con ceros (`"0s"`, `"0h0m0s"`). En las entradas inválidas prueba `"1H"`, pero no prueba las variantes en mayúscula de minutos o segundos (`"1M"`, `"1S"`). Tampoco se evalúan unidades individuales aisladas en cero (`"0h"`, `"0m"`), valores unitarios simples (`"1h"`, `"1m"`, `"1s"`), ceros intermedios (`"1h0m30s"`), unidades truncadas sin número intermedio (`"1hm"`, `"1ms"`), ni números con separador decimal por coma (`"1,5h"`).
- **Lo que infiero:** Aunque la expresión regular actual rechaza o calcula estos casos de forma consistente, la falta de aserciones explícitas para estas variantes deja huecos en la red de seguridad ante futuras modificaciones o refactorizaciones.

#### Severidad Baja: Compilación de la expresión regular en cada llamada
- **Evidencia textual ([`codigo.py:9-12`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/codigo.py#L9-L12)):**
  ```python
  match = (
      re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", text)
      if isinstance(text, str) else None
  )
  ```
- **Lo que leí:** La expresión regular se pasa como literal de cadena a `re.fullmatch` dentro del cuerpo de la función [`parse_duration`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/codigo.py#L4-L16).
- **Lo que infiero:** Aunque Python utiliza internamente `re._cache`, es una práctica estándar compilar el patrón una sola vez a nivel de módulo con `re.compile(...)` para evitar sobrecargas innecesarias en ejecuciones repetitivas o en bucles de alto volumen.

#### Severidad Baja: Captura de tipos no string mediante `ValueError`
- **Evidencia textual ([`codigo.py:4`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/codigo.py#L4), [`codigo.py:11`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/codigo.py#L11), [`codigo.py:13-14`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/codigo.py#L13-L14) y [`test_visible.py:37`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L37)):**
  ```python
  def parse_duration(text: str) -> int:
  ...
      if isinstance(text, str) else None
  ...
  if match is None or not text:
      raise ValueError("Duración inválida: usá enteros no negativos con h, m, s en ese orden")
  ```
- **Lo que leí:** La firma declara `text: str`, pero ante un valor no string ([`test_visible.py:37`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L37): `None`, `45`) asigna `match = None` y eleva `ValueError`.
- **Lo que infiero:** En Python idiomático, los tipos incorrectos suelen lanzar `TypeError` en lugar de `ValueError`. Esta decisión responde al diseño del test parametrizado en [`test_visible.py:37-42`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/test_visible.py#L37-L42), pero representa un compromiso en el diseño canónico de la interfaz.

---

### Qué NO encontré

- No encontré ningún caso de prueba ni entrada válida/inválida que rompa el comportamiento funcional exigido en [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/duracion-b-3-ea22612b/codex-forjador--eval-duracion-b3/issue.md#L1).
- No encontré precompilación del patrón regex a nivel de módulo.
- No encontré pruebas de límites numéricos para cadenas con cantidades masivas de dígitos.

---

### Preguntas abiertas

- ¿Es intencional que entradas de tipos no compatibles (como `None` o enteros) lancen `ValueError` en lugar de respetar la convención estándar de `TypeError` de Python?
- ¿Se contempla en el futuro tolerar espacios en blanco circundantes mediante sanitización (`strip()`), o la especificación exige de manera estricta que cualquier espacio resulte en `ValueError`?

---

Veredicto: APROBAR CON CAMBIOS
RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:9-16, test_visible.py:10-43
SIGUIENTE PASO: nada