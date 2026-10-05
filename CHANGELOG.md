# Changelog de orquesta

Cada `orquesta generate` aplicado agrega una entrada acá, en el mismo commit. Lo más nuevo, arriba.

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
