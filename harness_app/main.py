"""FastAPI entrypoint for the coding harness app."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .service import HarnessAppService, HarnessRunRequest, TERMINAL_STATUSES


class RunCreate(BaseModel):
    repo_path: str = Field(..., min_length=1)
    instruction: str = Field(..., min_length=1)
    test_command: str = Field("python -m pytest -q", min_length=1)
    model: str = Field("gpt-5.4-mini", min_length=1)
    max_iterations: int = Field(3, ge=1, le=10)
    max_bash_calls: int = Field(10, ge=1, le=100)
    repo_timeout: int = Field(60, ge=1, le=3600)
    max_tokens: int = Field(4096, ge=256, le=100_000)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    model_timeout: int = Field(300, ge=1, le=3600)
    max_repo_bytes: int = Field(200_000, ge=1_000, le=5_000_000)
    max_file_bytes: int = Field(30_000, ge=1_000, le=1_000_000)
    apply: bool = False

    def to_request(self) -> HarnessRunRequest:
        return HarnessRunRequest(**self.model_dump())


def create_app(service: HarnessAppService | None = None) -> FastAPI:
    app = FastAPI(title="D_val Coding Harness", version="0.2.0")
    app.state.service = service or HarnessAppService()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_service() -> HarnessAppService:
        return app.state.service

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/api/runs", status_code=202)
    def create_run(payload: RunCreate, svc: HarnessAppService = Depends(get_service)) -> dict:
        try:
            state = svc.create_run(payload.to_request())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return state.to_dict()

    @app.get("/api/runs")
    def list_runs(svc: HarnessAppService = Depends(get_service)) -> list[dict]:
        return [run.to_dict() for run in svc.list_runs()]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, svc: HarnessAppService = Depends(get_service)) -> dict:
        try:
            return svc.get_run(run_id).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/diff")
    def get_diff(run_id: str, svc: HarnessAppService = Depends(get_service)) -> FileResponse:
        try:
            state = svc.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not state.diff_path or not Path(state.diff_path).exists():
            raise HTTPException(status_code=404, detail="diff not available")
        return FileResponse(state.diff_path, media_type="text/x-diff", filename=f"{run_id}.patch")

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, svc: HarnessAppService = Depends(get_service)) -> StreamingResponse:
        try:
            svc.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def stream():
            last_payload = ""
            while True:
                state = svc.get_run(run_id)
                payload = json.dumps(state.to_dict())
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                if state.status in TERMINAL_STATUSES:
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
