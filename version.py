"""The git commit the running server was built from, for display in the UI.

Two sources, in order:

1. The ``GIT_SHA`` environment variable — baked into the Docker image at build
   time (the ``.git`` dir is excluded from the build context, so the deploy
   passes the short SHA as a ``--build-arg``; see the Dockerfile and Makefile).
2. ``git rev-parse --short HEAD`` in the source tree — the local fallback, since
   single-user mode runs straight from a checkout.

Returns ``None`` when neither is available (e.g. an image built without the
build-arg, run outside a checkout). The result is computed once and cached.
"""

import os
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_cached = False
_sha = None


def git_sha():
    """The abbreviated commit SHA of the running code, or None if unknown."""
    global _cached, _sha
    if _cached:
        return _sha
    _cached = True
    sha = (os.environ.get("GIT_SHA") or "").strip()
    if not sha:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=_HERE, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            sha = ""
    _sha = sha or None
    return _sha
