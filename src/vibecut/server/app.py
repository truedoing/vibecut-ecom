"""FastAPI 应用工厂。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from ..config import Settings, load_settings
from ..core.db import Database
from .files import media_type_for
from .routes import assets, desub, health, tasks


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    app = FastAPI(title="VibeCut Ecom API", version="0.1.0")
    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.db.init()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(tasks.router)
    app.include_router(assets.router)
    app.include_router(desub.router)

    dist = Path(settings.frontend_dir)

    @app.get("/", include_in_schema=False)
    def index():
        html = dist / "index.html"
        if html.exists():
            return FileResponse(html)
        return JSONResponse({
            "ok": True,
            "message": "VibeCut Ecom API 已启动；前端未构建（web/dist 不存在）",
            "docs": "/docs",
        })

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path == "api":
            raise HTTPException(404, "Not Found")
        target = (dist / path).resolve()
        if dist.exists() and dist.resolve() in target.parents and target.is_file():
            return FileResponse(target, media_type=media_type_for(target))
        html = dist / "index.html"
        if html.exists():
            return FileResponse(html)
        raise HTTPException(404, "Not Found")

    return app
