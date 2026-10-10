"""Voice demo: press the mic, speak (Arabic or English), hear the receptionist answer.

Run: streamlit run app.py
"""

import hashlib
import json
import sys
import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).parent / "src"))

from receptionist import clinic, voice  # noqa: E402
from receptionist.agent import build_agent  # noqa: E402
from receptionist.llm import get_llm, has_api_key  # noqa: E402

st.set_page_config(page_title="AI Voice Receptionist", page_icon="🎙️", layout="wide")


@st.cache_resource
def get_conn():
    if not clinic.DEFAULT_DB.exists():
        clinic.init_db()
    return clinic.connect()


@st.cache_resource
def get_agent(api_key: str | None):
    return build_agent(get_llm(api_key), get_conn(), InMemorySaver())


def browser_speak(text: str, lang: str) -> None:
    """Fallback voice: the browser's built-in speech synthesis."""
    components.html(
        f"""<script>
        const u = new SpeechSynthesisUtterance({json.dumps(text)});
        u.lang = "{"ar-EG" if lang == "ar" else "en-US"}";
        window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
        </script>""",
        height=0,
    )


def handle(agent, text: str):
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    before = len(agent.get_state(config).values.get("messages", []))
    result = agent.invoke({"messages": [HumanMessage(text)]}, config)
    new = result["messages"][before:]
    tools = [
        f"🔧 `{tc['name']}` {json.dumps(tc['args'], ensure_ascii=False)}"
        for m in new
        for tc in (getattr(m, "tool_calls", None) or [])
    ]
    tools += [f"↳ {m.content[:160]}" for m in new if isinstance(m, ToolMessage)]
    reply = result["messages"][-1].content
    audio = voice.synthesize(reply)
    st.session_state.chat += [
        {"role": "user", "content": text},
        {
            "role": "assistant",
            "content": reply,
            "tools": tools,
            "audio": audio,
            "lang": voice.language_of(reply),
            "fresh": True,
        },
    ]


# ---------------- Sidebar: clinic ----------------
conn = get_conn()
with st.sidebar:
    st.header("🏥 Nile Care Clinic")
    st.caption("Demo clinic. All data is fictional.")
    key = None
    if not has_api_key():
        key = st.text_input("Groq API key", type="password", help="Free at console.groq.com/keys")
    st.subheader("Doctors")
    st.dataframe(
        [
            {
                "doctor": d.name,
                "specialty": d.specialty,
                "days": ", ".join(x[:3] for x in d.days),
                "hours": f"{d.start:%H:%M}-{d.end:%H:%M}",
                "EGP": d.fee_egp,
            }
            for d in clinic.DOCTORS
        ],
        hide_index=True,
    )
    new_call, reset = st.columns(2)
    if new_call.button("📞 New call") or "thread_id" not in st.session_state:
        st.session_state.update(thread_id=uuid.uuid4().hex, chat=[], last_audio=None)
    if reset.button("Reset data"):
        clinic.init_db()
        st.session_state.update(thread_id=uuid.uuid4().hex, chat=[], last_audio=None)
        st.rerun()
    with st.expander("Booked appointments (staff view)"):
        st.dataframe(
            [
                {
                    "id": a["id"],
                    "doctor": a["doctor_id"],
                    "start": a["start"].replace("T", " "),
                    "patient": a["patient_name"],
                    "status": a["status"],
                }
                for a in clinic.all_appointments(conn)
                if a["patient_name"] != "Existing patient"
            ],
            hide_index=True,
        )

st.title("🎙️ AI Voice Receptionist")
st.caption(
    "Speak or type in **Arabic or English**: book, check or cancel appointments, ask about prices, "
    "hours or insurance. Try: *عايز أحجز مع دكتورة أسنان يوم الأحد* or *Do you accept AXA insurance?*"
)

if not (has_api_key() or key):
    st.info("Add a Groq API key in the sidebar to start (free at console.groq.com/keys).")
    st.stop()
agent = get_agent(key)

for msg in st.session_state.chat:
    with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🎙️"):
        st.markdown(msg["content"])
        if msg.get("tools"):
            with st.expander("What the agent did"):
                st.markdown("\n\n".join(msg["tools"]))
        if msg["role"] == "assistant" and msg.pop("fresh", False):  # speak each reply once
            if msg["audio"]:
                st.audio(msg["audio"], format="audio/wav", autoplay=True)
            else:
                browser_speak(msg["content"], msg["lang"])

spoken = st.audio_input("🎤 Tap to speak")
typed = st.chat_input("...or type here / اكتب هنا")

if spoken is not None:
    data = spoken.getvalue()
    digest = hashlib.md5(data).hexdigest()
    if digest != st.session_state.last_audio:  # Streamlit reruns keep the last recording
        st.session_state.last_audio = digest
        with st.spinner("Listening..."):
            text = voice.transcribe(data)
        if text:
            with st.spinner("Thinking..."):
                handle(agent, text)
            st.rerun()
if typed:
    with st.spinner("Thinking..."):
        handle(agent, typed)
    st.rerun()
