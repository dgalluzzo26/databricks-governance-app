"""Job entry point: makes the repo root importable, then runs provisioning.main."""

import inspect
import os
import sys

# Serverless Python tasks exec this file without defining __file__.
_this_file = inspect.currentframe().f_code.co_filename
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(_this_file))))

from provisioning.main import main  # noqa: E402

main()
