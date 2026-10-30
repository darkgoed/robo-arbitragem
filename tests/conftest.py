"""Keep module-level app initialization away from production data and credentials."""

import os
import tempfile
import atexit

_home = tempfile.TemporaryDirectory(prefix="robo-pytest-", dir=os.environ.get("TMPDIR"))
os.environ["ROBO_HOME"] = _home.name
atexit.register(_home.cleanup)
