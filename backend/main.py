import shutil
import logging
from pathlib import Path
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from idea.api import RunTaskManager, create_idea_router
from idea.followup_api import create_followup_router
from idea.config import load_config
from idea.health import HealthService
from idea.logging_security import redact_query_credentials
from idea.runtime import build_runtime
from landscape.api import create_landscape_router
from landscape.runtime import build_landscape_runtime

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"
WORKSPACE = BASE / "workspace"
UPLOADS = WORKSPACE / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
redact_query_credentials(LOG_DIR / "aifpatent.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_DIR / "aifpatent.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("aifpatent")
# Suppress noisy uvicorn access logs (200 OK on every /api/files poll etc.)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
# httpx logs the complete URL at INFO; SerpAPI authenticates with a query parameter.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(title="AIFPatent 专利工作台")

APP_CONFIG = load_config()
IDEA_RUNTIME = build_runtime(APP_CONFIG)
IDEA_DB = IDEA_RUNTIME.database
IDEA_CACHE = IDEA_RUNTIME.cache
IDEA_RUN_STORE = IDEA_RUNTIME.run_store
IDEA_WORKFLOW = IDEA_RUNTIME.harness
IDEA_TASKS = RunTaskManager(
    IDEA_DB,
    IDEA_WORKFLOW,
    IDEA_RUNTIME.executor,
    debug_log=IDEA_RUNTIME.debug_log,
)
LANDSCAPE_RUNTIME = build_landscape_runtime(APP_CONFIG, cache=IDEA_CACHE)
HEALTH_SERVICE = HealthService(
    APP_CONFIG,
    IDEA_DB,
    IDEA_CACHE,
    workflow_recovery_ready=lambda: IDEA_WORKFLOW.recovery_ready,
)
app.include_router(
    create_idea_router(
        APP_CONFIG,
        IDEA_DB,
        IDEA_RUN_STORE,
        IDEA_WORKFLOW,
        IDEA_TASKS,
        debug_log=IDEA_RUNTIME.debug_log,
    )
)
if IDEA_RUNTIME.followup_manager is not None:
    app.include_router(
        create_followup_router(
            IDEA_RUNTIME.followup_manager.repository,
            IDEA_RUNTIME.followup_manager,
        )
    )
app.include_router(create_landscape_router(LANDSCAPE_RUNTIME))


@app.on_event("startup")
async def resume_idea_runs():
    if APP_CONFIG.workflow.resume_incomplete_runs_on_startup:
        interrupted = IDEA_TASKS.resume_incomplete()
        if interrupted:
            logger.info("标记需要重新输入临时 API Token 的 IDEA Runs: %s", interrupted)
    if IDEA_RUNTIME.followup_manager is not None:
        interrupted_turns = await IDEA_RUNTIME.followup_manager.resume_incomplete()
        if interrupted_turns:
            logger.info(
                "标记需要重新输入临时 API Token 的追问 Turns: %s",
                interrupted_turns,
            )
    interrupted_landscape = LANDSCAPE_RUNTIME.tasks.resume_incomplete()
    if interrupted_landscape:
        logger.info("标记需要重新输入临时 API Token 的专利态势分析 Runs: %s", interrupted_landscape)
    if LANDSCAPE_RUNTIME.v4_tasks is not None:
        resumed_v4 = LANDSCAPE_RUNTIME.v4_tasks.resume_incomplete()
        if resumed_v4:
            logger.info("恢复专利态势 v4 Runs: %s", resumed_v4)


@app.on_event("shutdown")
async def close_idea_runtime():
    if LANDSCAPE_RUNTIME.v4_tasks is not None:
        await LANDSCAPE_RUNTIME.v4_tasks.aclose()
    await LANDSCAPE_RUNTIME.tasks.aclose()
    if IDEA_RUNTIME.followup_manager is not None:
        await IDEA_RUNTIME.followup_manager.aclose()
    await IDEA_RUNTIME.executor.aclose()


def _safe_name(name: str) -> str:
    """Strip directory components to prevent path traversal."""
    return Path(name).name


@app.get("/api/health")
async def health():
    result = await HEALTH_SERVICE.check()
    return {"ok": result["ok"], "status": result["status"], "engine": "idea-workflow/2.0.0"}


@app.get("/api/system/health")
async def system_health():
    return await HEALTH_SERVICE.check()


@app.get("/api/system/config")
async def system_config():
    return APP_CONFIG.snapshot()


@app.get("/api/system/cache")
async def system_cache():
    return {
        **IDEA_CACHE.stats(),
        "max_bytes": IDEA_CACHE.max_bytes,
        "low_watermark_bytes": IDEA_CACHE.low_watermark_bytes,
        "eviction_policy": "fifo",
    }


@app.post("/api/system/cache/cleanup")
async def system_cache_cleanup():
    return IDEA_CACHE.cleanup(force=True).__dict__


# ===== 文件管理 =====
@app.get("/api/files")
async def list_files():
    uploads = []
    for f in sorted(UPLOADS.iterdir()):
        if f.is_file():
            uploads.append({"name": f.name, "size": f.stat().st_size, "type": "upload"})
    generated = []
    for f in sorted(WORKSPACE.iterdir()):
        if f.is_file():
            generated.append({"name": f.name, "size": f.stat().st_size, "type": "generated"})
    return {"uploads": uploads, "generated": generated}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    saved = []
    for f in files:
        dest = UPLOADS / Path(f.filename).name
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(dest.name)
    logger.info(f"上传文件: {saved}")
    return {"uploaded": saved}


@app.get("/api/download/{name}")
async def download(name: str):
    name = _safe_name(name)
    for d in [UPLOADS, WORKSPACE]:
        f = d / name
        if f.is_file():
            return FileResponse(str(f), filename=name)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/files/{name}")
async def delete_file(name: str):
    name = _safe_name(name)
    for d in [UPLOADS, WORKSPACE]:
        f = d / name
        if f.is_file():
            f.unlink()
            logger.info(f"删除文件: {name}")
            return {"deleted": name}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND / "index.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/landscape")
async def landscape_index():
    return FileResponse(
        str(FRONTEND / "landscape.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
