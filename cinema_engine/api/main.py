"""
FastAPI application for AI Cinema Studio Engine.
Provides REST endpoints for media generation, RAG virtual cinematography,
audio synthesis, lip-sync, Remotion compositing, platform encoding,
Temporal phase gate management, cost governance, and WebSocket streaming.
"""

from contextlib import asynccontextmanager
from decimal import Decimal
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cinema_engine.config import get_settings
from cinema_engine.db import (
    CostModel,
    GovernanceLogModel,
    JobModel,
    ProjectModel,
    get_db,
    init_db,
    record_cost_entry,
    record_job,
)
from cinema_engine.models import (
    CinematicGenerateRequest,
    ColorGradeParams,
    CostSummary,
    DirectGenerateRequest,
    GenerationParams,
    JobResponse,
    JobStatus,
    LipSyncRequest,
    MediaType,
    ProjectCreateRequest,
    ProjectResponse,
    SpeechGenerateRequest,
)
from cinema_engine.post.encode import encode_all_platforms
from cinema_engine.post.grading import apply_color_grade, find_ffmpeg, list_available_luts
from cinema_engine.post.remotion_render import RemotionProps, render_remotion_composition
from cinema_engine.providers.registry import get_provider_registry
from cinema_engine.rag.presets import search_local_presets
from cinema_engine.rag.query import CinematographyDirective, get_rag_service


class ConnectionManager:
    """Manages live WebSocket subscriber connections per project."""

    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, project_id: str, websocket: WebSocket):
        await websocket.accept()
        if project_id not in self.active_connections:
            self.active_connections[project_id] = []
        self.active_connections[project_id].append(websocket)

    def disconnect(self, project_id: str, websocket: WebSocket):
        if project_id in self.active_connections:
            if websocket in self.active_connections[project_id]:
                self.active_connections[project_id].remove(websocket)

    async def broadcast(self, project_id: str, message: dict):
        if project_id in self.active_connections:
            for connection in self.active_connections[project_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass


ws_manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables
    await init_db()
    yield


app = FastAPI(
    title="AI Cinema Studio Engine API",
    description="Production AI video production system with multi-provider routing and cinematic finishing.",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
async def health_check():
    """System health check verifying dependencies, providers, and database."""
    settings = get_settings()
    ffmpeg_ok = False
    ffmpeg_path = None
    try:
        ffmpeg_path = find_ffmpeg()
        ffmpeg_ok = True
    except Exception:
        pass

    registry = get_provider_registry()
    providers_status = registry.list_providers()

    return {
        "status": "healthy",
        "version": "0.3.0",
        "environment": settings.app_env,
        "database": settings.database_url.split(":///")[0],
        "ffmpeg": {
            "available": ffmpeg_ok,
            "path": ffmpeg_path,
        },
        "providers": providers_status,
    }


@app.get("/api/v1/providers", tags=["Providers"])
async def list_providers():
    """List all registered generation providers and configuration status."""
    registry = get_provider_registry()
    return registry.list_providers()


@app.get("/api/v1/presets/search", tags=["RAG Cinematography"])
async def search_presets(
    category: str = Query(description="Preset category: camera, lighting, or effects"),
    q: str = Query(description="Query keywords or style description"),
    limit: int = Query(default=5, ge=1, le=20),
):
    """Search virtual cinematography presets."""
    return search_local_presets(category, q, limit=limit)


@app.get("/api/v1/luts", tags=["Post-Production"])
async def get_luts():
    """List all available 3D LUT presets for cinematic color grading."""
    return list_available_luts()


@app.post("/api/v1/grade", tags=["Post-Production"])
async def grade_video(
    video_path: str,
    params: Optional[ColorGradeParams] = None,
):
    """Run FFmpeg color grading on a video."""
    try:
        out_path = await apply_color_grade(video_path, params=params)
        return {
            "status": "success",
            "input_path": video_path,
            "graded_path": str(out_path),
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Color grading failed: {str(e)}",
        )


@app.post("/api/v1/composite", tags=["Post-Production"])
async def composite_video(props: RemotionProps):
    """Render Remotion composition (clips, audio, motion graphics)."""
    try:
        out = await render_remotion_composition(props)
        return {"status": "success", "composited_path": str(out)}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Compositing failed: {str(e)}",
        )


@app.post("/api/v1/encode", tags=["Post-Production"])
async def encode_deliverables(video_path: str):
    """Generate multi-platform encodes for YouTube, Shorts/Reels/TikTok, and LinkedIn."""
    try:
        results = await encode_all_platforms(video_path)
        return {"status": "success", "deliverables": results}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Platform encoding failed: {str(e)}",
        )


