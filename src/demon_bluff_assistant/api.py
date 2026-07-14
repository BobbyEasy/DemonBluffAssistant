from __future__ import annotations

from importlib.resources import files

from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from demon_bluff_assistant.analysis_archive import AnalysisArchive
from demon_bluff_assistant.capture import CaptureError
from demon_bluff_assistant.config import Settings
from demon_bluff_assistant.local_vision import LocalRecognitionError, LocalVisionService
from demon_bluff_assistant.model_config import (
    ModelConfigError,
    ModelConfigStore,
    ModelProvider,
    ModelSettingsUpdate,
)
from demon_bluff_assistant.models import ChatRequest, StatePatch, VillageConfig
from demon_bluff_assistant.openai_service import (
    AdviceValidationError,
    IntegrationUnavailable,
    OpenAIService,
)
from demon_bluff_assistant.solver import WorldSolver
from demon_bluff_assistant.store import SessionNotFound, SessionStore


def create_app(
    *,
    settings: Settings,
    store: SessionStore,
    solver: WorldSolver,
    openai_service: OpenAIService,
    local_vision: LocalVisionService,
    model_store: ModelConfigStore,
    captures,
    analysis_archive: AnalysisArchive | None = None,
    serve_static: bool = True,
) -> FastAPI:
    analysis_archive = analysis_archive or AnalysisArchive(
        settings.data_dir / "analysis.db"
    )
    app = FastAPI(title="Demon Bluff Assistant", version="0.4.0")
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )

    @app.exception_handler(SessionNotFound)
    async def session_not_found(_, exc: SessionNotFound):
        return JSONResponse(status_code=404, content={"detail": f"局面不存在：{exc.args[0]}"})

    @app.exception_handler(CaptureError)
    async def capture_error(_, exc: CaptureError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(IntegrationUnavailable)
    async def integration_error(_, exc: IntegrationUnavailable):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ModelConfigError)
    async def model_config_error(_, exc: ModelConfigError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(LocalRecognitionError)
    async def local_recognition_error(_, exc: LocalRecognitionError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/config")
    def get_config():
        public = settings.public_dict()
        models = model_store.public_view(settings)
        active = next(
            item for item in models.providers if item.provider == models.active_provider
        )
        vision = next(
            item for item in models.providers if item.provider == ModelProvider.OPENAI
        )
        glm_vision = next(
            item for item in models.providers if item.provider == ModelProvider.ZHIPU
        )
        public.update(
            {
                "provider": active.provider,
                "model": active.model,
                "strategy_configured": active.configured,
                "openai_configured": vision.configured,
                "glm_vision_configured": glm_vision.configured,
                "recognition_mode": "local",
                "local_ocr_available": True,
            }
        )
        return public

    @app.get("/api/model-settings")
    def get_model_settings():
        return model_store.public_view(settings)

    @app.put("/api/model-settings")
    def update_model_settings(update: ModelSettingsUpdate):
        model_store.update(update)
        return model_store.public_view(settings)

    @app.get("/api/roles")
    def get_roles():
        return {
            "version": solver.catalog.version,
            "roles": [role.model_dump() for role in solver.catalog.roles.values()],
        }

    @app.post("/api/sessions", status_code=status.HTTP_201_CREATED)
    def create_session(config: VillageConfig):
        return store.create(config)

    @app.post("/api/sessions/import", status_code=status.HTTP_201_CREATED)
    def import_session(payload: dict):
        return store.import_state(payload)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        return store.get(session_id)

    @app.post("/api/sessions/{session_id}/events")
    def confirm_events(session_id: str, patch: StatePatch):
        state = store.apply_patch(session_id, patch)
        analysis_archive.record_recognition(session_id, patch, state)
        return state

    @app.post("/api/sessions/{session_id}/undo")
    def undo(session_id: str):
        return store.undo(session_id)

    @app.get("/api/sessions/{session_id}/export")
    def export(session_id: str):
        return store.export_state(session_id)

    @app.get("/api/sessions/{session_id}/analysis")
    def analyze(session_id: str):
        game_state = store.get(session_id)
        report = solver.solve(game_state)
        try:
            advice = openai_service.generate_advice(game_state, report)
        except (IntegrationUnavailable, AdviceValidationError):
            advice = openai_service.template_advice(report)
        analysis_archive.record_analysis(session_id, game_state, report, advice)
        return {"report": report, "advice": advice}

    @app.get("/api/sessions/{session_id}/analysis/export")
    def export_analysis(session_id: str):
        store.get(session_id)
        bundle = analysis_archive.export_latest(session_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="请先完成一次局面分析。")
        return bundle

    @app.get("/api/dataset/export")
    def export_dataset():
        return analysis_archive.export_dataset()

    @app.get("/api/sessions/{session_id}/chat")
    def chat_history(session_id: str):
        store.get(session_id)
        return {"messages": analysis_archive.chat_history(session_id)}

    @app.post("/api/sessions/{session_id}/chat")
    def chat(session_id: str, request: ChatRequest):
        game_state = store.get(session_id)
        report = solver.solve(game_state)
        history = analysis_archive.chat_history(session_id)
        answer = openai_service.continue_strategy_chat(
            game_state, report, history, request.message
        )
        analysis_archive.add_chat_exchange(session_id, request.message, answer)
        return {
            "message": {"role": "assistant", "content": answer},
            "messages": analysis_archive.chat_history(session_id),
        }

    @app.delete(
        "/api/sessions/{session_id}/chat", status_code=status.HTTP_204_NO_CONTENT
    )
    def clear_chat(session_id: str):
        store.get(session_id)
        analysis_archive.clear_chat(session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/captures")
    def capture_now():
        return captures.capture_now()

    @app.get("/api/captures/latest")
    def latest_capture():
        return captures.latest()

    @app.get("/api/captures/{capture_id}/image")
    def capture_image(capture_id: str):
        png = captures.registry.get(capture_id)
        if png is None:
            raise HTTPException(status_code=404, detail="截图不存在或已过期。")
        return Response(
            content=png,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/captures/{capture_id}/parse")
    def parse_capture(
        capture_id: str,
        session_id: str = Query(...),
        engine: str = Query("local", pattern="^(local|openai|glm)$"),
    ):
        png = captures.registry.get(capture_id)
        if png is None:
            raise HTTPException(status_code=404, detail="截图不存在或已过期。")
        game_state = store.get(session_id)
        if engine == "openai":
            return openai_service.parse_capture(png, game_state)
        if engine == "glm":
            return openai_service.parse_capture_zhipu(png, game_state)
        return local_vision.parse_capture(png, game_state)

    @app.post("/api/captures/{capture_id}/village")
    def parse_village(
        capture_id: str,
        engine: str = Query("local", pattern="^(local|openai|glm)$"),
    ):
        png = captures.registry.get(capture_id)
        if png is None:
            raise HTTPException(status_code=404, detail="截图不存在或已过期。")
        if engine == "openai":
            return openai_service.parse_village(png)
        if engine == "glm":
            return openai_service.parse_village_zhipu(png)
        return local_vision.parse_village(png)

    if serve_static:
        static_dir = files("demon_bluff_assistant").joinpath("static")
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(str(static_dir.joinpath("index.html")))

    return app
