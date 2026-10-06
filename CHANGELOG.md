# Changelog de orquesta

Cada `orquesta generate` aplicado agrega una entrada acá, en el mismo commit. Lo más nuevo, arriba.

## 2026-10-06 08:55 — rrhh y reglas de no mergear

- plugin (~/orquesta/plugin): 7 archivos (1 creados, 6 modificados, 0 borrados)

## 2026-10-06 01:08 — Generar el plugin de Claude Code (fase legacy: también se escribe el plugin, sin instalarlo)

- plugin (~/orquesta/plugin): 26 archivos (26 creados, 0 modificados, 0 borrados)

## 2026-10-06 00:27 — Línea base de escaladas (tools, isolation, frontmatter, role)

- manifiesto de deploy actualizado

## 2026-10-06 00:07 — Migrar la línea base de prompts del guard

- manifiesto de deploy actualizado
- línea base de prompts migrada

## 2026-10-05 21:59 — Agregar claude-interrogador

- CLAUDE.md: bloque actualizado
- + agente `claude-interrogador` (claude, read-only)

## 2026-10-05 00:00 — Agregar reclutador Gemini y opinión opcional en RR. HH.

- CLAUDE.md: bloque actualizado
- + agente `gemini-reclutador` (agy, read-only)

## 2026-10-04 22:15 — Aplicar cambios desde la UI

- manifiesto de deploy actualizado

## 2026-10-04 21:21 — Reglas de orquestación basadas en evidencia (papers 2024-2026)

- CLAUDE.md: bloque actualizado

## 2026-10-04 20:54 — Tanda 2: personas mejoradas (guías Codex/Gemini), anti-disparadores, contrato de salida y 9 roles nuevos

- CLAUDE.md: bloque actualizado
- + agente `claude-arquitecto` (claude, read-only)
- + agente `claude-centinela` (claude, read-only)
- + agente `claude-detective` (claude, read-only)
- + agente `claude-juez` (claude, read-only)
- ~ agente `codex-abogado-del-diablo`
- ~ agente `codex-forjador`
- + agente `codex-perfilador` (codex, read-only)
- + agente `codex-refactorista` (codex, write)
- + agente `codex-tester` (codex, write)
- ~ agente `gemini-bibliotecario`
- + agente `gemini-investigador` (agy, read-only)
- + agente `gemini-ux-revisor` (agy, read-only)

## 2026-10-04 20:22 — Migración del manifiesto: registra plugins por agente

- manifiesto de deploy actualizado

## 2026-10-04 18:17 — E2E 7.4: mood por defecto ejecutivo (desde la UI)

- CLAUDE.md: bloque actualizado

## 2026-10-04 18:16 — E2E 7.3: deshabilito gemini-cronista

- CLAUDE.md: bloque actualizado
- - agente `gemini-cronista`

## 2026-10-04 18:16 — E2E 7.3: alta de gemini-cronista (agy, read-only, documentación)

- CLAUDE.md: bloque actualizado
- + agente `gemini-cronista` (agy, read-only)

## 2026-10-04 18:14 — Resincronizar gemini-bibliotecario.md con orquesta.yaml (wrapper_model haiku; el archivo desplegado tenía sonnet por edición manual)

- ~ agente `gemini-bibliotecario`

## 2026-10-04 17:36 — Deploy inicial: 4 agentes y bloque de CLAUDE.md

- CLAUDE.md: bloque creado
- + agente `codex-abogado-del-diablo` (codex, read-only)
- + agente `codex-forjador` (codex, write)
- + agente `gemini-bibliotecario` (agy, read-only)
- + agente `that-it-guy` (claude, write)
