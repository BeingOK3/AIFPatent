#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


TERMINAL = {"COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"}


class WorkflowClientError(RuntimeError):
    pass


def request(base_url: str, path: str, *, method: str = "GET", body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(payload).get("detail", payload)
        except json.JSONDecodeError:
            detail = payload
        raise WorkflowClientError(f"HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise WorkflowClientError(f"API unavailable: {type(exc).__name__}") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WorkflowClientError("API response is not JSON") from exc


def idea_text(args) -> str:
    if args.idea is not None:
        value = args.idea
    elif args.idea_file == "-":
        value = sys.stdin.read()
    else:
        value = Path(args.idea_file).read_text(encoding="utf-8")
    value = value.strip()
    if len(value) < 10:
        raise WorkflowClientError("IDEA text must contain at least 10 characters")
    return value


def start(args):
    text = idea_text(args)
    if args.deep_min < 10:
        raise WorkflowClientError("deep-review minimum cannot be below 10")
    if args.deep_max < args.deep_min:
        raise WorkflowClientError("deep-review maximum must be >= minimum")
    if args.candidate_max < args.deep_max:
        raise WorkflowClientError("candidate maximum must be >= deep-review maximum")
    model_base_url = args.model_base_url.strip().rstrip("/")
    model = args.model.strip()
    if not model_base_url.startswith(("https://", "http://")):
        raise WorkflowClientError("model Base URL must use http:// or https://")
    if not model:
        raise WorkflowClientError("model name is required")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise WorkflowClientError(
            f"runtime API Token is required in environment variable {args.api_key_env}"
        )
    if args.case_id:
        case_id = args.case_id
    else:
        case = request(
            args.base_url,
            "/api/idea/cases",
            method="POST",
            body={"title": args.case_title},
        )
        case_id = case["case_id"]
    return request(
        args.base_url,
        f"/api/idea/cases/{case_id}/runs",
        method="POST",
        body={
            "api_key": api_key,
            "base_url": model_base_url,
            "model": model,
            "input_text": text,
            "evaluation_date": args.evaluation_date,
            "date_basis": args.date_basis,
            "analysis_scope": "full",
            "settings": {
                "search_mode": args.mode,
                "candidate_max": args.candidate_max,
                "deep_review_min": args.deep_min,
                "deep_review_max": args.deep_max,
            },
        },
    )


def wait_for_run(args, run_id: str):
    deadline = time.monotonic() + args.timeout
    previous = None
    while True:
        run = request(args.base_url, f"/api/idea/runs/{run_id}")
        progress = run.get("progress", {})
        signature = (
            run.get("status"),
            progress.get("current_step"),
            progress.get("completed_steps"),
        )
        if signature != previous:
            print(
                f"[{signature[0]}] {signature[2]}/{progress.get('total_steps')} {signature[1] or ''}",
                file=sys.stderr,
            )
            previous = signature
        if run.get("status") in TERMINAL:
            return run
        if time.monotonic() >= deadline:
            raise WorkflowClientError(
                f"wait timeout; Run {run_id} continues in background with status {run.get('status')}"
            )
        time.sleep(args.poll)


def add_connection(parser):
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8001", help="AIFPatent application URL"
    )


def add_start_arguments(parser):
    parser.add_argument("--model-base-url", required=True, help="OpenAI-compatible model API URL")
    parser.add_argument("--model", required=True, help="model name supplied by the user")
    parser.add_argument(
        "--api-key-env",
        default="LLM_API_KEY",
        help="environment variable containing the runtime API Token",
    )
    parser.add_argument("--case-id", help="reuse an existing Case")
    parser.add_argument("--case-title", default="IDEA review", help="new Case title")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--idea", help="IDEA text; prefer --idea-file for multiline input")
    source.add_argument("--idea-file", help="UTF-8 path, or - for stdin")
    parser.add_argument("--evaluation-date", default=date.today().isoformat())
    parser.add_argument("--date-basis", default="用户指定或提交日")
    parser.add_argument("--mode", choices=("quick", "standard", "deep"), default="standard")
    parser.add_argument("--candidate-max", type=int, default=80)
    parser.add_argument("--deep-min", type=int, default=10)
    parser.add_argument("--deep-max", type=int, default=20)


def build_parser():
    parser = argparse.ArgumentParser(description="Deterministic client for AIFPatent")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("health", "history"):
        command = sub.add_parser(name)
        add_connection(command)
    start_parser = sub.add_parser("start")
    add_connection(start_parser)
    add_start_arguments(start_parser)
    for name in ("status", "report", "cancel"):
        command = sub.add_parser(name)
        add_connection(command)
        command.add_argument("run_id")
    wait = sub.add_parser("wait")
    add_connection(wait)
    wait.add_argument("run_id")
    wait.add_argument("--poll", type=float, default=2.0)
    wait.add_argument("--timeout", type=float, default=3600)
    run = sub.add_parser("run")
    add_connection(run)
    add_start_arguments(run)
    run.add_argument("--poll", type=float, default=2.0)
    run.add_argument("--timeout", type=float, default=3600)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "health":
            result = request(args.base_url, "/api/system/health")
        elif args.command == "history":
            result = request(args.base_url, "/api/idea/cases")
        elif args.command == "start":
            result = start(args)
        elif args.command == "status":
            result = request(args.base_url, f"/api/idea/runs/{args.run_id}")
        elif args.command == "report":
            result = request(args.base_url, f"/api/idea/runs/{args.run_id}/report")
        elif args.command == "cancel":
            result = request(
                args.base_url,
                f"/api/idea/runs/{args.run_id}/cancel",
                method="POST",
                body={},
            )
        elif args.command == "wait":
            result = wait_for_run(args, args.run_id)
        elif args.command == "run":
            created = start(args)
            terminal = wait_for_run(args, created["run_id"])
            if terminal["status"] not in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS"}:
                result = terminal
            else:
                result = request(args.base_url, f"/api/idea/runs/{created['run_id']}/report")
        else:
            raise WorkflowClientError(f"unsupported command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        if isinstance(result, dict) and result.get("status") in {"FAILED", "CANCELLED"}:
            return 2
        if args.command == "health" and isinstance(result, dict) and result.get("ok") is False:
            return 2
        return 0
    except (WorkflowClientError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
