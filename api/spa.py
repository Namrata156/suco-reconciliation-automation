"""
Serves the production SPA build from Django (Phase 5 design 2.4 / P9: "without
an answer from IT, Slice 1 builds for 'Django serves everything'").

The design names WhiteNoise for this; it is NOT added (no new packages
without tracker sign-off), so this uses Django's own django.views.static.serve,
which is adequate for an intranet with a handful of users but has no
compression or far-future caching. Once IT answers P9, a web server
(nginx / IIS) serving frontend/dist should take over and this hook can stay
unused.

Inert when settings.FRONTEND_DIST_DIR has no index.html: every path answers
404, exactly as before the hook existed.

  <path> is a file inside the build (assets/..., favicon) -> that file
  anything else outside /api/, /admin/, /static/           -> index.html
                                                              (the SPA's own
                                                              router decides)
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils._os import safe_join
from django.views.decorators.http import require_safe
from django.views.static import serve


def _dist() -> Path:
    return Path(settings.FRONTEND_DIST_DIR)


@require_safe
def spa(request, path: str = ""):
    dist = _dist()
    index = dist / "index.html"
    if not index.is_file():
        raise Http404("The front-end build (frontend/dist) is not present")
    if path:
        try:
            candidate = Path(safe_join(dist, path))
        except Exception:  # SuspiciousFileOperation: path escapes dist
            raise Http404("Not found") from None
        if candidate.is_file():
            return serve(request, path, document_root=str(dist))
        if path.startswith("assets/"):  # a missing hashed asset must not turn into HTML
            raise Http404("Not found")
    response = FileResponse(index.open("rb"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"  # always pick up a new build
    return response
