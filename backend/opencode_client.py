import asyncio
import os
import re
import uuid
import logging
import json as _json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OPENCODE_BIN = str(BASE / "bin" / "opencode" / "opencode")
WORKSPACE = BASE / "workspace"
UPLOADS = WORKSPACE / "uploads"
DEFAULT_MODEL = "agent-plan/glm-5.2"
_ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
_IDLE_TIMEOUT = 300  # seconds without output before killing subprocess
_procs = {}  # task_id -> proc, supports parallel execution
logger = logging.getLogger("ai4p")


def _log_event(t, part):
    """Log every opencode event type (tool calls, reasoning, steps, etc.)."""
    if t == "text":
        txt = (part.get("text") or "").strip()
        if txt:
            logger.info(f"  | {txt[:2000]}")
    elif t == "tool_start":
        tool = part.get("tool", "?")
        inp = part.get("input", {})
        logger.info(f"  [tool_start] {tool}: {_json.dumps(inp, ensure_ascii=False)[:1000]}")
    elif t == "tool_finish":
        tool = part.get("tool", "?")
        out = part.get("output", part.get("result", ""))
        out_s = out if isinstance(out, str) else _json.dumps(out, ensure_ascii=False)
        logger.info(f"  [tool_finish] {tool}: {out_s[:1000]}")
    elif t == "reasoning":
        txt = (part.get("text") or "").strip()
        if txt:
            logger.info(f"  [reasoning] {txt[:1000]}")
    elif t == "step_start":
        logger.info(f"  [step_start] {_json.dumps(part, ensure_ascii=False)[:500]}")
    elif t == "step_finish":
        logger.info(f"  [step_finish] {_json.dumps(part, ensure_ascii=False)[:500]}")
    else:
        logger.info(f"  [{t}] {_json.dumps(part, ensure_ascii=False)[:500]}")


def kill_task(task_id):
    """Kill a specific running task by task_id."""
    proc = _procs.get(task_id)
    if proc and proc.returncode is None:
        proc.kill()
        return True
    return False


def _env():
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(BASE / "config")
    env["XDG_DATA_HOME"] = str(BASE / "data")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def kill_current():
    """Kill all running tasks. Called by /api/stop when no task_id given."""
    for proc in list(_procs.values()):
        if proc and proc.returncode is None:
            proc.kill()


async def run_task(text, model=DEFAULT_MODEL, files=None, session_id=None, task_id=None):
    """opencode run --format json。session_id 不为空时续接该 session（追问）。
    task_id 用于多模块并行时追踪/停止单个任务。"""
    args = [OPENCODE_BIN, "run", "--format", "json", text]
    if session_id:
        args += ["--session", session_id]
    else:
        for fn in (files or []):
            f = UPLOADS / fn
            if f.is_file():
                args += ["-f", str(f)]
    args += ["--model", model]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_env(),
        cwd=str(WORKSPACE),
    )
    if task_id is None:
        task_id = str(uuid.uuid4())
    _procs[task_id] = proc
    sid = None
    texts = []
    buf = ""
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(8192), timeout=_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                yield {"type": "error", "error": f"任务超时（{_IDLE_TIMEOUT}秒无输出），已终止"}
                break
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = _ANSI.sub("", line.strip())
                if not line.startswith("{"):
                    continue
                try:
                    evt = _json.loads(line)
                except Exception:
                    continue
                if not sid and evt.get("sessionID"):
                    sid = evt["sessionID"]
                t = evt.get("type")
                part = evt.get("part") or {}
                _log_event(t, part)
                if t == "text" and part.get("text"):
                    texts.append(part["text"])
                    yield {"type": "output", "text": part["text"]}
                elif t == "step_start":
                    yield {"type": "step"}
                else:
                    yield {"type": "log", "event_type": t, "part": part}
        # flush remaining buffer after EOF (last line without trailing newline)
        if buf.strip():
            line = _ANSI.sub("", buf.strip())
            if line.startswith("{"):
                try:
                    evt = _json.loads(line)
                    if not sid and evt.get("sessionID"):
                        sid = evt["sessionID"]
                    t = evt.get("type")
                    part = evt.get("part") or {}
                    _log_event(t, part)
                    if t == "text" and part.get("text"):
                        texts.append(part["text"])
                        yield {"type": "output", "text": part["text"]}
                    elif t == "step_start":
                        yield {"type": "step"}
                    else:
                        yield {"type": "log", "event_type": t, "part": part}
                except Exception:
                    pass
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        _procs.pop(task_id, None)
    yield {"type": "done", "result": "\n".join(texts), "session_id": sid}
