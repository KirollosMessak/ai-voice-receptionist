import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from receptionist.agent import build_agent, calendar_text
from receptionist.tools import make_tools
from tests.conftest import NOW, TODAY


class ScriptedLLM(GenericFakeChatModel):
    """Replays a fixed list of AI messages (including tool calls). No network."""

    def bind_tools(self, tools, **kwargs):
        return self


def tool(conn, name):
    return {t.name: t for t in make_tools(conn, lambda: NOW)}[name]


def test_booking_tool_refuses_without_caller_confirmation(conn):
    args = {"doctor_id": "D1", "day": "2026-10-11", "time": "14:00", "patient_name": "Mona", "phone": "01234567891"}
    assert "Not booked" in tool(conn, "book_appointment").invoke({**args, "caller_confirmed": False})
    booked = json.loads(tool(conn, "book_appointment").invoke({**args, "caller_confirmed": True}))
    assert booked["booked"] is True and booked["weekday"] == "Sunday"


def test_tools_report_rule_violations_as_text_not_crashes(conn):
    out = tool(conn, "book_appointment").invoke(
        {
            "doctor_id": "D1",
            "day": "2026-10-16",
            "time": "11:00",
            "patient_name": "Mona",
            "phone": "01234567891",
            "caller_confirmed": True,
        }
    )
    assert out.startswith("Not booked")
    assert "Error" in tool(conn, "find_available_slots").invoke({"doctor_id": "D9", "day": "2026-10-11"})


def test_agent_loop_calls_tools_and_answers(conn):
    llm = ScriptedLLM(
        messages=iter(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {"name": "find_available_slots", "id": "c1", "args": {"doctor_id": "D1", "day": "2026-10-11"}}
                    ],
                ),
                AIMessage("Dr. Sara has 10:30 in the morning free on Sunday."),
            ]
        )
    )
    agent = build_agent(llm, conn, InMemorySaver(), now_fn=lambda: NOW)
    result = agent.invoke({"messages": [HumanMessage("dentist on Sunday?")]}, {"configurable": {"thread_id": "t"}})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert '"10:30"' in tool_msgs[0].content
    assert result["messages"][-1].content.startswith("Dr. Sara")


def test_calendar_gives_the_model_exact_dates():
    cal = calendar_text(TODAY, 3)
    assert "Saturday 2026-10-10 today" in cal and "Sunday 2026-10-11 tomorrow" in cal


def test_output_guard_catches_false_booking_claims_and_garbled_text():
    from receptionist.agent import clean_spoken, reply_problem

    assert reply_problem("تمام، حجزت مع د. سارة الساعة 2", []) is not None
    assert reply_problem("Your appointment is confirmed for Sunday.", []) is not None
    assert reply_problem("Shall I confirm Dr. Sara on Sunday at 2 PM?", []) is None
    booked = [ToolMessage('{"booked": true, "appointment_id": 5}', name="book_appointment", tool_call_id="x")]
    assert reply_problem("Your appointment is confirmed for Sunday.", booked) is None
    assert reply_problem("مرحبا‏‏‏‏", []) is not None
    assert clean_spoken("Hello‏ there\n\nfriend") == "Hello there friend"


def test_guard_makes_the_model_rewrite_a_false_claim(conn):
    llm = ScriptedLLM(
        messages=iter(
            [
                AIMessage("Done, your appointment is confirmed!"),  # nothing was booked: must be rewritten
                AIMessage("I can book Dr. Sara on Sunday at 2 PM. What's your name and mobile number?"),
            ]
        )
    )
    agent = build_agent(llm, conn, InMemorySaver(), now_fn=lambda: NOW)
    result = agent.invoke({"messages": [HumanMessage("Sunday 2 PM please")]}, {"configurable": {"thread_id": "g"}})
    assert result["messages"][-1].content.startswith("I can book")


def test_two_bad_drafts_fall_back_to_a_confirmation_built_from_the_booking(conn):
    leak = "We need to respond according to rules... " * 20  # reasoning leak, far too long to speak
    llm = ScriptedLLM(
        messages=iter(
            [
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "book_appointment",
                            "id": "b1",
                            "args": {
                                "doctor_id": "D1",
                                "day": "2026-10-11",
                                "time": "14:00",
                                "patient_name": "Mona Adel",
                                "phone": "01234567891",
                                "caller_confirmed": True,
                            },
                        }
                    ],
                ),
                AIMessage(leak),
                AIMessage(leak),
            ]
        )
    )
    agent = build_agent(llm, conn, InMemorySaver(), now_fn=lambda: NOW)
    result = agent.invoke({"messages": [HumanMessage("yes confirm")]}, {"configurable": {"thread_id": "f"}})
    reply = result["messages"][-1].content
    assert "confirmed" in reply and "Dr. Sara Nabil" in reply and "2:00 PM" in reply
