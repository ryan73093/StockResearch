"""Deploy only authentication while preserving the installed research package."""
import argparse
import hashlib
import shutil
import sysconfig
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--check-only", action="store_true")
check = parser.parse_args().check_only
source = root / "src/quant_platform/dashboard"
installed = Path(sysconfig.get_path("purelib")) / "quant_platform/dashboard"
state = root / ".runtime/home-auth-last.sha256"
if not state.is_file():
    raise SystemExit("The deployed authentication baseline has not been recorded.")
current = hashlib.sha256((installed / "cloudflare_access.py").read_bytes()).hexdigest()
if current != state.read_text().strip():
    raise SystemExit("Installed authentication changed since preflight; deployment stopped safely.")
if not (source / "pimi_sso/client.py").is_file():
    raise SystemExit("Home SSO adapter is missing.")
if check:
    print("Installed authentication baseline verified; unrelated research files remain installed.")
    raise SystemExit(0)
backup = root / ".runtime/home-auth-original"
backup.mkdir(parents=True, exist_ok=True)
original = backup / "cloudflare_access.py"
if not original.exists():
    shutil.copy2(installed / "cloudflare_access.py", original)
shutil.copy2(source / "cloudflare_access.py", installed / "cloudflare_access.py")
shutil.copytree(source / "pimi_sso", installed / "pimi_sso", dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
state.write_text(hashlib.sha256((installed / "cloudflare_access.py").read_bytes()).hexdigest())
print("Home authentication deployed; installed research modules are unchanged.")
