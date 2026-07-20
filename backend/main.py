import json
import shutil
import logging
import uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from idea.api import RunTaskManager, create_idea_router
from idea.config import load_config
from idea.health import HealthService
from idea.runtime import build_runtime
from opencode_client import run_task, kill_current, kill_task

BASE = Path(__file__).resolve().parent.parent
FRONTEND = BASE / "frontend"
WORKSPACE = BASE / "workspace"
UPLOADS = WORKSPACE / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_DIR / "ai4p.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("ai4p")
# Suppress noisy uvicorn access logs (200 OK on every /api/files poll etc.)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = FastAPI(title="AI4P 专利工作台")

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


@app.on_event("startup")
async def resume_idea_runs():
    if APP_CONFIG.workflow.resume_incomplete_runs_on_startup:
        interrupted = IDEA_TASKS.resume_incomplete()
        if interrupted:
            logger.info("标记需要重新输入临时 API Token 的 IDEA Runs: %s", interrupted)


def sse(d):
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


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


# ===== 任务执行 =====
class Task(BaseModel):
    text: str
    model: str = "agent-plan/glm-5.2"
    files: list[str] = []
    session_id: str | None = None


@app.post("/api/run")
async def run(task: Task):
    task_id = str(uuid.uuid4())
    async def gen():
        yield sse({"type": "task_id", "task_id": task_id})
        try:
            tag = f"续接session={task.session_id[:16]}" if task.session_id else f"附带文件={task.files}"
            logger.info(f"任务开始: model={task.model}, {tag}")
            yield sse({"type": "status", "text": "已提交，agent 工作中..." if not task.session_id else "追问中..."})
            async for evt in run_task(task.text, task.model, task.files, task.session_id, task_id):
                et = evt.get("type")
                if et == "output":
                    logger.info(f"  [output] {evt['text'][:2000]}")
                elif et == "log":
                    raw_t = evt.get("event_type", "?")
                    part = evt.get("part", {})
                    logger.info(f"  [{raw_t}] {json.dumps(part, ensure_ascii=False)[:1000]}")
                elif et == "step":
                    logger.info("  [step]")
                yield sse(evt)
            logger.info("任务完成")
        except Exception as e:
            logger.exception(f"任务异常: {e}")
            yield sse({"type": "error", "error": f"{type(e).__name__}: {e}"})
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/stop")
async def stop(task_id: str | None = None):
    """Stop a specific task (by task_id) or all running tasks."""
    if task_id:
        kill_task(task_id)
    else:
        kill_current()
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND / "index.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
