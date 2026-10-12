"""La demo offline (examples/demo) recorre el flujo completo con backends guionados: tiene que seguir andando."""
import subprocess
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo" / "run_demo.py"


def test_la_demo_offline_corre_de_punta_a_punta():
    r = subprocess.run([sys.executable, str(DEMO)], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    for marca in ("VIOLACION_READ_ONLY", "RATE_LIMIT", "NECESITO_CONFIRMACION", "debilita los tests"):
        assert marca in r.stdout
