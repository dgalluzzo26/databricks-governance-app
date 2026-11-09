"""Job entry point: makes the repo root importable, then runs provisioning.main."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from provisioning.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
