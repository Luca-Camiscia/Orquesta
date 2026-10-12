"""Los enlaces relativos de la documentación pública apuntan a archivos que existen."""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = [REPO / "README.md", REPO / "README.es.md", *sorted((REPO / "docs").glob("*.md")),
        REPO / "eval" / "README.md", REPO / "examples" / "demo" / "run_demo.py"]
ENLACE = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO)))
def test_enlaces_relativos_existen(doc):
    rotos = [destino for destino in ENLACE.findall(doc.read_text(encoding="utf-8"))
             if not destino.startswith(("http://", "https://", "mailto:"))
             and not (doc.parent / destino).exists()]
    assert not rotos, f"{doc.name}: {rotos}"
