import os
import uuid
import requests
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

# -----------------------------
# Config
# -----------------------------
st.set_page_config(page_title="Chat + SQL Frontend", layout="wide")

API_URL = os.getenv("API_URL", "http://api:8000/chat")

CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "5"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "180"))
DEFAULT_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

MAX_PREVIEW_ROWS = int(os.getenv("MAX_PREVIEW_ROWS", "30"))

APP_TITLE = "SQL Agent Example"


# -----------------------------
# Helpers
# -----------------------------
def ensure_thread_id() -> None:
    if "thread_id" not in st.session_state or not st.session_state.thread_id:
        st.session_state.thread_id = str(uuid.uuid4())


def call_api(content: str, thread_id: str) -> dict:
    payload = {"content": content, "thread_id": thread_id}
    r = requests.post(API_URL, json=payload, timeout=DEFAULT_TIMEOUT)
    if not r.ok:
        raise requests.HTTPError(f"{r.status_code} {r.reason} - {r.text}", response=r)
    return r.json()


def extract_assistant_text(resp: dict) -> str:
    """
    STRICT: Use ONLY additional_kwargs.direct_answer.
    """
    additional = resp.get("additional_kwargs") or {}
    direct = additional.get("direct_answer")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    return "Done."


def scroll_to_bottom() -> None:
    components.html(
        """
        <script>
          const bottom = window.parent.document.getElementById("chat-bottom");
          if (bottom) bottom.scrollIntoView({behavior: "smooth", block: "end"});
        </script>
        """,
        height=0,
    )


def get_result_block(raw: dict) -> dict | None:
    """
    Returns additional_kwargs.result if present and shaped like:
    {sql, params, columns, rows, row_count, truncated}
    """
    if not isinstance(raw, dict):
        return None
    add = raw.get("additional_kwargs") or {}
    res = add.get("result")
    if isinstance(res, dict):
        return res
    return None


def render_result_popover(raw: dict) -> None:
    """
    Popover UI (no HTML) showing SQL, params, and a table preview.
    """
    res = get_result_block(raw)
    if not res:
        return

    sql = res.get("sql") or ""
    params = res.get("params") or []
    cols = res.get("columns") or []
    rows = res.get("rows") or []
    row_count = res.get("row_count")
    truncated = bool(res.get("truncated"))

    with st.popover("ⓘ Detalhes"):
        st.markdown("**SQL**")
        st.code(sql, language="sql")

        st.markdown("**Params**")
        st.json(params)

        if cols and isinstance(rows, list) and rows:
            st.markdown("**Preview**")
            preview = rows[:MAX_PREVIEW_ROWS]
            df = pd.DataFrame(preview, columns=cols)
            st.dataframe(df, use_container_width=True, hide_index=True)

            if isinstance(row_count, int) and row_count > len(preview):
                st.caption(f"Mostrando {len(preview)} de {row_count} linhas.")
            elif truncated:
                st.caption(f"Mostrando as primeiras {len(preview)} linhas (resultado truncado).")
        else:
            st.caption("Sem linhas para pré-visualização.")


# -----------------------------
# Session state init
# -----------------------------
ensure_thread_id()

if "history" not in st.session_state:
    st.session_state.history = []  # [{"role":"user"|"assistant","text":str,"raw":dict|None}]

if "waiting" not in st.session_state:
    st.session_state.waiting = False

if "pending_msg" not in st.session_state:
    st.session_state.pending_msg = None


# -----------------------------
# Sidebar (title + session)
# -----------------------------
with st.sidebar:
    st.title(APP_TITLE)

    st.header("Session")
    st.text_input("thread_id", key="thread_id", disabled=st.session_state.waiting)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("New thread", disabled=st.session_state.waiting):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.history = []
            st.session_state.waiting = False
            st.session_state.pending_msg = None
            st.rerun()
    with col2:
        if st.button("Clear chat", disabled=st.session_state.waiting):
            st.session_state.history = []
            st.session_state.waiting = False
            st.session_state.pending_msg = None
            st.rerun()

    st.divider()
    st.caption("API URL")
    st.code(API_URL, language="text")


# -----------------------------
# Main area (NO titles)
# -----------------------------

# Render history
for item in st.session_state.history:
    role = item["role"]
    text = item["text"]
    raw = item.get("raw")

    with st.chat_message(role):
        if role == "assistant":
            # Put answer + popover in the same message container
            cols = st.columns([1, 0.06])
            with cols[0]:
                st.markdown(text)
            with cols[1]:
                if get_result_block(raw):
                    render_result_popover(raw)
        else:
            st.markdown(text)

# Anchor at bottom for scroll
st.markdown('<div id="chat-bottom"></div>', unsafe_allow_html=True)


# If waiting, call API now
if st.session_state.waiting and st.session_state.pending_msg:
    try:
        with st.spinner("Thinking..."):
            resp = call_api(st.session_state.pending_msg, st.session_state.thread_id)

        assistant_text = extract_assistant_text(resp)
        st.session_state.history.append({"role": "assistant", "text": assistant_text, "raw": resp})

    except requests.HTTPError as e:
        st.session_state.history.append({"role": "assistant", "text": f"HTTP error: {e}", "raw": None})
    except requests.RequestException as e:
        st.session_state.history.append({"role": "assistant", "text": f"Request failed: {e}", "raw": None})
    finally:
        st.session_state.pending_msg = None
        st.session_state.waiting = False
        st.rerun()


# Chat input (disabled while waiting)
if st.session_state.waiting:
    st.chat_input("Thinking...", disabled=True, key="chat_input_disabled")
else:
    user_msg = st.chat_input("Ask something (e.g., Which good do we sell?)", key="chat_input_enabled")
    if user_msg:
        st.session_state.history.append({"role": "user", "text": user_msg, "raw": None})
        st.session_state.pending_msg = user_msg
        st.session_state.waiting = True
        st.rerun()

scroll_to_bottom()
