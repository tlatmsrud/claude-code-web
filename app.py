import json
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

CLAUDE_TIMEOUT_SEC = 6000

_COMPONENT_DIR = Path(__file__).parent / "chat_input_component"
_chat_input_component = components.declare_component(
    "claude_chat_input",
    path=str(_COMPONENT_DIR),
)


def chat_input(commands, disabled=False, key="claude_chat"):
    return _chat_input_component(
        commands=commands,
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
        .block-container { padding-top: 2rem; padding-bottom: 180px; max-width: 980px; }

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

        /* Pin the chat input to the bottom of the viewport (ChatGPT/Claude-style).
           Lives outside .block-container's flow but spans the main area horizontally. */
        div[data-testid="stElementContainer"]:has(iframe[title*="claude_chat_input"]),
        .element-container:has(iframe[title*="claude_chat_input"]) {
            position: fixed !important;
            bottom: 0;
            left: 244px;   /* leave room for default Streamlit sidebar */
            right: 0;
            z-index: 100;
            background: linear-gradient(to top, #0e1117 70%, rgba(14,17,23,0));
            padding: 28px 1.25rem 16px 1.25rem;
            margin: 0 !important;
        }
        /* Collapse sidebar → input spans full width */
        body:has([data-testid="stSidebar"][aria-expanded="false"])
            div[data-testid="stElementContainer"]:has(iframe[title*="claude_chat_input"]) {
            left: 0;
        }
        iframe[title*="claude_chat_input"] {
            width: 100% !important;
            max-width: 940px !important;
            margin: 0 auto !important;
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
if "working_dir" not in st.session_state:
    st.session_state.working_dir = "/Users/ptk/claude-workspace"
if "claude_status" not in st.session_state:
    st.session_state.claude_status = None  # populated lazily in sidebar
if "resume_session_id" not in st.session_state:
    st.session_state.resume_session_id = None
if "pending_resume" not in st.session_state:
    st.session_state.pending_resume = False
if "picker_version" not in st.session_state:
    st.session_state.picker_version = 0


SYSTEM_USER_TAGS = (
    "<system-reminder>",
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
)


def _is_system_user_text(s: str) -> bool:
    s = s.lstrip()
    return any(s.startswith(t) for t in SYSTEM_USER_TAGS)


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
    return sessions


def load_session_messages(jsonl_path: str) -> list[dict]:
    msgs: list[dict] = []
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
                elif t == "assistant":
                    content = (d.get("message") or {}).get("content")
                    parts: list[str] = []
                    if isinstance(content, list):
                        for blk in content:
                            if isinstance(blk, dict) and blk.get("type") == "text":
                                parts.append(blk.get("text", ""))
                    elif isinstance(content, str):
                        parts.append(content)
                    text = "\n".join(x for x in parts if x and x.strip())
                    if text:
                        msgs.append({"role": "assistant", "content": text, "ts": ts_short})
    except Exception:
        pass
    return msgs


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

with st.sidebar:
    status, status_msg = st.session_state.claude_status
    dot_color = {"green": "#22c55e", "yellow": "#eab308", "red": "#ef4444"}[status]
    label = {"green": "Claude CLI ready", "yellow": "Auth not detected", "red": "Claude CLI unavailable"}[status]
    c1, c2 = st.columns([5, 1], vertical_alignment="center")
    with c1:
        st.markdown(
            f"<div style='display:flex; align-items:center; gap:10px; padding:4px 0;'>"
            f"<span style='display:inline-block; width:10px; height:10px; border-radius:50%;"
            f" background:{dot_color}; box-shadow:0 0 6px {dot_color};'></span>"
            f"<span style='color:#cbd5e1; font-size:13px;'>{label}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c2:
        if st.button("↻", help=f"Re-check · {status_msg}", key="claude_recheck"):
            st.session_state.claude_status = check_claude_status()
            st.rerun()
    st.divider()

    st.header("Working directory")
    st.code(st.session_state.working_dir, language=None)
    if st.button("📁 Select folder…", use_container_width=True):
        picked = pick_directory_macos()
        if picked:
            st.session_state.working_dir = picked
            reset_conversation_state()
            st.rerun()
    working_dir = st.session_state.working_dir

    sessions = list_sessions(working_dir)
    sessions_by_id = {s["id"]: s for s in sessions}
    NEW_VALUE = "__new_session__"
    NEW_LABEL = "🆕 New session"

    def _fmt_option(val: str) -> str:
        # selectbox options are session ids (guaranteed-unique UUIDs);
        # only the display label is allowed to collide.
        if val == NEW_VALUE:
            return NEW_LABEL
        s = sessions_by_id.get(val)
        if not s:
            return val
        when = datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
        preview = s["last_user"].splitlines()[0][:50]
        return f"{when} · {preview} ({s['msg_count']})"

    options = [NEW_VALUE] + [s["id"] for s in sessions]
    current = 0
    if st.session_state.resume_session_id:
        for i, s in enumerate(sessions):
            if s["id"] == st.session_state.resume_session_id:
                current = i + 1
                break

    picker_disabled = st.session_state.pending_prompt is not None
    selected = st.selectbox(
        "Resume session",
        options,
        index=current,
        format_func=_fmt_option,
        key=f"session_picker_{working_dir}_{st.session_state.picker_version}",
        disabled=picker_disabled,
        help="Pick a previous session to resume in this directory, or start fresh.",
    )

    if selected == NEW_VALUE:
        if st.session_state.resume_session_id is not None:
            reset_conversation_state()
            st.rerun()
    else:
        picked_session = sessions_by_id[selected]
        if st.session_state.resume_session_id != picked_session["id"]:
            st.session_state.resume_session_id = picked_session["id"]
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
    total_input_all = t["input"] + t["cache_read"] + t["cache_creation"]
    col1, col2 = st.columns(2)
    col1.metric("Input (total)", fmt_k(total_input_all))
    col2.metric("Output", fmt_k(t["output"]))
    col1.metric("Cache read", fmt_k(t["cache_read"]))
    col2.metric("Cache write", fmt_k(t["cache_creation"]))
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
        "Core command (streaming for live intermediate output):\n\n"
        "`claude [--resume <id> | -c] --dangerously-skip-permissions -p \"USER INPUT\" --output-format stream-json --verbose`"
    )


def run_claude_stream(
    user_input: str,
    cwd: str,
    timeout: int,
    continue_session: bool,
    resume_id: str | None = None,
):
    """Generator yielding events from a streaming `claude` invocation.

    Event kinds:
      - text:        {'text': str}                    — assistant text chunk
      - tool_use:    {'name': str, 'input': dict}
      - tool_result: {'is_error': bool, 'content': str}
      - thinking:    {'text': str}
      - error:       {'text': str}                    — stderr / cli failure
      - done:        {'text', 'elapsed', 'code', 'usage'} — terminal
    """
    started = time.time()
    cmd = ["claude"]
    if resume_id:
        cmd += ["--resume", resume_id]
    elif continue_session:
        cmd.append("-c")
    cmd += [
        "--dangerously-skip-permissions",
        "-p", user_input,
        "--output-format", "stream-json",
        "--verbose",
    ]

    text_parts: list[str] = []
    final_text: str | None = None
    usage: dict | None = None

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        bufsize=1,
    )

    timed_out = {"v": False}
    def _kill_on_timeout():
        timed_out["v"] = True
        try: proc.kill()
        except Exception: pass
    timer = threading.Timer(timeout, _kill_on_timeout)
    timer.start()

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            et = evt.get("type")

            if et == "assistant":
                for blk in ((evt.get("message") or {}).get("content") or []):
                    if not isinstance(blk, dict):
                        continue
                    bt = blk.get("type")
                    if bt == "text":
                        t = blk.get("text") or ""
                        if t:
                            text_parts.append(t)
                            yield {"kind": "text", "text": t}
                    elif bt == "tool_use":
                        yield {
                            "kind": "tool_use",
                            "name": blk.get("name", "?"),
                            "input": blk.get("input") or {},
                        }
                    elif bt == "thinking":
                        yield {"kind": "thinking", "text": blk.get("thinking", "")}

            elif et == "user":
                for blk in ((evt.get("message") or {}).get("content") or []):
                    if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                        continue
                    c = blk.get("content")
                    if isinstance(c, list):
                        c = "\n".join(
                            (b.get("text") if isinstance(b, dict) else str(b))
                            for b in c if b
                        )
                    elif not isinstance(c, str):
                        c = "" if c is None else str(c)
                    yield {
                        "kind": "tool_result",
                        "is_error": bool(blk.get("is_error")),
                        "content": (c or "")[:2000],
                    }

            elif et == "result":
                u = evt.get("usage") or {}
                u["_cost_usd"] = evt.get("total_cost_usd")
                u["_duration_ms"] = evt.get("duration_ms")
                mu = evt.get("modelUsage") or {}
                u["_model"] = next(iter(mu.keys()), None)
                usage = u
                ft = (evt.get("result") or "").strip()
                if ft:
                    final_text = ft
    finally:
        timer.cancel()
        try:
            proc.wait(timeout=2)
        except Exception:
            try: proc.kill()
            except Exception: pass

    return_code = proc.returncode if proc.returncode is not None else -1
    stderr = ""
    try:
        stderr = (proc.stderr.read() or "").strip()
    except Exception:
        pass

    if timed_out["v"]:
        yield {"kind": "error", "text": f"Timed out after {timeout}s"}
        return_code = -1
    elif return_code != 0 and stderr:
        yield {"kind": "error", "text": stderr[:2000]}

    text = (
        final_text
        if final_text is not None
        else "\n".join(p for p in text_parts if p.strip()).strip()
    )
    yield {
        "kind": "done",
        "text": text,
        "elapsed": time.time() - started,
        "code": return_code,
        "usage": usage,
    }


st.title("🤖 Claude Code Web For MTG")
st.caption("Streamlit-based streaming wrapper for the claude CLI")

for msg in st.session_state.messages:
    role = msg["role"]
    content = msg["content"]
    ts = msg.get("ts", "")
    meta = msg.get("meta", "")
    cls = "chat-bubble-user" if role == "user" else ("error-bubble" if role == "error" else "chat-bubble-assistant")
    label = "You" if role == "user" else ("Error" if role == "error" else "Claude")
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
result = chat_input(commands=all_cmds, disabled=is_running, key="claude_chat")

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
    # Nonce-based dedup: each submission carries a fresh UUID. Equality check
    # (not ordering) means clock skew, iframe reloads, or counter resets
    # cannot silently swallow new messages — every distinct nonce is "new".
    text = (result.get("text") or "").strip()
    incoming_nonce = result.get("nonce")
    last_nonce = st.session_state.get("_chat_nonce")

    # Fallback for stale cached HTML that still sends ts only.
    # Use ts as the dedup key directly (equality, not ordering) so a counter
    # that resets to 0 on iframe reload doesn't get stuck behind a saved
    # large value from a previous submission.
    if incoming_nonce is None:
        incoming_nonce = f"ts:{result.get('ts')}"

    if text and incoming_nonce and incoming_nonce != last_nonce and not is_running:
        st.session_state._chat_nonce = incoming_nonce
        _submit_message(text)

if st.session_state.pending_prompt is not None:
    prompt = st.session_state.pending_prompt
    continue_session = not st.session_state.start_new_session
    resume_id = st.session_state.resume_session_id if st.session_state.pending_resume else None

    # Streaming UI: live-updated assistant bubble + collapsible activity log.
    bubble = st.empty()
    activity = st.expander("🔧 Tool calls · thinking · results", expanded=False)
    accumulated = ""
    error_lines: list[str] = []
    final_state: dict = {}

    def _render_bubble(content: str) -> None:
        body = content if content else "<i style='opacity:0.55'>thinking…</i>"
        bubble.markdown(
            f"<div class='chat-bubble-assistant'><b>Claude</b><br>{body}</div>",
            unsafe_allow_html=True,
        )

    _render_bubble("")

    try:
        for evt in run_claude_stream(
            prompt, working_dir, CLAUDE_TIMEOUT_SEC, continue_session, resume_id=resume_id,
        ):
            k = evt["kind"]
            if k == "text":
                accumulated += evt["text"]
                _render_bubble(accumulated)
            elif k == "tool_use":
                inp = json.dumps(evt.get("input") or {}, ensure_ascii=False)
                if len(inp) > 400:
                    inp = inp[:400] + "…"
                with activity:
                    st.markdown(f"🔧 **{evt.get('name','?')}** `{inp}`")
            elif k == "tool_result":
                snippet = (evt.get("content") or "")
                if len(snippet) > 600:
                    snippet = snippet[:600] + "…"
                prefix = "❌" if evt.get("is_error") else "✅"
                with activity:
                    st.code(f"{prefix} {snippet}", language=None)
            elif k == "thinking":
                with activity:
                    st.markdown(f"💭 _{(evt.get('text','') or '')[:600]}_")
            elif k == "error":
                error_lines.append(evt.get("text", ""))
            elif k == "done":
                final_state = evt
                break
    except FileNotFoundError:
        final_state = {"text": "`claude` CLI not found. Install Claude Code first.",
                       "code": -1, "elapsed": 0.0, "usage": None}
        error_lines.append(final_state["text"])
    except Exception as e:
        final_state = {"text": f"Unexpected error: {e}",
                       "code": -1, "elapsed": 0.0, "usage": None}
        error_lines.append(str(e))

    # Drop the temporary bubble — the persisted message will re-render on rerun.
    bubble.empty()

    st.session_state.start_new_session = False
    st.session_state.pending_resume = False

    code = final_state.get("code", -1)
    output = (final_state.get("text") or "").strip()
    elapsed = final_state.get("elapsed", 0.0)
    usage = final_state.get("usage")

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
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": output or "(no text response)",
                "ts": datetime.now().strftime("%H:%M:%S"),
                "meta": f"({elapsed:.1f}s){meta_extra}",
            }
        )
    else:
        msg = output or "\n".join(x for x in error_lines if x) or "(no output)"
        st.session_state.messages.append(
            {
                "role": "error",
                "content": msg,
                "ts": datetime.now().strftime("%H:%M:%S"),
                "meta": f"(exit {code}, {elapsed:.1f}s)" if elapsed else f"(exit {code})",
            }
        )
    st.session_state.pending_prompt = None
    st.session_state._scroll_bottom = True
    st.rerun()
