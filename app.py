import html
import json
import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

CLAUDE_TIMEOUT_SEC = 6000

_PROJECT_DIR = Path(__file__).parent.resolve()


def _git(*args, timeout: int = 10) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_PROJECT_DIR),
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def _current_commit() -> str:
    code, out, _ = _git("rev-parse", "HEAD", timeout=5)
    return out if code == 0 else ""


def _check_updates() -> dict | None:
    """Fetch origin and return {commit, subject} if remote has commits beyond
    the boot commit, else None. Silent no-op if not a git repo / no upstream /
    network unavailable."""
    _git("fetch", "--quiet", "origin", timeout=8)
    code, upstream, _ = _git("rev-parse", "--abbrev-ref", "@{u}", timeout=3)
    if code != 0 or not upstream:
        return None
    code2, remote_head, _ = _git("rev-parse", upstream, timeout=3)
    if code2 != 0 or not remote_head:
        return None
    boot = st.session_state.get("_boot_commit") or ""
    if not boot or remote_head == boot:
        return None
    _, subject, _ = _git("log", "-1", "--pretty=%h · %s", remote_head, timeout=3)
    return {"commit": remote_head, "subject": subject or remote_head[:7]}


def _restart_service() -> bool:
    """Spawn a detached shell that terminates this streamlit process and
    re-execs run.sh (which will git-pull + relaunch)."""
    run_sh = _PROJECT_DIR / "run.sh"
    if not run_sh.exists():
        return False
    pid = os.getpid()
    port = os.environ.get("STREAMLIT_SERVER_PORT", "8501")
    project_q = shlex.quote(str(_PROJECT_DIR))
    run_sh_q = shlex.quote(str(run_sh))
    script = f"""#!/usr/bin/env bash
sleep 2
kill -TERM {pid} 2>/dev/null || true
# Wait until the port is free (up to ~30s)
for _ in $(seq 1 60); do
    (exec 3<>/dev/tcp/127.0.0.1/{port}) 2>/dev/null && {{ exec 3<&-; sleep 0.5; }} || break
done
cd {project_q}
exec bash {run_sh_q}
"""
    tmp = Path("/tmp") / f"claude_code_web_restart_{pid}.sh"
    try:
        tmp.write_text(script)
        tmp.chmod(0o755)
        subprocess.Popen(
            ["bash", str(tmp)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    except Exception:
        return False
    return True

_COMPONENT_DIR = Path(__file__).parent / "chat_input_component"
_chat_input_component = components.declare_component(
    "claude_chat_input",
    path=str(_COMPONENT_DIR),
)


def chat_input(commands, session_id=None, disabled=False, key="claude_chat"):
    return _chat_input_component(
        commands=commands,
        session_id=session_id,
        disabled=disabled,
        key=key,
        default=None,
    )

st.set_page_config(
    page_title="Claude Code Web For MTG",
    page_icon="🤖",
    layout="wide",
)

st.markdown(
    """
    <style>
        .stApp { background-color: #0e1117; }
        /* Bubbles + sticky chat input share this same content column, so their
           left/right edges always line up (sidebar open or closed). */
        .block-container { padding: 2rem 1.25rem 24px 1.25rem; max-width: 980px; }
        /* Pin sidebar content to the top so nothing wastes vertical space. */
        section[data-testid="stSidebar"] .block-container,
        section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
            padding-top: 0.5rem !important;
        }
        /* Float the CLI status badge into the top-left corner of the sidebar,
           on the same horizontal line as Streamlit's built-in collapse button
           (which sits at top-right). The badge lives inside `with st.sidebar:`
           so it auto-hides when the sidebar collapses; we lift its wrapper out
           of flow so downstream sidebar content starts at the top edge. */
        section[data-testid="stSidebar"] { position: relative !important; }
        section[data-testid="stSidebar"] div[data-testid="stElementContainer"]:has(#cli-status-badge),
        section[data-testid="stSidebar"] .element-container:has(#cli-status-badge) {
            position: absolute !important;
            top: 12px;
            left: 16px;
            width: auto !important;
            margin: 0 !important;
            padding: 0 !important;
            z-index: 1000;
        }

        /* Dark scrollbars — Webkit (Chrome, Safari, Edge) */
        ::-webkit-scrollbar { width: 10px; height: 10px; }
        ::-webkit-scrollbar-track { background: #0e1117; }
        ::-webkit-scrollbar-thumb {
            background: #374151;
            border-radius: 8px;
            border: 2px solid #0e1117;
        }
        ::-webkit-scrollbar-thumb:hover { background: #4b5563; }
        ::-webkit-scrollbar-corner { background: #0e1117; }
        /* Dark scrollbars — Firefox */
        * { scrollbar-color: #374151 #0e1117; scrollbar-width: thin; }

        /* Chat input sticks to the bottom of the viewport BUT lives inside
           .block-container's flow — so it inherits the exact same horizontal
           bounds as the chat bubbles regardless of sidebar width or state.
           Sticky (vs. fixed) avoids hardcoding a sidebar-width offset. */
        div[data-testid="stElementContainer"]:has(iframe[title*="claude_chat_input"]),
        .element-container:has(iframe[title*="claude_chat_input"]) {
            position: sticky !important;
            bottom: 0;
            z-index: 100;
            background: linear-gradient(to top, #0e1117 70%, rgba(14,17,23,0));
            padding: 28px 0 16px 0;
            margin: 0 !important;
        }
        iframe[title*="claude_chat_input"] {
            width: 100% !important;
            margin: 0 !important;
            border: none !important;
            display: block;
            background: transparent;
        }
        .chat-bubble-user {
            background: #2563eb; color: #fff; padding: 12px 16px;
            border-radius: 14px 14px 4px 14px; margin: 6px 0; line-height: 1.55;
            white-space: pre-wrap; word-wrap: break-word;
        }
        .chat-bubble-assistant {
            background: #1f2937; color: #e5e7eb; padding: 12px 16px;
            border-radius: 14px 14px 14px 4px; margin: 6px 0; line-height: 1.55;
            white-space: pre-wrap; word-wrap: break-word;
            border: 1px solid #374151;
        }
        .meta { color: #6b7280; font-size: 11px; margin-top: 4px; }
        .error-bubble {
            background: #7f1d1d; color: #fecaca; padding: 12px 16px;
            border-radius: 8px; margin: 6px 0;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None
if "totals" not in st.session_state:
    st.session_state.totals = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "cost_usd": 0.0,
        "turns": 0,
    }
if "last_usage" not in st.session_state:
    st.session_state.last_usage = None
if "start_new_session" not in st.session_state:
    st.session_state.start_new_session = False
_STATE_FILE = Path.home() / ".claude" / "claude_code_web_state.json"


def _load_persisted_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_persisted_state() -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"recent_projects": st.session_state.recent_projects}),
            encoding="utf-8",
        )
    except Exception:
        pass


if "working_dir" not in st.session_state:
    st.session_state.working_dir = ""
if "recent_projects" not in st.session_state:
    st.session_state.recent_projects = _load_persisted_state().get("recent_projects", [])
if "claude_status" not in st.session_state:
    st.session_state.claude_status = None  # populated lazily in sidebar
if "resume_session_id" not in st.session_state:
    st.session_state.resume_session_id = None
if "pending_resume" not in st.session_state:
    st.session_state.pending_resume = False
if "picker_version" not in st.session_state:
    st.session_state.picker_version = 0
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "_boot_commit" not in st.session_state:
    st.session_state._boot_commit = _current_commit()
if "_update_info" not in st.session_state:
    st.session_state._update_info = None
if "_restart_now" not in st.session_state:
    st.session_state._restart_now = False


SYSTEM_USER_TAGS = (
    "<system-reminder>",
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
)

# Synthetic prompts injected by the harness / auto-continue mechanisms.
# We render neither the user side ("Continue…") nor the assistant placeholder
# ("No response requested.") since they carry no user-facing conversation value.
PLACEHOLDER_USER_TEXTS = {
    "continue from where you left off.",
    "continue from where you left off",
}
PLACEHOLDER_ASSISTANT_TEXTS = {
    "no response requested.",
    "no response requested",
}


def _is_system_user_text(s: str) -> bool:
    s = s.lstrip()
    if any(s.startswith(t) for t in SYSTEM_USER_TAGS):
        return True
    return s.strip().lower() in PLACEHOLDER_USER_TEXTS


def _is_placeholder_assistant_text(s: str) -> bool:
    return s.strip().lower() in PLACEHOLDER_ASSISTANT_TEXTS


def _encode_cwd(path: str) -> str:
    # /Users/ptk/foo → -Users-ptk-foo (Claude Code's project dir naming)
    return path.replace("/", "-")


def list_sessions(working_dir: str) -> list[dict]:
    proj_dir = Path.home() / ".claude" / "projects" / _encode_cwd(working_dir)
    if not proj_dir.exists():
        return []
    sessions: list[dict] = []
    for jsonl in proj_dir.glob("*.jsonl"):
        last_user = ""
        msg_count = 0
        try:
            with jsonl.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = d.get("type")
                    if t in ("user", "assistant"):
                        msg_count += 1
                    if t == "user":
                        content = (d.get("message") or {}).get("content")
                        if isinstance(content, str) and content.strip() and not _is_system_user_text(content):
                            last_user = content
        except Exception:
            continue
        if not last_user:
            continue
        sessions.append({
            "id": jsonl.stem,
            "path": str(jsonl),
            "mtime": jsonl.stat().st_mtime,
            "last_user": last_user.strip(),
            "msg_count": msg_count,
        })
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions[:10]


def load_session_messages(jsonl_path: str) -> list[dict]:
    msgs: list[dict] = []
    # Assistant lines that carry only thinking / tool_use (no text) are collapsed
    # into the next text-bearing assistant line so their reasoning still surfaces.
    pending_thinking: list[str] = []
    p = Path(jsonl_path)
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                ts = d.get("timestamp", "")
                ts_short = ts[11:19] if len(ts) >= 19 else ""
                if t == "user":
                    content = (d.get("message") or {}).get("content")
                    if isinstance(content, str) and content.strip() and not _is_system_user_text(content):
                        msgs.append({"role": "user", "content": content, "ts": ts_short})
                        pending_thinking = []
                elif t == "assistant":
                    content = (d.get("message") or {}).get("content")
                    parts: list[str] = []
                    if isinstance(content, list):
                        for blk in content:
                            if isinstance(blk, dict):
                                bt = blk.get("type")
                                if bt == "text":
                                    parts.append(blk.get("text", ""))
                                elif bt == "thinking":
                                    tk = (blk.get("thinking") or "").strip()
                                    if tk:
                                        pending_thinking.append(tk)
                    elif isinstance(content, str):
                        parts.append(content)
                    text = "\n".join(x for x in parts if x and x.strip())
                    if text and not _is_placeholder_assistant_text(text):
                        msg = {"role": "assistant", "content": text, "ts": ts_short}
                        if pending_thinking:
                            msg["thinking"] = "\n\n".join(pending_thinking)
                            pending_thinking = []
                        msgs.append(msg)
    except Exception:
        pass
    return _group_intermediate_assistants(msgs)


def _group_intermediate_assistants(msgs: list[dict]) -> list[dict]:
    # A single conversational turn often produces multiple assistant lines
    # (preamble text → tool_use → …→ final response). Only the last of the run
    # is the user-facing answer; earlier lines are Claude narrating its work
    # and should be tucked behind a collapsible "intermediate steps" UI so
    # they don't dominate the transcript.
    out: list[dict] = []
    i = 0
    while i < len(msgs):
        if msgs[i]["role"] != "assistant":
            out.append(msgs[i])
            i += 1
            continue
        j = i
        while j < len(msgs) and msgs[j]["role"] == "assistant":
            j += 1
        run = msgs[i:j]
        if len(run) == 1:
            out.append(run[0])
        else:
            final = dict(run[-1])
            all_thinking = [m.get("thinking") for m in run if m.get("thinking")]
            if all_thinking:
                final["thinking"] = "\n\n".join(all_thinking)
            final["intermediates"] = [
                {"content": m["content"], "ts": m.get("ts", "")}
                for m in run[:-1]
            ]
            out.append(final)
        i = j
    return out


def _new_assistant_messages_since_last_user(jsonl_path: str) -> list[dict]:
    """All text-bearing assistant messages after the last real user prompt.
    Mirrors load_session_messages' turn-flattening so live view == resume view."""
    p = Path(jsonl_path)
    if not p.exists():
        return []
    msgs: list[dict] = []
    pending_thinking: list[str] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                ts = d.get("timestamp", "")
                ts_short = ts[11:19] if len(ts) >= 19 else ""
                if t == "user":
                    content = (d.get("message") or {}).get("content")
                    if isinstance(content, str) and content.strip() and not _is_system_user_text(content):
                        msgs = []
                        pending_thinking = []
                elif t == "assistant":
                    content = (d.get("message") or {}).get("content")
                    parts: list[str] = []
                    if isinstance(content, list):
                        for blk in content:
                            if isinstance(blk, dict):
                                bt = blk.get("type")
                                if bt == "text":
                                    parts.append(blk.get("text", ""))
                                elif bt == "thinking":
                                    tk = (blk.get("thinking") or "").strip()
                                    if tk:
                                        pending_thinking.append(tk)
                    elif isinstance(content, str):
                        parts.append(content)
                    text = "\n".join(x for x in parts if x and x.strip())
                    if text and not _is_placeholder_assistant_text(text):
                        m = {"role": "assistant", "content": text, "ts": ts_short}
                        if pending_thinking:
                            m["thinking"] = "\n\n".join(pending_thinking)
                            pending_thinking = []
                        msgs.append(m)
    except Exception:
        return []
    # Apply the same intermediate-grouping load_session_messages does so the
    # live transcript and Resume view render identically.
    return _group_intermediate_assistants(msgs)


def pick_directory_macos() -> str | None:
    script = (
        'POSIX path of (choose folder with prompt "Select working directory")'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip().rstrip("/")
    return path or None


def check_claude_status() -> tuple[str, str]:
    """Return ('green'|'yellow'|'red', short message)."""
    if not shutil.which("claude"):
        return "red", "claude CLI not found in PATH"
    try:
        kc = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials"],
            capture_output=True, text=True, timeout=5,
        )
        if kc.returncode == 0:
            return "green", "Authenticated (macOS Keychain)"
    except Exception:
        pass
    for p in (
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".config" / "claude" / "credentials.json",
    ):
        if p.exists():
            return "green", f"Authenticated ({p})"
    return "yellow", "claude installed, but no credentials detected — run `claude login`"


def render_login_gate(status: str, status_msg: str) -> None:
    if status == "yellow":
        icon = "🔒"
        title = "Claude에 로그인되어 있지 않습니다"
        body = "터미널에서 아래 명령을 실행해 로그인한 뒤, [다시 확인]을 눌러주세요."
        command = "claude login"
    else:
        icon = "⛔"
        title = "Claude CLI를 찾을 수 없습니다"
        body = "Claude Code를 먼저 설치하세요. 설치 후 [다시 확인]을 눌러주세요."
        command = "https://docs.claude.com/en/docs/claude-code/setup"

    st.markdown(
        f"""
        <div style='
            max-width: 520px;
            margin: 80px auto 24px;
            padding: 32px;
            background: #1f2937;
            border: 1px solid #374151;
            border-radius: 16px;
            text-align: center;
            color: #e5e7eb;
            box-shadow: 0 12px 40px rgba(0,0,0,0.5);
        '>
            <div style='font-size: 56px; margin-bottom: 16px;'>{icon}</div>
            <h2 style='margin: 0 0 12px; color: #f9fafb; font-size: 20px;'>{title}</h2>
            <p style='color: #cbd5e1; margin: 0 0 20px; font-size: 14px;'>{body}</p>
            <code style='
                display: inline-block;
                padding: 10px 16px;
                background: #0e1117;
                border: 1px solid #374151;
                border-radius: 8px;
                color: #93c5fd;
                font-size: 14px;
                user-select: all;
            '>{command}</code>
            <p style='color: #6b7280; font-size: 12px; margin-top: 18px;'>{status_msg}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        if st.button("🔄 다시 확인", use_container_width=True, key="login_gate_recheck"):
            st.session_state.claude_status = check_claude_status()
            st.rerun()

    st.stop()


def _read_description(p: Path) -> str:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    for line in text[3:end].splitlines():
        line = line.strip()
        if line.startswith("description:"):
            return line[len("description:"):].strip().strip('"\'')
    return ""


BUILTIN_COMMANDS: list[tuple[str, str]] = [
    ("/agents", "Manage and view available subagents"),
    ("/bug", "Report a bug to Anthropic"),
    ("/clear", "Clear conversation history and start fresh"),
    ("/compact", "Compact the conversation context"),
    ("/config", "Open the configuration menu"),
    ("/context", "Show context window utilization"),
    ("/cost", "Show token usage and cost for the session"),
    ("/doctor", "Diagnose Claude Code installation"),
    ("/export", "Export the current conversation"),
    ("/fast", "Toggle fast mode (Opus 4.6, faster output)"),
    ("/help", "Show available commands and shortcuts"),
    ("/init", "Initialize a new CLAUDE.md file with codebase documentation"),
    ("/login", "Authenticate with Claude"),
    ("/logout", "Sign out of Claude"),
    ("/mcp", "Manage MCP server connections"),
    ("/memory", "View or edit memory files"),
    ("/model", "Select or view the current Claude model"),
    ("/permissions", "Configure tool permissions"),
    ("/pr-comments", "View pull request comments"),
    ("/release-notes", "Show Claude Code release notes"),
    ("/resume", "Resume a previous conversation"),
    ("/review", "Review a pull request"),
    ("/security-review", "Run a security review on pending changes"),
    ("/skills", "List available skills"),
    ("/status", "Show current session status"),
    ("/statusline", "Configure the status line"),
    ("/todos", "View the task list"),
    ("/upgrade", "Upgrade Claude Code to the latest version"),
    ("/vim", "Toggle vim mode for the input"),
]


def discover_slash_commands(working_dir: str) -> list[dict]:
    home = Path.home()
    wd = Path(working_dir)
    items: dict[str, dict] = {}

    def add(name: str, desc: str, source: str) -> None:
        if name not in items:
            items[name] = {"name": name, "description": desc, "source": source}

    for name, desc in BUILTIN_COMMANDS:
        add(name, desc, "built-in")

    cmd_dir = home / ".claude" / "commands"
    if cmd_dir.exists():
        for p in cmd_dir.glob("*.md"):
            add(f"/{p.stem}", _read_description(p), "user command")
    skill_dir = home / ".claude" / "skills"
    if skill_dir.exists():
        for p in skill_dir.glob("*/SKILL.md"):
            add(f"/{p.parent.name}", _read_description(p), "user skill")

    proj_cmds = wd / ".claude" / "commands"
    if proj_cmds.exists():
        for p in proj_cmds.glob("*.md"):
            add(f"/{p.stem}", _read_description(p), "project command")
    proj_skills = wd / ".claude" / "skills"
    if proj_skills.exists():
        for p in proj_skills.glob("*/SKILL.md"):
            add(f"/{p.parent.name}", _read_description(p), "project skill")

    plugin_cache = home / ".claude" / "plugins" / "cache"
    if plugin_cache.exists():
        for marketplace in plugin_cache.iterdir():
            if not marketplace.is_dir():
                continue
            for plugin_dir in marketplace.iterdir():
                if not plugin_dir.is_dir():
                    continue
                versions = [v for v in plugin_dir.iterdir() if v.is_dir()]
                in_use = [v for v in versions if (v / ".in_use").exists()]
                chosen = in_use[0] if in_use else (versions[0] if versions else None)
                if not chosen:
                    continue
                cdir = chosen / "commands"
                if cdir.exists():
                    for p in cdir.glob("*.md"):
                        add(f"/{plugin_dir.name}:{p.stem}", _read_description(p), f"{plugin_dir.name} command")
                sdir = chosen / "skills"
                if sdir.exists():
                    for sp in sdir.glob("*/SKILL.md"):
                        add(f"/{plugin_dir.name}:{sp.parent.name}", _read_description(sp), f"{plugin_dir.name} skill")

    return sorted(items.values(), key=lambda x: x["name"])


def reset_conversation_state() -> None:
    st.session_state.messages = []
    st.session_state.totals = {
        "input": 0, "output": 0, "cache_read": 0,
        "cache_creation": 0, "cost_usd": 0.0, "turns": 0,
    }
    st.session_state.last_usage = None
    st.session_state.start_new_session = True
    st.session_state.pending_prompt = None
    st.session_state.resume_session_id = None
    st.session_state.pending_resume = False
    st.session_state.current_session_id = None
    st.session_state.picker_version += 1
    # Drop the sidebar selectbox's persisted value — otherwise on the next rerun
    # it returns the previously-selected session label and the picker's `else`
    # branch reloads its messages from disk, masking the clear.
    st.session_state.pop(f"session_picker_{st.session_state.working_dir}", None)

def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_k(n: int) -> str:
    if n is None:
        return "0"
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K"
    return f"{n / 1_000_000:.2f}M"


if st.session_state.claude_status is None:
    st.session_state.claude_status = check_claude_status()
_gate_status, _gate_msg = st.session_state.claude_status
if _gate_status != "green":
    render_login_gate(_gate_status, _gate_msg)

# ---------------- Update-available notification & restart handler ----------------
# User clicks "지금 업데이트" (the anchor below sets ?do_update=1) → we spawn a
# detached restart script and switch UI into the "restarting" overlay state.
if st.query_params.get("do_update"):
    _ok = _restart_service()
    try:
        del st.query_params["do_update"]
    except Exception:
        pass
    if _ok:
        st.session_state._restart_now = True
    st.rerun()

if st.session_state.get("_restart_now"):
    st.markdown(
        "<div style='position: fixed; inset: 0; z-index: 100001;"
        " display:flex; align-items:center; justify-content:center;"
        " background: rgba(14,17,23,0.94);'>"
        "<div style='background:#1f2937; border:1px solid #374151;"
        " padding:32px 40px; border-radius:16px; text-align:center; color:#e5e7eb;"
        " box-shadow:0 12px 40px rgba(0,0,0,0.6);'>"
        "<div style='font-size:40px; margin-bottom:12px;'>🔄</div>"
        "<div style='font-size:16px; font-weight:600;'>재시작 중…</div>"
        "<div style='color:#9ca3af; font-size:12px; margin-top:8px;'>"
        "서버가 준비되면 자동으로 새로고침됩니다.</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )
    components.html(
        """
        <script>
        (function () {
          function tryReload() {
            fetch(String(window.top.location.origin) + "/_stcore/health", { cache: "no-store" })
              .then(function (r) {
                if (r && r.ok) { try { window.top.location.reload(); } catch (_) {} }
                else { setTimeout(tryReload, 800); }
              })
              .catch(function () { setTimeout(tryReload, 800); });
          }
          setTimeout(tryReload, 4000);
        })();
        </script>
        """,
        height=0,
    )
elif st.session_state.get("_update_info"):
    _info = st.session_state["_update_info"]
    _subject = html.escape((_info.get("subject", "") or "")[:140])
    st.markdown(
        f"<div style='position: fixed; top: 72px; right: 20px; z-index: 100000;"
        f" background:#1f2937; border:1px solid #22c55e; border-radius:12px;"
        f" padding:14px 18px; max-width:340px; color:#e5e7eb;"
        f" box-shadow:0 8px 28px rgba(0,0,0,0.5); font-size:12.5px;'>"
        f"<div style='display:flex; align-items:center; gap:8px; font-weight:600;"
        f" font-size:13px;'><span>🔔</span><span>업데이트 알림</span></div>"
        f"<div style='color:#9ca3af; font-size:11.5px; margin:6px 0 12px;"
        f" word-break:break-word;'>새 버전: {_subject}</div>"
        f"<a href='?do_update=1' style='display:inline-block; background:#22c55e;"
        f" color:#0e1117; font-weight:600; font-size:12.5px; padding:7px 14px;"
        f" border-radius:8px; text-decoration:none;'>🔁 지금 업데이트</a>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _apply_project_pick(picked_path: str) -> None:
    """Add path to recents (LRU cap 5), set working_dir, reset conversation.
    Also blanks the recent-projects selectbox back to its placeholder so the
    next click — even on the same item — always fires on_change (Streamlit
    does not fire on re-selecting the currently-displayed value)."""
    _recents = [p for p in st.session_state.recent_projects if p != picked_path]
    _recents.insert(0, picked_path)
    st.session_state.recent_projects = _recents[:5]
    st.session_state.working_dir = picked_path
    st.session_state["recent_picker"] = None
    _save_persisted_state()
    reset_conversation_state()


def _on_recent_pick_change() -> None:
    picked = st.session_state.get("recent_picker")
    if not picked:
        return
    if picked != st.session_state.working_dir:
        _apply_project_pick(picked)
    else:
        # Same project re-picked — just reset the picker so subsequent clicks fire.
        st.session_state["recent_picker"] = None


with st.sidebar:
    status, _status_msg = st.session_state.claude_status
    dot_color = {"green": "#22c55e", "yellow": "#eab308", "red": "#ef4444"}[status]
    label = {"green": "Claude CLI ready", "yellow": "Auth not detected", "red": "Claude CLI unavailable"}[status]
    st.markdown(
        f"<div id='cli-status-badge' style='display:flex; align-items:center; gap:6px;"
        f" font-size:11.5px; color:#cbd5e1; line-height:1.2; pointer-events:none;'>"
        f"<span style='display:inline-block; width:7px; height:7px; border-radius:50%;"
        f" background:{dot_color}; box-shadow:0 0 4px {dot_color};'></span>"
        f"<span>{label}</span></div>",
        unsafe_allow_html=True,
    )

    st.subheader("Working directory")
    if st.session_state.working_dir:
        _basename = Path(st.session_state.working_dir).name or st.session_state.working_dir
        st.markdown(
            f"<div style='color:#9ca3af; font-size:12px; padding:4px 2px 8px 2px;"
            f" font-family: ui-monospace, SFMono-Regular, Menlo, monospace;'"
            f" title='{st.session_state.working_dir}'>📂 {_basename}</div>",
            unsafe_allow_html=True,
        )

    if st.button("📁 Select folder…", use_container_width=True, key="pick_folder"):
        picked = pick_directory_macos()
        if picked and picked != st.session_state.working_dir:
            _apply_project_pick(picked)
            st.rerun()

    if st.session_state.recent_projects:
        st.selectbox(
            "최근 프로젝트",
            st.session_state.recent_projects,
            index=None,
            format_func=lambda p: Path(p).name or p,
            placeholder="— 프로젝트 선택 —",
            key="recent_picker",
            on_change=_on_recent_pick_change,
            help="이전에 선택했던 프로젝트 (최대 5개)",
        )

    working_dir = st.session_state.working_dir

    sessions = list_sessions(working_dir) if working_dir else []
    NEW_LABEL = "🆕 New session"

    def _fmt_session(s: dict) -> str:
        when = datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
        preview = s["last_user"].splitlines()[0][:50]
        return f"{when} · {preview} ({s['msg_count']})"

    if working_dir:
        labels = [NEW_LABEL] + [_fmt_session(s) for s in sessions]
        current = 0
        if st.session_state.resume_session_id:
            for i, s in enumerate(sessions):
                if s["id"] == st.session_state.resume_session_id:
                    current = i + 1
                    break

        picker_disabled = st.session_state.pending_prompt is not None
        selected = st.selectbox(
            "Resume session",
            labels,
            index=current,
            key=f"session_picker_{working_dir}_{st.session_state.picker_version}",
            disabled=picker_disabled,
            help="Pick a previous session to resume in this directory, or start fresh.",
        )

        if selected == NEW_LABEL:
            if st.session_state.resume_session_id is not None:
                reset_conversation_state()
                st.rerun()
        else:
            picked_session = sessions[labels.index(selected) - 1]
            if st.session_state.resume_session_id != picked_session["id"]:
                st.session_state.resume_session_id = picked_session["id"]
                st.session_state.current_session_id = picked_session["id"]
                st.session_state.pending_resume = True
                st.session_state.messages = load_session_messages(picked_session["path"])
                st.session_state.totals = {
                    "input": 0, "output": 0, "cache_read": 0,
                    "cache_creation": 0, "cost_usd": 0.0, "turns": 0,
                }
                st.session_state.last_usage = None
                st.session_state.start_new_session = False
                st.session_state.pending_prompt = None
                st.session_state._scroll_bottom = True
                st.rerun()
    st.divider()

    st.subheader("Session Tokens")
    t = st.session_state.totals
    # `input_tokens` alone counts only tokens NOT covered by prompt cache,
    # so it stays tiny once the session is warmed up. Sum in cache reads +
    # cache writes so the number reflects the actual context size Claude saw.
    _input_total = t["input"] + t["cache_read"] + t["cache_creation"]
    col1, col2 = st.columns(2)
    col1.metric("Input", fmt_k(_input_total))
    col2.metric("Output", fmt_k(t["output"]))
    st.caption(f"Turns: {t['turns']} · Messages: {len(st.session_state.messages)}")

    if st.session_state.last_usage:
        with st.expander("Last turn details", expanded=False):
            u = st.session_state.last_usage
            st.write(
                {
                    "input": u.get("input_tokens"),
                    "output": u.get("output_tokens"),
                    "cache_read": u.get("cache_read_input_tokens"),
                    "cache_write": u.get("cache_creation_input_tokens"),
                    "model": u.get("_model"),
                    "cost_usd": u.get("_cost_usd"),
                    "duration_ms": u.get("_duration_ms"),
                }
            )

    if st.session_state.start_new_session:
        st.info("New session will start on next message.")
    elif st.session_state.pending_resume and st.session_state.resume_session_id:
        st.info(f"Resuming session `{st.session_state.resume_session_id[:8]}…` on next message.")
    elif st.session_state.resume_session_id:
        st.caption(f"Continuing resumed session `{st.session_state.resume_session_id[:8]}…`")
    st.caption(
        "Core command (now JSON for token tracking):\n\n"
        "`claude [--resume <id> | -c] --dangerously-skip-permissions -p \"USER INPUT\" --output-format json`"
    )

    if st.session_state.get("_boot_commit"):
        _v = st.session_state._boot_commit[:7]
        st.caption(f"version · `{_v}`")


def run_claude(
    user_input: str,
    cwd: str,
    timeout: int,
    continue_session: bool,
    resume_id: str | None = None,
) -> tuple[str, float, int, dict | None]:
    started = time.time()
    cmd = ["claude"]
    if resume_id:
        cmd += ["--resume", resume_id]
    elif continue_session:
        cmd.append("-c")
    cmd += [
        "--dangerously-skip-permissions",
        "-p",
        user_input,
        "--output-format",
        "json",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    elapsed = time.time() - started
    if proc.returncode != 0:
        return proc.stderr.strip() or f"(exit {proc.returncode})", elapsed, proc.returncode, None

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout.strip(), elapsed, 0, None

    text = (data.get("result") or "").strip()
    usage = data.get("usage") or {}
    usage["_cost_usd"] = data.get("total_cost_usd")
    usage["_duration_ms"] = data.get("duration_ms")
    usage["_session_id"] = data.get("session_id")
    model_usage = data.get("modelUsage") or {}
    usage["_model"] = next(iter(model_usage.keys()), None)
    return text, elapsed, 0, usage


st.title("🤖 Claude Code Web For MTG")
st.caption("Streamlit-based non-streaming wrapper for the claude CLI")

if not working_dir:
    st.markdown(
        "<div style='max-width: 520px; margin: 60px auto; padding: 32px;"
        " background: #1f2937; border: 1px solid #374151; border-radius: 16px;"
        " text-align: center; color: #e5e7eb;'>"
        "<div style='font-size: 44px; margin-bottom: 12px;'>📁</div>"
        "<h3 style='margin: 0 0 10px; color: #f9fafb; font-size: 18px;'>"
        "Select a working directory to begin</h3>"
        "<p style='color: #cbd5e1; margin: 0; font-size: 14px;'>"
        "Use the sidebar’s <b>Select folder…</b> button, or pick from"
        " 최근 프로젝트 if you have any.</p></div>",
        unsafe_allow_html=True,
    )
    st.stop()

for i, msg in enumerate(st.session_state.messages):
    role = msg["role"]
    content = msg["content"]
    ts = msg.get("ts", "")
    meta = msg.get("meta", "")
    thinking = msg.get("thinking") if role == "assistant" else None
    intermediates = msg.get("intermediates") if role == "assistant" else None
    cls = "chat-bubble-user" if role == "user" else ("error-bubble" if role == "error" else "chat-bubble-assistant")
    label = "You" if role == "user" else ("Error" if role == "error" else "Claude")
    if thinking:
        with st.expander("🧠 Thinking", expanded=False):
            st.markdown(
                "<div style='color:#9ca3af; font-style:italic; white-space:pre-wrap;"
                " font-size:13px; line-height:1.6;'>"
                f"{html.escape(thinking)}</div>",
                unsafe_allow_html=True,
            )
    if intermediates:
        with st.expander(f"↳ {len(intermediates)} intermediate step(s)", expanded=False):
            for im in intermediates:
                st.markdown(
                    "<div style='background:#111827; border:1px solid #2a3441;"
                    " padding:10px 14px; border-radius:8px; margin:6px 0;"
                    " color:#9ca3af; font-size:13px; line-height:1.55;"
                    " white-space:pre-wrap; word-wrap:break-word;'>"
                    f"{im['content']}</div>",
                    unsafe_allow_html=True,
                )
                if im.get("ts"):
                    st.markdown(
                        f"<div class='meta' style='margin:0 0 4px 4px;'>{im['ts']}</div>",
                        unsafe_allow_html=True,
                    )
    st.markdown(f"<div class='{cls}'><b>{label}</b><br>{content}</div>", unsafe_allow_html=True)
    if ts or meta:
        st.markdown(f"<div class='meta'>{ts} {meta}</div>", unsafe_allow_html=True)

is_running = st.session_state.pending_prompt is not None


def _submit_message(text: str) -> None:
    if text.strip() == "/clear":
        reset_conversation_state()
        st.session_state._scroll_bottom = True
        st.rerun()
        return
    st.session_state.messages.append(
        {"role": "user", "content": text, "ts": datetime.now().strftime("%H:%M:%S")}
    )
    st.session_state.pending_prompt = text
    st.session_state._scroll_bottom = True
    st.rerun()


all_cmds = discover_slash_commands(working_dir)
result = chat_input(
    commands=all_cmds,
    session_id=st.session_state.current_session_id,
    disabled=is_running,
    key="claude_chat",
)

if st.session_state.pop("_scroll_bottom", False):
    components.html(
        """
        <script>
        (function () {
          function doScroll() {
            var w = window.top || window.parent;
            if (!w) return;
            var d = w.document;
            // Try every plausible scroll container — different Streamlit versions
            // put the scrollbar on different elements.
            var targets = [
              d.scrollingElement,
              d.documentElement,
              d.body,
              d.querySelector('section.main'),
              d.querySelector('[data-testid="stAppViewContainer"]'),
              d.querySelector('section[data-testid="stMain"]'),
              d.querySelector('[data-testid="stMainBlockContainer"]'),
            ].filter(Boolean);
            targets.forEach(function (t) {
              var h = t.scrollHeight || 9999999;
              try { t.scrollTop = h; } catch (_) {}
            });
            try { w.scrollTo(0, 9999999); } catch (_) {}
          }
          // Retry across multiple paint cycles — sticky input + iframe height
          // settles ~200-500ms after the rerun.
          doScroll();
          [50, 150, 400, 800, 1500].forEach(function (ms) { setTimeout(doScroll, ms); });
        })();
        </script>
        """,
        height=0,
    )
if result and isinstance(result, dict):
    # Timestamp-based dedup: JS sends Date.now() per submission. Survives iframe
    # reloads (which would reset a local counter) since wall-clock time only moves forward.
    incoming_ts = int(result.get("ts", 0) or 0)
    last_ts = int(st.session_state.get("_chat_ts", 0))
    text = (result.get("text") or "").strip()
    if text and incoming_ts > last_ts and not is_running:
        st.session_state._chat_ts = incoming_ts
        _submit_message(text)

if st.session_state.pending_prompt is not None:
    prompt = st.session_state.pending_prompt
    continue_session = not st.session_state.start_new_session
    resume_id = st.session_state.resume_session_id if st.session_state.pending_resume else None
    with st.spinner("Claude is thinking... (non-streaming, please wait)"):
        try:
            output, elapsed, code, usage = run_claude(
                prompt, working_dir, CLAUDE_TIMEOUT_SEC, continue_session, resume_id=resume_id,
            )
            st.session_state.start_new_session = False
            st.session_state.pending_resume = False
            if code == 0:
                meta_extra = ""
                if usage:
                    st.session_state.last_usage = usage
                    st.session_state.totals["input"] += usage.get("input_tokens", 0) or 0
                    st.session_state.totals["output"] += usage.get("output_tokens", 0) or 0
                    st.session_state.totals["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
                    st.session_state.totals["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0
                    st.session_state.totals["cost_usd"] += usage.get("_cost_usd", 0) or 0
                    st.session_state.totals["turns"] += 1
                    in_t = usage.get("input_tokens", 0) or 0
                    out_t = usage.get("output_tokens", 0) or 0
                    cr_t = usage.get("cache_read_input_tokens", 0) or 0
                    cw_t = usage.get("cache_creation_input_tokens", 0) or 0
                    meta_extra = f" · in:{in_t:,} out:{out_t:,} cache_r:{cr_t:,} cache_w:{cw_t:,}"
                sid = usage.get("_session_id") if usage else None
                new_msgs: list[dict] = []
                if sid:
                    st.session_state.current_session_id = sid
                    jsonl_path = (
                        Path.home() / ".claude" / "projects"
                        / _encode_cwd(working_dir) / f"{sid}.jsonl"
                    )
                    new_msgs = _new_assistant_messages_since_last_user(str(jsonl_path))
                if new_msgs:
                    # Timing + token meta belongs on the final (result) bubble.
                    new_msgs[-1]["meta"] = f"({elapsed:.1f}s){meta_extra}"
                    st.session_state.messages.extend(new_msgs)
                else:
                    # Fallback: JSONL not readable / no session_id — show just the CLI result.
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": output,
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "meta": f"({elapsed:.1f}s){meta_extra}",
                        }
                    )
                # Poll git for new upstream commits after each successful turn.
                # Silent no-op if not a git repo / offline / already up-to-date.
                try:
                    _upd = _check_updates()
                    if _upd:
                        st.session_state._update_info = _upd
                except Exception:
                    pass
            else:
                st.session_state.messages.append(
                    {
                        "role": "error",
                        "content": output,
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "meta": f"(exit {code}, {elapsed:.1f}s)",
                    }
                )
        except subprocess.TimeoutExpired:
            st.session_state.messages.append(
                {
                    "role": "error",
                    "content": f"Command timed out after {CLAUDE_TIMEOUT_SEC}s.",
                    "ts": datetime.now().strftime("%H:%M:%S"),
                }
            )
        except FileNotFoundError:
            st.session_state.messages.append(
                {
                    "role": "error",
                    "content": "`claude` CLI not found. Install Claude Code first.",
                    "ts": datetime.now().strftime("%H:%M:%S"),
                }
            )
        except Exception as e:
            st.session_state.messages.append(
                {
                    "role": "error",
                    "content": f"Unexpected error: {e}",
                    "ts": datetime.now().strftime("%H:%M:%S"),
                }
            )
    st.session_state.pending_prompt = None
    st.session_state._scroll_bottom = True
    st.rerun()
