"""Vercel serverless entry point for agent-context-handoff API.

Vercel Python functions expect a handler callable. We wrap the FastAPI
ASGI app with Mangum for AWS Lambda compatibility.
"""

from __future__ import annotations

import os
import sys

# Add src/ to Python path so `handoff` imports work
_project_root = os.path.dirname(os.path.dirname(__file__))
_src_dir = os.path.join(_project_root, "src")
sys.path.insert(0, _src_dir)

from handoff.api.server import create_app  # noqa: E402
from mangum import Mangum  # noqa: E402

app = create_app()
handler = Mangum(app, lifespan="off")
