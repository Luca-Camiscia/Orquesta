"""Los resultados publicados de la eval (condición D) se reproducen desde las trazas, sin modelos."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "eval" / "reconstruir.py"


def test_las_trazas_reproducen_los_resultados_publicados():
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "18/18 match" in r.stdout
