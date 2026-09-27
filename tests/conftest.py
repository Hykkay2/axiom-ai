import os
import tempfile

# `kickbacks.app` builds a default app at import time; keep its database out of the repo.
os.environ.setdefault("KICKBACKS_DB", os.path.join(tempfile.mkdtemp(), "import.db"))
