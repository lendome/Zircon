"""
Flask frontend server for Zircon agent.
Provides web UI to interact with the agent via SSE streaming.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import Any

from flask import Flask, jsonify, render_template, request, Response, stream_with_context

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PARENT_DIR = _PROJECT_ROOT.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

from zirconAgent.core.agent import Agent
from zirconAgent.core.types import TaskStatus, Tier, Plan, PlanStep, SubAgentProgress

app = Flask(__name__,
    template_folder=str(_PROJECT_ROOT / "frontend" / "templates"),
    static_folder=str(_PROJECT_ROOT / "frontend" / "static"),
)

_agent: Agent | None = None
_agent_lock = threading.Lock()
_event_queue: Queue[dict] = Queue()
_stream_active = threading.Event()
_thread_loop: asyncio.AbstractEventLoop | None = None

# Edit snapshots for undo support
_edit_snapshots: list[Any] = []
_cancel_requested = threading.Event()

# Sub-agent progress tracking
_subagent_progress: dict[str, list[SubAgentProgress]] = {}
_subagent_progress_lock = threading.Lock()


class _EditSnapshot:
    def __init__(self, undo_id: int, repo_path: str, history_len: int, modified_files: list[str]):
        self.undo_id = undo_id
        self.repo_path = repo_path
        self.history_len = history_len
        self.modified_files = list(modified_files)
        self.git_ref: str | None = None

    def save_git_ref(self, ref: str):
        self.git_ref = ref


_undo_counter = 0


def _next_undo_id() -> int:
    global _undo_counter
    _undo_counter += 1
    return _undo_counter


def _get_git_manager(repo_path: str):
    from zirconAgent.vcs.git import GitManager
    return GitManager(repo_path)


def _get_event_loop() -> asyncio.AbstractEventLoop:
    global _thread_loop
    if _thread_loop is None or _thread_loop.is_closed():
        _thread_loop = asyncio.new_event_loop()
        t = threading.Thread(target=_thread_loop.run_forever, daemon=True)
        t.start()
    return _thread_loop


def _init_agent(repo_path: str, tier: str, swarm: bool, dump_context: bool = False, plan_mode: bool = False):
    global _agent
    from zirconAgent.core.constants import ensure_zircon_dir
    from zirconAgent.core.logging_config import setup_logging

    ensure_zircon_dir(repo_path)
    setup_logging(repo_path, console=False)

    config_path = str(_PROJECT_ROOT / "models.yaml")
    _agent = Agent(
        repo_path=repo_path,
        config_path=config_path,
        tier=Tier(tier),
        swarm_mode=swarm,
        dump_context=dump_context,
        plan_mode=plan_mode,
    )
    return _agent


def _clear_agent():
    global _agent
    _agent = None


def _load_recent_folders() -> list[dict]:
    """Load recent folders from persistent JSON file."""
    try:
        if _RECENT_FOLDERS_PATH.exists():
            data = json.loads(_RECENT_FOLDERS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_recent_folders(folders: list[dict]) -> None:
    """Save recent folders to persistent JSON file."""
    try:
        _RECENT_FOLDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_FOLDERS_PATH.write_text(
            json.dumps(folders, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def _track_recent_folder(path: str) -> None:
    """Add a folder to the recent folders list (deduplicated, max 10)."""
    resolved = str(Path(path).resolve())
    folders = _load_recent_folders()
    # Remove existing entry with same path
    folders = [f for f in folders if f.get("path") != resolved]
    # Insert at front
    folders.insert(0, {"path": resolved, "name": Path(resolved).name})
    # Keep max 10
    folders = folders[:10]
    _save_recent_folders(folders)


def _get_session_manager(repo_path: str | None = None):
    """Get a SessionManager for the given repo path, or for the current agent."""
    if repo_path is None and _agent is not None:
        repo_path = str(_agent.repo_path)
    if repo_path is None:
        return None
    from zirconAgent.core.session import SessionManager
    return SessionManager(repo_path)


# Persistent path for recent folders
_RECENT_FOLDERS_PATH = _PROJECT_ROOT / ".zircon-code" / "recent_folders.json"


# ===== SubAgent Progress Callback =====

def _subagent_progress_callback(progress: SubAgentProgress) -> None:
    """Callback from swarm orchestrator / subagents to track progress."""
    if not _stream_active.is_set():
        return
    with _subagent_progress_lock:
        if progress.agent_id not in _subagent_progress:
            _subagent_progress[progress.agent_id] = []
        _subagent_progress[progress.agent_id].append(progress)
        # Keep only last 100 entries per agent
        if len(_subagent_progress[progress.agent_id]) > 100:
            _subagent_progress[progress.agent_id] = _subagent_progress[progress.agent_id][-100:]
    try:
        _event_queue.put({
            "type": "subagent_progress",
            "agent_id": progress.agent_id,
            "agent_type": progress.agent_type,
            "status": progress.status if not hasattr(progress.status, 'value') else progress.status.value,
            "phase": progress.phase,
            "detail": progress.detail,
            "step": progress.step,
            "total_steps": progress.total_steps,
            "turn": progress.turn,
            "progress_label": progress.progress_label,
            "files_modified": progress.files_modified,
            "files_read": progress.files_read,
        }, block=False)
    except Exception:
        pass


def _get_subagent_progress(agent_id: str | None = None) -> list[dict]:
    """Get subagent progress history."""
    with _subagent_progress_lock:
        if agent_id:
            entries = _subagent_progress.get(agent_id, [])
        else:
            entries = []
            for entries_list in _subagent_progress.values():
                entries.extend(entries_list)
            # Sort by recent first
            entries = sorted(entries, key=lambda p: p.turn, reverse=True)[:200]
        return [
            {
                "agent_id": e.agent_id,
                "agent_type": e.agent_type,
                "status": e.status.value if hasattr(e.status, 'value') else str(e.status),
                "phase": e.phase,
                "detail": e.detail,
                "step": e.step,
                "total_steps": e.total_steps,
                "turn": e.turn,
                "progress_label": e.progress_label,
                "files_modified": e.files_modified,
                "files_read": e.files_read,
            }
            for e in entries
        ]


# ===== Routes =====

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    with _agent_lock:
        if _agent is None:
            return jsonify({"status": "idle", "connected": False})
        try:
            agent = _agent
            ctx = agent.context
            return jsonify({
                "status": agent.status.value,
                "connected": True,
                "repo": str(agent.repo_path),
                "tier": agent.tier.value,
                "swarm": getattr(agent, '_swarm_mode', False),
                "working_set": len(ctx.working_set),
                "modified_files": list(ctx.modified_files)[:20],
                "session_notes": len(ctx.session_notes),
                "history_len": len(ctx.history),
            })
        except Exception as e:
            return jsonify({"status": "error", "connected": False, "error": str(e)})


@app.route("/api/init", methods=["POST"])
def api_init():
    data = request.get_json() or {}
    repo_path = data.get("repo_path", ".")
    tier_val = data.get("tier", "balanced")
    swarm_val = data.get("swarm", False)
    plan_mode_val = data.get("plan_mode", False)

    resolved = str(Path(repo_path).resolve())
    if not Path(resolved).is_dir():
        return jsonify({"ok": False, "error": f"Directory not found: {resolved}"})

    with _agent_lock:
        _init_agent(resolved, tier_val, swarm_val, plan_mode=plan_mode_val)
        # Wire up the progress callback to the swarm orchestrator
        if _agent is not None:
            _agent._progress_callback = _subagent_progress_callback

    # Track the folder as recently used
    _track_recent_folder(resolved)

    return jsonify({"ok": True, "repo": resolved, "tier": tier_val, "swarm": swarm_val})


@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    send_mode = data.get("mode", "chat")

    with _agent_lock:
        if _agent is None:
            return jsonify({"ok": False, "error": "No agent initialized. Open a folder first."})
        agent = _agent

    if not message:
        return jsonify({"ok": False, "error": "Empty message."})

    _cancel_requested.clear()

    def generate():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        queue: Queue[str | None] = Queue()
        _producer_done = threading.Event()

        async def _producer():
            try:
                if send_mode == "task":
                    iterable = agent.solve_stream(message)
                else:
                    iterable = agent.chat_stream(message)

                async for event_or_chunk in iterable:
                    if _cancel_requested.is_set():
                        queue.put(f"data: {json.dumps({'type': 'error', 'text': 'Action cancelled by user.'})}\n\n")
                        queue.put(f"data: {json.dumps({'type': 'done', 'status': 'cancelled'})}\n\n")
                        return

                    # Check if this chunk indicates awaiting_input (plan approval needed)
                    # and handle it by blocking the stream until feedback arrives.
                    awaiting = False
                    if send_mode == "task":
                        if hasattr(event_or_chunk, 'phase') and event_or_chunk.phase == "awaiting_input":
                            awaiting = True
                    else:
                        # chat mode: StreamChunk with status AWAITING_INPUT
                        if hasattr(event_or_chunk, 'status') and event_or_chunk.status and event_or_chunk.status.value == "awaiting_input":
                            awaiting = True

                    if awaiting:
                        queue.put(f"data: {json.dumps(chunk_to_dict(event_or_chunk) if send_mode != 'task' else {'type': 'trace', **event_to_dict(event_or_chunk)})}\n\n")
                        _stream_active.set()
                        while _stream_active.is_set():
                            if _cancel_requested.is_set():
                                queue.put(f"data: {json.dumps({'type': 'error', 'text': 'Action cancelled by user.'})}\n\n")
                                queue.put(f"data: {json.dumps({'type': 'done', 'status': 'cancelled'})}\n\n")
                                return
                            try:
                                ev = _event_queue.get(timeout=0.5)
                                queue.put(f"data: {json.dumps(ev)}\n\n")
                                if ev.get("_done"):
                                    _stream_active.clear()
                                    return
                            except Empty:
                                queue.put(f"data: {json.dumps({'type': 'keepalive'})}\n\n")

                        # Feedback received — if approved, the agent's status was updated
                        # via submit_feedback. Continue streaming the SAME agent iteration
                        # to pick up plan execution. But chat_stream already returned,
                        # so we need to re-invoke it with the pending plan.
                        if send_mode != "task":
                            # Re-invoke chat_stream with the same message to pick up plan execution
                            try:
                                iterable2 = agent.chat_stream(message)
                                async for chunk2 in iterable2:
                                    d2 = chunk_to_dict(chunk2)
                                    if d2:
                                        queue.put(f"data: {json.dumps(d2)}\n\n")
                            except Exception as e2:
                                queue.put(f"data: {json.dumps({'type': 'error', 'text': f'Plan execution error: {e2}'})}\n\n")
                            queue.put(f"data: {json.dumps({'type': 'done', 'status': agent.status.value})}\n\n")
                            return
                        # For task mode, the agent's solve_stream is still running;
                        # it will continue yielding events after the while-loop.
                        continue

                    if send_mode == "task":
                        queue.put(f"data: {json.dumps({'type': 'trace', **event_to_dict(event_or_chunk)})}\n\n")
                    else:
                        chunk = event_or_chunk
                        data_dict = chunk_to_dict(chunk)
                        if data_dict:
                            if data_dict.get("tool_calls"):
                                for tc in data_dict["tool_calls"]:
                                    if tc.get("name") in ("edit_file", "edit_lines", "create_file", "delete_file"):
                                        await _record_edit_snapshot(agent)
                            if data_dict.get("tool_result"):
                                tr = data_dict["tool_result"]
                                if "changed" in tr or "written" in tr or "created" in tr or "deleted" in tr:
                                    await _record_edit_snapshot(agent)
                            queue.put(f"data: {json.dumps(data_dict)}\n\n")

                if send_mode == "task":
                    queue.put(f"data: {json.dumps({'type': 'done', 'status': agent.status.value})}\n\n")
                else:
                    queue.put(f"data: {json.dumps({'type': 'done', 'status': agent.status.value})}\n\n")
            except asyncio.CancelledError:
                queue.put(f"data: {json.dumps({'type': 'error', 'text': 'Action cancelled.'})}\n\n")
                queue.put(f"data: {json.dumps({'type': 'done', 'status': 'cancelled'})}\n\n")
            except Exception as e:
                queue.put(f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n")
            finally:
                _producer_done.set()
                queue.put(None)

        def _worker():
            try:
                loop.run_until_complete(_producer())
            except Exception as e:
                try:
                    queue.put(f"data: {json.dumps({'type': 'error', 'text': f'Worker error: {e}'})}\n\n")
                    queue.put(None)
                except Exception:
                    pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        import queue as qmod
        try:
            while True:
                try:
                    item = queue.get(timeout=30)
                except qmod.Empty:
                    if _producer_done.is_set():
                        break
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
                    continue
                if item is None:
                    break
                yield item
        except GeneratorExit:
            _cancel_requested.set()
            raise
        finally:
            # Event.is_set() does not support timeout — use a short polling loop
            for _ in range(50):  # ~5 seconds
                if _producer_done.is_set():
                    break
                import time
                time.sleep(0.1)
            if not _producer_done.is_set():
                _cancel_requested.set()
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _record_edit_snapshot(agent) -> None:
    """Create a snapshot of current state before an edit for undo support.
    Uses async subprocess to avoid blocking the event loop."""
    try:
        undo_id = _next_undo_id()
        ctx = agent.context

        # Get current git HEAD as the reference point (non-blocking)
        git_ref = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                cwd=str(agent.repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            if proc.returncode == 0:
                git_ref = stdout.decode("utf-8", errors="replace").strip()
        except Exception:
            pass

        snapshot = _EditSnapshot(
            undo_id=undo_id,
            repo_path=str(agent.repo_path),
            history_len=len(ctx.history),
            modified_files=list(ctx.modified_files),
        )
        if git_ref:
            snapshot.save_git_ref(git_ref)
        _edit_snapshots.append(snapshot)

        # Keep only the last 20 snapshots to prevent memory bloat
        while len(_edit_snapshots) > 20:
            _edit_snapshots.pop(0)

        # Emit an edit_snapshot event to the frontend
        _event_queue.put({
            "type": "edit_snapshot",
            "undo_id": undo_id,
            "modified_files": list(ctx.modified_files),
        }, block=False)
    except Exception:
        pass


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    data = request.get_json() or {}
    feedback = data.get("feedback", "approved")

    with _agent_lock:
        if _agent is None:
            return jsonify({"ok": False, "error": "No agent."})
        if _agent.status != TaskStatus.AWAITING_INPUT and _agent.status != TaskStatus.IDLE:
            return jsonify({"ok": False, "error": f"Agent is not awaiting input (status={_agent.status})."})
        _agent.submit_feedback(feedback)
        _stream_active.clear()

    return jsonify({"ok": True})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    _cancel_requested.set()

    with _agent_lock:
        if _agent is not None:
            from zirconAgent.core.types import TaskStatus
            _agent._status = TaskStatus.IDLE
            _agent._pending_plan = None
            _agent._recovery_exhausted = False
            _agent._recovery_attempts = 999

    _stream_active.clear()

    try:
        _event_queue.put({"_done": True, "_cancelled": True}, block=False)
    except Exception:
        pass

    return jsonify({"ok": True, "message": "Cancellation requested."})


@app.route("/api/undo", methods=["POST"])
def api_undo():
    data = request.get_json() or {}
    undo_id = data.get("undo_id")

    snapshot = None
    for s in _edit_snapshots:
        if s.undo_id == undo_id:
            snapshot = s
            break

    if snapshot is None:
        return jsonify({"ok": False, "error": f"No snapshot found for undo_id={undo_id}"})

    with _agent_lock:
        if _agent is None:
            return jsonify({"ok": False, "error": "No agent initialized."})

        agent = _agent
        ctx = agent.context

        # 1) Revert chat history to before the edit
        if len(ctx.history) > snapshot.history_len:
            ctx.history = ctx.history[:snapshot.history_len]

        # 2) Revert git state
        reverted_files: list[str] = []
        try:
            if snapshot.git_ref:
                try:
                    import subprocess
                    result = subprocess.run(
                        ["git", "checkout", snapshot.git_ref, "--"],
                        capture_output=True,
                        text=True,
                        cwd=str(agent.repo_path),
                    )
                    if result.returncode == 0:
                        reverted_files = list(snapshot.modified_files)
                    else:
                        for f in snapshot.modified_files:
                            subprocess.run(
                                ["git", "checkout", "HEAD", "--", f],
                                capture_output=True,
                                cwd=str(agent.repo_path),
                            )
                            reverted_files.append(f)
                except Exception:
                    pass
            else:
                try:
                    import subprocess
                    for f in snapshot.modified_files:
                        subprocess.run(
                            ["git", "checkout", "HEAD", "--", f],
                            capture_output=True,
                            cwd=str(agent.repo_path),
                        )
                        reverted_files.append(f)
                except Exception:
                    pass

            if not reverted_files:
                try:
                    gm = _get_git_manager(str(agent.repo_path))
                    gm.rollback("HEAD~1")
                except Exception:
                    pass
        except Exception as e:
            return jsonify({"ok": False, "error": f"Git revert failed: {e}"})

        # 3) Update modified_files tracking
        for f in reverted_files:
            if f in ctx.modified_files:
                ctx.modified_files.discard(f)

    return jsonify({"ok": True, "reverted_files": reverted_files, "history_restored_to": snapshot.history_len})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    with _agent_lock:
        if _agent:
            _agent.context.clear_history()
    return jsonify({"ok": True})


@app.route("/api/clear_agent", methods=["POST"])
def api_clear_agent():
    _clear_agent()
    return jsonify({"ok": True})


@app.route("/api/pending_plan")
def api_pending_plan():
    with _agent_lock:
        if _agent is None or _agent.pending_plan is None:
            return jsonify({"ok": False})
        plan = _agent.pending_plan
        return jsonify({
            "ok": True,
            "plan": plan_to_dict(plan),
        })


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/pick_folder", methods=["POST"])
def api_pick_folder():
    import sys
    import subprocess

    # macOS: use AppleScript for a native folder picker (avoids Tkinter thread crash)
    if sys.platform == "darwin":
        try:
            script = """
            tell application "System Events"
                set frontApp to name of first application process whose frontmost is true
            end tell
            tell application "Finder"
                set theFolder to choose folder with prompt "Select a workspace folder for Zircon"
                set folderPath to POSIX path of theFolder
                return folderPath
            end tell
            """
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                folder = result.stdout.strip()
                if folder:
                    return jsonify({"ok": True, "path": folder})
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass

    # Fallback: Tkinter for Linux/Windows
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="Select Workspace Folder")
        root.destroy()
        if folder:
            return jsonify({"ok": True, "path": folder})
    except Exception:
        pass

    return jsonify({"ok": False, "error": "No folder selected."})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    config_path = _PROJECT_ROOT / "models.yaml"
    if not config_path.exists():
        return jsonify({"ok": False, "error": "models.yaml not found"})
    import yaml
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return jsonify({"ok": True, "config": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/settings", methods=["PUT"])
def api_save_settings():
    config_path = _PROJECT_ROOT / "models.yaml"
    new_config = request.get_json() or {}
    from zirconAgent.core.config import save_config
    try:
        save_config(new_config, config_path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/settings/models", methods=["GET"])
def api_list_models():
    import yaml
    config_path = _PROJECT_ROOT / "models.yaml"
    if not config_path.exists():
        return jsonify({"ok": False, "error": "models.yaml not found", "models": []})
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
    except Exception:
        return jsonify({"ok": False, "models": []})

    models_list: list[str] = []
    profiles = cfg.get("profiles", {})
    for name, profile in profiles.items():
        model = profile.get("model", "")
        base_url = profile.get("base_url", "")
        if model and base_url:
            models_list.append(f"{name}: {model} ({base_url})")
    seen: set[str] = set()
    unique: list[str] = []
    for m in models_list:
        key = m.split(":")[1].strip() if ":" in m else m
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return jsonify({"ok": True, "models": unique})


@app.route("/api/settings/fetch_models", methods=["POST"])
def api_fetch_models():
    data = request.get_json() or {}
    base_url = data.get("base_url", "").rstrip("/")
    api_key = data.get("api_key", "")

    if not base_url:
        return jsonify({"ok": False, "error": "No base URL provided", "models": []})

    import urllib.request
    import urllib.error
    import json as _json

    candidates = [
        f"{base_url}/models",
        f"{base_url}/v1/models",
        f"{base_url}/api/models",
    ]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    models_set: set[str] = set()

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = _json.loads(resp.read())
                for item in body.get("data", []):
                    mid = item.get("id", "")
                    if mid:
                        models_set.add(mid)
                for item in body.get("models", []):
                    mid = item.get("name", item.get("model", item.get("id", "")))
                    if mid:
                        models_set.add(mid)
        except Exception:
            continue
        if models_set:
            break  # First successful endpoint wins

    sorted_models = sorted(models_set)
    return jsonify({"ok": True, "models": sorted_models})


# ===== SubAgent Progress Endpoints =====

@app.route("/api/subagent_progress")
def api_subagent_progress():
    """Get current subagent progress entries."""
    agent_id = request.args.get("agent_id")
    entries = _get_subagent_progress(agent_id)
    return jsonify({"ok": True, "entries": entries})


@app.route("/api/subagent_progress/clear", methods=["POST"])
def api_clear_subagent_progress():
    """Clear all subagent progress entries."""
    with _subagent_progress_lock:
        _subagent_progress.clear()
    return jsonify({"ok": True})


_webview_window: Any | None = None
_webview_maximized = False
_window_drag_pos: tuple[int, int] | None = None

@app.route("/api/window/move", methods=["POST"])
def api_window_move():
    """Move the window by a relative delta via pywebview."""
    global _webview_window, _window_drag_pos
    data = request.get_json() or {}
    dx = data.get("dx", 0)
    dy = data.get("dy", 0)
    if _webview_window is not None:
        try:
            if _window_drag_pos is None:
                _window_drag_pos = (_webview_window.x, _webview_window.y)
            cur_x, cur_y = _window_drag_pos
            new_x = cur_x + dx
            new_y = cur_y + dy
            _webview_window.move(new_x, new_y)
            _window_drag_pos = (new_x, new_y)
            return jsonify({"ok": True})
        except Exception:
            pass
    return jsonify({"ok": False})

@app.route("/api/window/close", methods=["POST"])
def api_window_close():
    """Close the application window."""
    global _webview_window
    if _webview_window is not None:
        try:
            _webview_window.destroy()
            return jsonify({"ok": True})
        except Exception:
            pass
    import os
    os._exit(0)

@app.route("/api/window/minimize", methods=["POST"])
def api_window_minimize():
    """Minimize the window."""
    global _webview_window
    if _webview_window is not None:
        try:
            _webview_window.minimize()
            return jsonify({"ok": True})
        except Exception:
            pass
    return jsonify({"ok": True})

@app.route("/api/window/maximize", methods=["POST"])
def api_window_maximize():
    """Toggle maximize/restore the window."""
    global _webview_window, _webview_maximized
    if _webview_window is not None:
        try:
            if _webview_maximized:
                _webview_window.restore()
            else:
                _webview_window.maximize()
            _webview_maximized = not _webview_maximized
            return jsonify({"ok": True})
        except Exception:
            pass
    return jsonify({"ok": True})


# ===== Session Endpoints =====

@app.route("/api/sessions")
def api_list_sessions():
    """List all saved sessions."""
    repo_path = request.args.get("repo", "")
    if not repo_path and _agent is not None:
        repo_path = str(_agent.repo_path)
    if not repo_path:
        return jsonify({"ok": False, "error": "No repo path available", "sessions": []})

    sm = _get_session_manager(repo_path)
    if sm is None:
        return jsonify({"ok": False, "error": "Could not create SessionManager", "sessions": []})

    try:
        sessions = sm.list_sessions()
        # Sort by started_at descending
        sessions.sort(key=lambda s: s.get("started_at", ""), reverse=True)
        return jsonify({"ok": True, "sessions": sessions})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "sessions": []})


@app.route("/api/sessions/<session_id>")
def api_get_session(session_id: str):
    """Get session details including journal entries."""
    repo_path = request.args.get("repo", "")
    if not repo_path and _agent is not None:
        repo_path = str(_agent.repo_path)
    if not repo_path:
        return jsonify({"ok": False, "error": "No repo path available"})

    sm = _get_session_manager(repo_path)
    if sm is None:
        return jsonify({"ok": False, "error": "Could not create SessionManager"})

    try:
        session_dir = sm.session_dir / session_id
        manifest_path = session_dir / "manifest.json"
        journal_path = session_dir / "journal.jsonl"

        if not manifest_path.exists():
            return jsonify({"ok": False, "error": f"Session {session_id} not found"})

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        journal_entries = []
        if journal_path.exists():
            with open(journal_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            journal_entries.append(json.loads(line))
                        except Exception:
                            pass

        return jsonify({
            "ok": True,
            "manifest": manifest,
            "journal": journal_entries,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sessions/<session_id>/messages")
def api_get_session_messages(session_id: str):
    """Get the full chat messages for a session."""
    repo_path = request.args.get("repo", "")
    if not repo_path and _agent is not None:
        repo_path = str(_agent.repo_path)
    if not repo_path:
        return jsonify({"ok": False, "error": "No repo path available", "messages": []})

    sm = _get_session_manager(repo_path)
    if sm is None:
        return jsonify({"ok": False, "error": "Could not create SessionManager", "messages": []})

    try:
        messages = sm.load_messages(session_id)
        return jsonify({"ok": True, "messages": messages})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "messages": []})


# ===== Recent Folders Endpoints =====

@app.route("/api/diffs")
def api_diffs():
    """Get git diffs from the current working tree."""
    with _agent_lock:
        if _agent is None:
            return jsonify({"ok": False, "diffs": [], "error": "No agent initialized."})
        repo = str(_agent.repo_path)
    try:
        import subprocess
        result = subprocess.run(
            ["git", "diff", "--unified=8"],
            capture_output=True, text=True, cwd=repo, timeout=30,
        )
        diff_text = result.stdout
        files_result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, cwd=repo, timeout=10,
        )
        staged_result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, cwd=repo, timeout=10,
        )
        changed_files = [f for f in files_result.stdout.splitlines() if f]
        staged_files = [f for f in staged_result.stdout.splitlines() if f]
        all_files = list(dict.fromkeys(changed_files + staged_files))

        # Parse diffs per file
        diffs = []
        current_file = None
        current_lines = []
        for line in diff_text.splitlines(True):
            if line.startswith("diff --git"):
                if current_file and current_lines:
                    diffs.append({"file": current_file, "content": "".join(current_lines)})
                current_file = line.split()[-1].lstrip("b/")
                current_lines = [line]
            elif current_file:
                current_lines.append(line)
        if current_file and current_lines:
            diffs.append({"file": current_file, "content": "".join(current_lines)})

        return jsonify({"ok": True, "diffs": diffs, "files": all_files})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "diffs": [], "error": "Diff timed out."})
    except FileNotFoundError:
        return jsonify({"ok": False, "diffs": [], "error": "Git not found."})
    except Exception as e:
        return jsonify({"ok": False, "diffs": [], "error": str(e)})


@app.route("/api/recent_folders", methods=["GET"])

@app.route("/api/recent_folders", methods=["GET"])
def api_get_recent_folders():
    """Get list of recently opened folders."""
    folders = _load_recent_folders()
    return jsonify({"ok": True, "folders": folders})


@app.route("/api/recent_folders", methods=["POST"])
def api_add_recent_folder():
    """Add a folder to the recent folders list."""
    data = request.get_json() or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "No path provided"})
    _track_recent_folder(path)
    return jsonify({"ok": True})


@app.route("/api/recent_folders/clear", methods=["POST"])
def api_clear_recent_folders():
    """Clear all recent folders."""
    _save_recent_folders([])
    return jsonify({"ok": True})


def event_to_dict(event) -> dict:
    return {
        "type": "trace",
        "phase": event.phase,
        "detail": event.detail,
        "payload": event.payload,
    }


def chunk_to_dict(chunk) -> dict | None:
    d: dict[str, Any] = {"type": "chunk"}
    if chunk.text:
        d["text"] = chunk.text
    if chunk.reasoning:
        d["reasoning"] = chunk.reasoning
    if chunk.tool_calls:
        d["tool_calls"] = [{"name": tc.name, "arguments": tc.arguments} for tc in chunk.tool_calls]
    if chunk.tool_result:
        d["tool_result"] = chunk.tool_result
    if chunk.error:
        d["error"] = chunk.error
    if chunk.done:
        d["done"] = True
    if chunk.status:
        d["status"] = chunk.status.value
    if chunk.progress_label:
        d["progress_label"] = chunk.progress_label
    if chunk.usage:
        d["usage"] = chunk.usage
    # Always emit a chunk if it has a progress label, even if empty, to allow clearing
    if chunk.progress_label is not None and chunk.progress_label == "":
        d["progress_label"] = ""
        return d
    if any(d.get(k) for k in ("text", "tool_calls", "tool_result", "error", "done", "progress_label", "reasoning")):
        return d
    return None


def plan_to_dict(plan: Plan) -> dict:
    return {
        "complexity": plan.complexity,
        "steps": [
            {
                "index": s.index,
                "action": s.action,
                "description": s.description,
                "target_files": s.target_files,
            }
            for s in plan.steps
        ],
    }


def start_server(port: int = 5555, debug: bool = False):
    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=debug, use_reloader=False),
        daemon=True,
    )
    t.start()
    return port