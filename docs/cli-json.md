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
ISO que escribe el registro actual. Los archivos ausentes y las líneas inválidas se omiten (JSON roto, `agent` o `backend` que no son texto, o tokens y latencia que no son números); una línea inválida se descarta entera. `--dias` tiene que ser mayor que 0: con `0` o un valor negativo el comando termina con error.
No se agrega una línea base «directo»: el registro no contiene ese dato.