@app.post("/api/v1/projects", response_model=ProjectResponse, tags=["Projects"])
async def create_project(
    req: ProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new production project governed by the 26-Step Production SOP."""
    proj_id = f"proj_{uuid4().hex[:8]}"
    db_proj = ProjectModel(
        id=proj_id,
        name=req.name,
        client=req.client,
        format=req.format,
        status="pre_production",
        budget_ceiling_usd=req.budget_ceiling_usd,
        actual_cost_usd=Decimal("0.00"),
    )
    db.add(db_proj)
    await db.commit()

    return ProjectResponse(
        id=db_proj.id,
        name=db_proj.name,
        client=db_proj.client,
        format=db_proj.format,
        status=db_proj.status,
        budget_ceiling_usd=db_proj.budget_ceiling_usd,
        actual_cost_usd=db_proj.actual_cost_usd,
        created_at=db_proj.created_at,
        updated_at=db_proj.updated_at,
    )


@app.get("/api/v1/projects/{project_id}", response_model=ProjectResponse, tags=["Projects"])
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieve production project status."""
    proj = await db.get(ProjectModel, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectResponse(
        id=proj.id,
        name=proj.name,
        client=proj.client,
        format=proj.format,
        status=proj.status,
        budget_ceiling_usd=proj.budget_ceiling_usd,
        actual_cost_usd=proj.actual_cost_usd,
        created_at=proj.created_at,
        updated_at=proj.updated_at,
    )


@app.post("/api/v1/projects/{project_id}/approve-gate/{gate_id}", tags=["Governance"])
async def approve_phase_gate(
    project_id: str,
    gate_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Human-in-the-loop Phase Gate Approval (Gate 1, Gate 2, or Gate 3).
    Logs the sign-off audit trail and advances the project phase.
    """
    proj = await db.get(ProjectModel, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    gate_map = {
        1: ("Gate 1 Approved: Pre-production (Script, Shotlist, Prompts)", "production"),
        2: ("Gate 2 Approved: Production (Raw Shots & Audio QA)", "post_production"),
        3: ("Gate 3 Approved: Post-production (Final Master & Deliverables)", "published"),
    }
    if gate_id not in gate_map:
        raise HTTPException(status_code=400, detail="Invalid gate_id. Must be 1, 2, or 3.")

    action, next_status = gate_map[gate_id]
    proj.status = next_status

    log = GovernanceLogModel(
        project_id=project_id,
        action=action,
        severity="info",
        details={"gate": gate_id, "approved": True},
    )
    db.add(log)
    await db.commit()

    # Broadcast event via WebSocket
    await ws_manager.broadcast(project_id, {
        "event": "gate_approved",
        "project_id": project_id,
        "gate": gate_id,
        "status": next_status,
    })

    return {"status": "approved", "gate": gate_id, "project_status": next_status}


@app.post("/api/v1/generate", response_model=JobResponse, tags=["Generation"])
async def generate_media(
    request: DirectGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Direct generation endpoint with automatic LUT color grading and cost tracking."""
    registry = get_provider_registry()
    video_provider = registry.get_video_provider(request.provider)

    gen_params = GenerationParams(
        prompt=request.prompt,
        duration_sec=request.duration_sec,
        aspect_ratio=request.aspect_ratio,
        ref_image_url=request.ref_image_url,
        seed=request.seed,
        model=request.model,
    )

    job = await video_provider.generate_video(gen_params)

    if job.status == JobStatus.COMPLETED and job.local_path and request.auto_grade:
        try:
            graded = await apply_color_grade(job.local_path, params=request.grading)
            job.graded_path = str(graded)
        except Exception as e:
            job.error = f"Generation succeeded, but color grading failed: {str(e)}"

    await record_job(db, job)

    if job.cost_usd > Decimal("0.0000"):
        await record_cost_entry(
            db,
            job_id=job.job_id,
            service=f"{job.provider}_{job.model}",
            amount_usd=job.cost_usd,
            details={"prompt": job.prompt, "duration_sec": job.duration_sec},
        )

    return job


@app.post("/api/v1/generate/cinematic", response_model=JobResponse, tags=["Generation"])
async def generate_cinematic(
    request: CinematicGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """RAG-driven cinematic generation endpoint."""
    rag_service = get_rag_service()
    directive: CinematographyDirective = await rag_service.assemble_cinematography(
        shot_description=request.shot_description,
        camera_query=request.camera_query,
        lighting_query=request.lighting_query,
        effects_query=request.effects_query,
    )

    grading_params = request.grading or ColorGradeParams()

    registry = get_provider_registry()
    video_provider = registry.get_video_provider(request.provider)

    gen_params = GenerationParams(
        prompt=directive.full_prompt,
        duration_sec=request.duration_sec,
        aspect_ratio=request.aspect_ratio,
        ref_image_url=request.ref_image_url,
        seed=request.seed,
        model=request.model,
        extra_params={"cinematography": directive.model_dump()},
    )

    job = await video_provider.generate_video(gen_params)

    if job.status == JobStatus.COMPLETED and job.local_path and request.auto_grade:
        try:
            graded = await apply_color_grade(job.local_path, params=grading_params)
            job.graded_path = str(graded)
        except Exception as e:
            job.error = f"Generation succeeded, but color grading failed: {str(e)}"

    await record_job(db, job)

    if job.cost_usd > Decimal("0.0000"):
        await record_cost_entry(
            db,
            job_id=job.job_id,
            service=f"{job.provider}_{job.model}",
            amount_usd=job.cost_usd,
            details={"prompt": job.prompt, "directive": directive.assembled_fragment},
        )

    return job


@app.post("/api/v1/audio/speech", response_model=JobResponse, tags=["Audio"])
async def generate_speech(
    request: SpeechGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate voiceover audio from script text via ElevenLabs."""
    registry = get_provider_registry()
    audio_provider = registry.get_audio_provider("elevenlabs")

    job = await audio_provider.generate_speech(
        text=request.text,
        voice_id=request.voice_id,
        model_id=request.model_id,
    )

    await record_job(db, job)
    if job.cost_usd > Decimal("0.0000"):
        await record_cost_entry(
            db,
            job_id=job.job_id,
            service="elevenlabs_speech",
            amount_usd=job.cost_usd,
            details={"chars": len(request.text)},
        )

    return job


@app.post("/api/v1/lipsync", response_model=JobResponse, tags=["Audio"])
async def generate_lipsync(
    request: LipSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    """Synchronize video mouth movements to an audio track via Sync Labs."""
    registry = get_provider_registry()
    lipsync_provider = registry.get_lipsync_provider("synclabs")

    job = await lipsync_provider.apply_lipsync(
        video_url_or_path=request.video_url_or_path,
        audio_url_or_path=request.audio_url_or_path,
        model=request.model,
    )

    await record_job(db, job)
    if job.cost_usd > Decimal("0.0000"):
        await record_cost_entry(
            db,
            job_id=job.job_id,
            service="synclabs_lipsync",
            amount_usd=job.cost_usd,
        )

    return job


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse, tags=["Jobs"])
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Fetch status and details of a specific generation job."""
    db_job = await db.get(JobModel, job_id)
    if not db_job:
        raise HTTPException(status_code=404, detail="Job not found.")

    return JobResponse(
        job_id=db_job.id,
        provider=db_job.provider,
        model=db_job.model,
        media_type=MediaType(db_job.media_type),
        status=JobStatus(db_job.status),
        prompt=db_job.prompt,
        duration_sec=float(db_job.duration_sec),
        cost_usd=db_job.cost_usd,
        output_url=db_job.output_url,
        local_path=db_job.local_path,
        graded_path=db_job.graded_path,
        error=db_job.error,
        created_at=db_job.created_at,
        completed_at=db_job.completed_at,
    )


@app.get("/api/v1/jobs", response_model=List[JobResponse], tags=["Jobs"])
async def list_jobs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """List recent generation jobs ordered by creation timestamp."""
    stmt = select(JobModel).order_by(desc(JobModel.created_at)).limit(limit)
    result = await db.execute(stmt)
    records = result.scalars().all()

    return [
        JobResponse(
            job_id=r.id,
            provider=r.provider,
            model=r.model,
            media_type=MediaType(r.media_type),
            status=JobStatus(r.status),
            prompt=r.prompt,
            duration_sec=float(r.duration_sec),
            cost_usd=r.cost_usd,
            output_url=r.output_url,
            local_path=r.local_path,
            graded_path=r.graded_path,
            error=r.error,
            created_at=r.created_at,
            completed_at=r.completed_at,
        )
        for r in records
    ]


@app.get("/api/v1/costs", response_model=CostSummary, tags=["Governance"])
async def get_cost_summary(db: AsyncSession = Depends(get_db)):
    """Get aggregated cost report across all services and generation runs."""
    stmt = select(CostModel)
    result = await db.execute(stmt)
    costs = result.scalars().all()

    total_usd = Decimal("0.0000")
    by_service: Dict[str, Decimal] = {}
    by_model: Dict[str, Decimal] = {}

    for c in costs:
        amt = Decimal(str(c.amount_usd))
        total_usd += amt
        by_service[c.service] = by_service.get(c.service, Decimal("0.0000")) + amt

    return CostSummary(
        total_cost_usd=total_usd,
        total_transactions=len(costs),
        by_service=by_service,
        by_model=by_model,
    )


@app.websocket("/ws/projects/{project_id}")
async def websocket_project_feed(websocket: WebSocket, project_id: str):
    """Real-time WebSocket streaming feed for project state and rendering progress."""
    await ws_manager.connect(project_id, websocket)
    try:
        while True:
            # Keep-alive ping/pong
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(project_id, websocket)
