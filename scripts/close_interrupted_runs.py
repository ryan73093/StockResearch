"""Close run records left "running" once scripts/stop-services.ps1 has stopped every service process.

Nothing of this project runs at that point, so such a record was interrupted. Uses the working tree's
code: during a deploy the installed package is still the previous version.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from quant_platform.config.settings import Settings  # noqa: E402
from quant_platform.scheduler.runner import close_interrupted_runs  # noqa: E402

closed = close_interrupted_runs(Settings.from_env().database_url)
if closed:
    print(f"Closed {closed} interrupted run record(s).")
