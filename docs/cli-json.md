# Salida JSON de la CLI

El sobre se imprime como JSON en una sola línea, con caracteres Unicode sin escapar.
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
