#!/usr/bin/env python3
"""Backend falso para los tests: imita lo justo de `codex exec` y `agy -p`.

Uso: fake_backend.py <codex|agy> [flags...]. El comportamiento sale del prompt:
  WRITE:<archivo>   crea ese archivo en el directorio de trabajo (-C o cwd)
  STAGE:<archivo>   agrega ese archivo al índice de git después de escribir
  COMMIT           agrega todos los cambios y los commitea en el cwd
  SLEEP:<seg>       duerme antes de responder
  RATELIMIT         sale con 1 y un mensaje de límite de uso
  EMPTY             no imprime nada (exit 0)
  STATUSFAIL        (agy) devuelve status != SUCCESS
  MENTION_LIMIT     respuesta exitosa que menciona "usage limit" / "quota reached"
  ECHOENV:<VAR>     imprime el valor de esa variable de entorno y sale
  ECHOARGS          responde con un JSON de los argumentos, cwd y prompt recibidos
Si no, responde "PONG: <primera línea del prompt>".
"""
import json
import os
import re
import subprocess
import sys
import time

kind, args = sys.argv[1], sys.argv[2:]
prompt, cwd = None, os.getcwd()
i = 0
while i < len(args):
    a = args[i]
    if kind == "codex":
        if a == "-C":
            cwd = args[i + 1]
            i += 2
            continue
        if a in ("--color", "--sandbox", "-m"):
            i += 2
            continue
        if a == "-":
            prompt = sys.stdin.read()
            i += 1
            continue
        prompt = a
        i += 1
    else:
        if a == "-p":
            prompt = args[i + 1]
            i += 2
            continue
        if a in ("--output-format", "--model", "--mode"):
            i += 2
            continue
        i += 1
prompt = prompt or ""

m = re.search(r"SLEEP:(\d+(?:\.\d+)?)", prompt)
if m:
    time.sleep(float(m.group(1)))
for destino in re.findall(r"WRITE:(\S+)", prompt):
    with open(os.path.join(cwd, destino), "w", encoding="utf-8") as fh:
        fh.write("escrito por el backend falso\n")
m = re.search(r"STAGE:(\S+)", prompt)
if m:
    subprocess.run(["git", "add", "--", m.group(1)], cwd=cwd, check=True)
if re.search(r"\bCOMMIT\b", prompt):
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=cwd, check=True)
if "RATELIMIT" in prompt:
    print("ERROR: You've hit your usage limit / quota reached. Try again later.", file=sys.stderr)
    sys.exit(1)
if "EMPTY" in prompt:
    sys.exit(0)

m = re.search(r"ECHOENV:(\w+)", prompt)
if m:
    print(os.environ.get(m.group(1), ""))
    sys.exit(0)
if "ECHOARGS" in prompt:
    text = json.dumps({"args": args, "cwd": cwd, "prompt": prompt})
elif "MENTION_LIMIT" in prompt:
    text = "El código maneja bien el usage limit y el caso quota reached."
else:
    first = next((ln for ln in prompt.splitlines() if ln.strip()), "")
    text = f"PONG: {first}"

print("progreso del backend...", file=sys.stderr)
if kind == "agy":
    status = "ERROR" if "STATUSFAIL" in prompt else "SUCCESS"
    print(json.dumps({"status": status, "response": text + "\n"}))
else:
    print(text)
