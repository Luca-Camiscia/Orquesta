### Mapa (qué hay y dónde)

- [`codigo.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/codigo.py#L1-L3): Contiene la definición de [`moving_average(xs, k)`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/codigo.py#L1-L3).
- [`test_visible.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/test_visible.py#L1-L6): Suite de pruebas visible del proyecto con una única función [`test_basico`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/test_visible.py#L4-L5).
- [`issue.md`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/issue.md#L1): Reporte del problema original que motivó el cambio.
- [`.orquesta/proyecto.toml`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/.orquesta/proyecto.toml#L1-L3): Configuración de ejecución de tests del worktree.
- Historial git: Commit base `bf1178c` («inicial») y commit del agente `02228c0` («orquesta(codex-forjador): eval-media-b3») que modifica la cota superior del rango en [`codigo.py:3`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/codigo.py#L3).

---

### Hallazgos (con citas)

#### Severidad Media: Casos borde sin validación de parámetros de entrada
- **Evidencia textual**:
  En [`codigo.py:1-3`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/codigo.py#L1-L3):
  ```python
  def moving_average(xs, k):
      """Promedio móvil de ventana k."""
      return [sum(xs[i:i + k]) / k for i in range(len(xs) - k + 1)]
  ```
- **Distinción leído vs inferido**:
  - *Leído*: La función recibe `xs` y `k` sin validación previa y calcula directamente `range(len(xs) - k + 1)` y división por `k`.
  - *Inferido (diagnóstico)*:
    1. Para `k = 0`: Al evaluar `/ k` ocurre `ZeroDivisionError: division by zero`.
    2. Para `k < 0`: Con `k = -1` y `xs = [1, 2, 3]`, la expresión evalúa `range(3 - (-1) + 1)` resultando en un rango de tamaño 5; la indexación por rebanada negativa `xs[i:i-1]` no falla pero genera valores anómalos (`[-3.0, -0.0, -0.0, -0.0, -0.0]`) en vez de señalar un argumento inválido.
    3. Para `k` no entero (ej. `k = 2.0`): `range()` falla con `TypeError: 'float' object cannot be interpreted as an integer`.

#### Severidad Media: Cobertura de tests insuficiente para la regresión y casos límite
- **Evidencia textual**:
  En [`test_visible.py:4-5`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/test_visible.py#L4-L5):
  ```python
  def test_basico():
      assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
  ```
- **Distinción leído vs inferido**:
  - *Leído*: El archivo de tests solo valida el caso estándar con 4 elementos y ventana 2. El agente no añadió tests nuevos en su cambio.
  - *Inferido (diagnóstico)*: No hay verificación automatizada para:
    1. Ventana unitaria (`k = 1`).
    2. Ventana igual al tamaño total (`k == len(xs)`).
    3. Ventana mayor a la lista (`k > len(xs)`), que retorna `[]`.
    4. Lista vacía (`xs = []`).
    5. Parámetros inválidos (`k <= 0`).

#### Severidad Baja: Riesgo de rendimiento por recomputación de sumas y rebanadas
- **Evidencia textual**:
  En [`codigo.py:3`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/codigo.py#L3):
  ```python
  return [sum(xs[i:i + k]) / k for i in range(len(xs) - k + 1)]
  ```
- **Distinción leído vs inferido**:
  - *Leído*: Cada iteración ejecuta `sum(xs[i:i + k])` sobre una nueva sublista.
  - *Inferido (diagnóstico)*: La complejidad temporal es $O((N - k + 1) \cdot k)$ con asignación de memoria intermedia por cada rebanada, en lugar de un algoritmo de ventana deslizante $O(N)$. En secuencias extensas con valores de `k` grandes existe degradación de rendimiento.

---

### Qué NO encontré

- No encontré validación de argumentos ni aserciones de tipos/rangos para `k` en [`codigo.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/codigo.py).
- No encontré tests adicionales en [`test_visible.py`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/test_visible.py) ni otros archivos de test en el repositorio.
- No encontré especificación sobre el comportamiento esperado ante entradas inválidas o vacías en [`issue.md:1`](file://~/.cache/orquesta-eval-op9mqnv5/home/worktrees/media-b-3-26b08518/codex-forjador--eval-media-b3/issue.md#L1) (solo menciona: `"Bug report: moving_average en codigo.py devuelve valores de más al final. Arreglalo."`).

---

### Preguntas abiertas

- ¿Debe la función rechazar explícitamente valores de `k <= 0` con una excepción (ej. `ValueError`)?
- ¿Se considera válido que `moving_average` retorne `[]` cuando `k > len(xs)`, o debe considerarse una condición de error?
- ¿Se requiere compatibilidad con iterables genéricos (generadores/iteradores) o la firma asume exclusivamente secuencias con soporte de longitud y rebanado?

---

Veredicto: APROBAR CON CAMBIOS

RESULTADO: OK
CONFIANZA: 95
EVIDENCIA: codigo.py:1-3, test_visible.py:4-5, issue.md:1
SIGUIENTE PASO: Incorporar validaciones para k <= 0 y ampliar la suite de tests con casos borde en test_visible.py