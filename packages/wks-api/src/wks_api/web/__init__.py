"""Bundled same-origin human workspace, outside the reusable Core."""

from pathlib import Path

from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles


class WorkspaceFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
            "media-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def install_workspace(app):
    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/app/")

    app.mount(
        "/app",
        WorkspaceFiles(directory=Path(__file__).parent / "assets", html=True),
        name="workspace",
    )
