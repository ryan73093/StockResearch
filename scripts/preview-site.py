"""A preview of the site from the source tree on 127.0.0.1:5055, for UI work (docs/rules/ui.md 驗收).

The deployed service on 5000 is untouched: this is a second, read-only web process with no scheduler and no warm-up
thread; it reads the views the deployed service's warm-up keeps ready (so a view whose shape changed shows the OLD
shape until the change is deployed — templates must tolerate that anyway). Templates reload on every request; Python
changes need a restart. Run it without a window and stop it when the work is done:

    .venv\Scripts\python.exe scripts\preview-site.py

Only open pages with it (GET); do not post forms — it writes to the same database as the service.
"""
import os
import sys

os.environ["SCHEDULER_IN_WEB"] = "false"
os.environ["SCHEDULER_ENABLED"] = "false"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from quant_platform.dashboard import v2  # noqa: E402
from quant_platform.dashboard.app import build_container, create_app  # noqa: E402

v2._STARTED = 0.0          # use the views the deployed warm-up already wrote
app = create_app(build_container())
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.run(host="127.0.0.1", port=5055, debug=False, use_reloader=False, threaded=True)
