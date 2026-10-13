# Salida JSON de la CLI

Los comandos que usan el sobre lo imprimen como JSON en una sola línea, con caracteres Unicode sin escapar.
Siempre incluye estas claves:

| Clave | Contenido |
|---|---|
| `version` | Versión del contrato: `1`. |
| `comando` | Comando que produjo la respuesta. |
| `estado` | Uno de los estados de la tabla siguiente. |
| `generacion` | Identificador de generación, o `null` si no corresponde. |
| `datos` | Resultado del comando; por defecto, `{}`. |
| `errores` | Errores; por defecto, `[]`. |
| `avisos` | Avisos; por defecto, `[]`. |

| Estado | Significado | Exit code |
|---|---|---|
| `OK` | El comando terminó correctamente. | `0` |
| `NECESITO_CONFIRMACION` | Hace falta confirmación humana. | `3` |
| `BLOQUEADO` | Una condición impide continuar. | `3` |
| `RATE_LIMIT` | El backend alcanzó su límite de uso. | `1` |
| `ORQUESTA_OFF` | El proyecto tiene la delegación desactivada. | `1` |
| `OCUPADO` | El recurso necesario está ocupado. | `1` |
| `ERROR` | El comando falló. | `1` |

El constructor rechaza estados desconocidos con `ValueError`.
`exit_code()` devuelve `1` para cualquier estado distinto de `OK`,
`NECESITO_CONFIRMACION` y `BLOQUEADO`.

## Review

`orquesta review RAMA [--repo DIR] [--revisor NOMBRE]` devuelve el sobre JSON.
`--revisor` permite elegir un agente del manifiesto que sea `read-only` y tenga
un backend distinto del autor (otra familia). Sin la opción se usa la selección
automática habitual. Si el revisor indicado no cumple esas condiciones, devuelve
`ERROR` (exit 1) con el motivo en `errores`, sin ejecutar tests ni review y sin
modificar la evidencia previa. Una review válida registra el nombre en `datos.revisor`.

## `orquesta ledger`

`orquesta ledger anotar TAREA (--progreso | --sin-progreso) [--nota TEXTO] [--repo DIR]`
registra un intento y devuelve su estado en `datos`: `tarea`, `intentos`, `corridas`,
`sin_progreso` (racha final), `cerrada` y `accion`. La nota se recorta a 500 caracteres.
Con 2 intentos consecutivos sin progreso devuelve `OK` (exit 0), acción `replanificar`
y un aviso para armar un brief nuevo sin historial. Con 4 sin progreso o 6 intentos
totales devuelve `BLOQUEADO` (exit 3), acción `escalar` y el motivo
`tope alcanzado: escalá al usuario antes de otro intento`; el intento queda anotado.
La escalada tiene prioridad y el progreso reinicia solamente la racha.

`orquesta ledger cerrar TAREA --motivo TEXTO [--repo DIR]` guarda el motivo y cierra
la tarea; desde entonces no acepta intentos. `orquesta ledger ver [--repo DIR]`
devuelve los estados en `datos.tareas`. Ambos devuelven `OK` (exit 0).
El proyecto se identifica por el repo git del directorio actual o de `--repo`,
resolviendo los worktrees registrados al repo original. Los comandos usan el sobre
JSON con `comando` igual a `ledger anotar`, `ledger cerrar` o `ledger ver`.
Un slug inválido, una tarea cerrada al anotar o un ledger corrupto devuelve `ERROR` (exit 1).

`orquesta run AGENTE --task TAREA` reserva y cuenta cada corrida en el mismo ledger,
bajo lock, antes de delegar. Con 8 corridas la acción pasa a `escalar`: la octava se
ejecuta y la novena queda bloqueada, aunque el orquestador no haya anotado progreso.
Ese margen sobre los 6 intentos anotados permite reintentos dentro de una ronda.
Las entradas viejas sin `corridas` se leen como 0; sin `--task` no se toca el ledger.
La clave conserva `TAREA` si es un slug válido; de lo contrario se normaliza con `slugify`.
Si antes de reservar la tarea está cerrada o su acción es `escalar`,
devuelve `ESTADO: BLOQUEADO` (exit 3) sin ejecutar
el backend, con el detalle `tarea cerrada en el ledger` o
`tope del ledger alcanzado: escalá al usuario`. Las anotaciones de progreso siguen
contando por separado en `intentos`; el tope de corridas lo aplica el runner por sí solo.

## Confirmación humana

El modelo no confirma; sin TTY disponible, se devuelve NECESITO_CONFIRMACION con el comando sugerido para que lo corra el usuario.

La confirmación abre `/dev/tty` para leer y escribir. Te pide:
`Para confirmar, escribí «texto esperado»: `. Solo acepta el texto exacto,
tras quitar espacios al principio y al final. No hay variables de entorno ni
flags que salteen la confirmación.

Si no puede abrir la terminal, `pedir_confirmacion()` lanza `SinTTY` con el
atributo `comando_sugerido`. Los comandos que confirman (`merge`, `descartar`,
`generate --confirm`, `migrar corte|rollback`) convierten esa excepción en el
sobre `NECESITO_CONFIRMACION` (exit 3) con el comando exacto, que el usuario corre
en una segunda terminal (`!` de Claude Code tampoco tiene TTY).

## `orquesta usage`

`orquesta usage [--dias N]` analiza el uso por agente de los últimos N días (default: 30).
Lee `state/usage.jsonl` dentro de `ORQUESTA_HOME` (o la raíz de orquesta si no está definido).
Devuelve directamente un array JSON en una sola línea, ordenado por `agent`, sin el sobre
versionado. Sale con código `0`; si no hay invocaciones en la ventana, devuelve `[]`.

```json
[{"agent":"codex-forjador","llamadas":4,"tokens":1200,"latencia_media_s":3.5,"errores":1,"tasa_error":0.25,"backend":"codex"}]
```

| Campo | Contenido |
|---|---|
| `agent` | Identificador del agente. |
| `llamadas` | Invocaciones registradas en la ventana. |
| `tokens` | Suma de `tokens_in` y `tokens_out`; `null` cuenta como `0`. |
| `latencia_media_s` | Promedio de latencias disponibles, en segundos; `0.0` si no hay muestras. |
| `errores` | Invocaciones cuyo estado no es `ok` ni `OK` (formato del runner). |
| `tasa_error` | `errores / llamadas`. |
| `backend` | Backend si es único en la ventana; `null` si hay múltiples. |

La ventana incluye el límite inferior y excluye el instante actual:
`now - N * 86400 <= timestamp < now`. Se acepta `timestamp` Unix o ISO y el campo `ts`
ISO que escribe el registro actual. Los archivos ausentes y las líneas inválidas se omiten (JSON roto, `agent` o `backend` que no son texto, o tokens y latencia que no son números); una línea inválida se descarta entera. `--dias` tiene que estar entre 1 y 36500: fuera de ese rango el comando termina con error. Un timestamp ISO sin zona se interpreta como UTC, y `NaN`/`Infinity` en la latencia y tokens que no sean enteros invalidan la línea.
No se agrega una línea base «directo»: el registro no contiene ese dato.
