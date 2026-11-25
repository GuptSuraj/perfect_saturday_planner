import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from graph import build_graph
from schemas import Preferences
from state import PlannerState
from tools import make_tools, save_preferences


COMPLETE = {
    "city": "Bengaluru", "budget": 2000, "available_time": 240,
    "mood": "relaxed", "interests": ["books", "cafes"], "constraints": [],
}


@pytest.mark.parametrize("missing", list(COMPLETE))
def test_each_input_is_required_but_explicit_empty_lists_are_valid(missing):
    values = {key: value for key, value in COMPLETE.items() if key != missing}
    assert Preferences(**values).missing_planning_inputs() == [missing]


def test_available_time_alias_preserves_existing_internal_schema_and_zero_budget():
    prefs = Preferences(**{**COMPLETE, "budget": 0, "interests": []})
    assert not prefs.missing_planning_inputs()
    assert prefs.available_minutes == 240
    assert "available_time" not in prefs.model_dump()


async def test_partial_updates_do_not_assume_missing_lists_and_revisions_preserve_inputs():
    state = PlannerState(publish=AsyncMock(return_value=True))
    await save_preferences(state, Preferences(city="Bengaluru"))
    await save_preferences(state, Preferences(budget=0, available_time=120, mood="flexible"))
    assert state.preferences.missing_planning_inputs() == ["interests", "constraints"]
    await save_preferences(state, Preferences(interests=[]))
    assert state.preferences.missing_planning_inputs() == ["constraints"]
    await save_preferences(state, Preferences(constraints=[]))
    assert not state.preferences.missing_planning_inputs()
    await save_preferences(state, Preferences(budget=500), request_revision=True)
    assert not state.preferences.missing_planning_inputs()
    assert state.preferences.city == "Bengaluru"
    assert state.preferences.available_minutes == 120
    assert state.preferences.mood == "flexible"
    await save_preferences(state, Preferences(mood=None))
    assert state.preferences.missing_planning_inputs() == ["mood"]


@pytest.mark.parametrize("tool_name,args", [
    ("search_places", {"query": "cafe"}),
    ("get_place_details", {"place_id": "venue_1"}),
    ("get_weather", {}),
    ("calculate_route", {"destination_place_id": "venue_1"}),
    *[(name, {"proposal": {"title": "Afternoon", "explanation": "Take a break.", "activities": [{
        "place_id": "venue_1", "category": "food", "outdoors": False,
        "duration_minutes": 60, "description": "Have coffee.", "reason": "A quiet break.",
    }]}}) for name in ("estimate_cost", "validate_itinerary", "display_itinerary")],
])
async def test_research_and_planning_tools_are_blocked_until_intake_complete(tool_name, args):
    providers = MagicMock()
    state = PlannerState(publish=AsyncMock(return_value=True), preferences=Preferences(
        city="Bengaluru", budget=2000, available_minutes=240,
    ))
    tools = {tool.name: tool for tool in make_tools(state, providers)}
    result = await tools[tool_name].ainvoke(args)
    assert result["status"] == "needs_input"
    assert result["missing_inputs"] == ["mood", "interests", "constraints"]
    assert "maps_failure" not in result
    assert providers.mock_calls == []
    assert state.itinerary is None


async def test_graph_asks_for_missing_inputs_then_researches_using_all_preferences():
    state = PlannerState(publish=AsyncMock(return_value=True))
    providers = MagicMock()
    providers.places_search = AsyncMock(return_value=[])
    stage = 0

    class IntakeModel:
        def bind_tools(self, tools):
            names = {tool.name for tool in tools}
            return SimpleNamespace(ainvoke=lambda messages: respond(messages, names))

    async def respond(messages, allowed):
        nonlocal stage
        stage += 1
        if stage == 1:
            assert allowed == {"update_preferences"}
            return AIMessage(content="", tool_calls=[{
                "name": "update_preferences", "args": {"patch": {
                    "city": "Bengaluru", "budget": 2000, "available_time": 240,
                }}, "id": "basics",
            }])
        if stage == 2:
            assert allowed == {"update_preferences"}
            providers.places_search.assert_not_awaited()
            return AIMessage(content="What mood, interests and constraints should I plan around?")
        if stage == 3:
            assert allowed == {"update_preferences"}
            return AIMessage(content="", tool_calls=[{
                "name": "update_preferences", "args": {"patch": {
                    "mood": "relaxed", "interests": ["books", "cafes"], "constraints": ["indoors"],
                    "environment": "indoors",
                }}, "id": "details",
            }])
        if stage == 4:
            assert "search_places" in allowed
            assert not state.preferences.missing_planning_inputs()
            context = messages[0].content
            assert all(word in context for word in ("Bengaluru", "2000", "240", "relaxed", "books", "indoors"))
            return AIMessage(content="", tool_calls=[{
                "name": "search_places", "args": {"query": "bookstores"}, "id": "research",
            }])
        return AIMessage(content="No matching bookstores were found; let's try another area.")

    graph = build_graph(state, providers, IntakeModel())
    await graph.ainvoke({"messages": [HumanMessage(content="Bengaluru, ₹2000, four hours.")]})
    providers.places_search.assert_not_awaited()
    await graph.ainvoke({"messages": [HumanMessage(content="Relaxed, books and cafes, indoors only.")]})
    providers.places_search.assert_awaited_once()
    assert state.itinerary is None


async def test_initial_voice_greeting_uses_realtime_and_does_not_switch_to_pipeline_tts(monkeypatch):
    # Avoid reading the user's real credentials while importing the entry point.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    import agent
    import asyncio

    session = MagicMock()
    session.start = AsyncMock()
    session.interrupt = AsyncMock()
    monkeypatch.setattr(agent, "AgentSession", MagicMock(return_value=session))
    monkeypatch.setattr(agent, "build_graph", MagicMock())
    monkeypatch.setattr(agent, "planning_model", MagicMock())
    monkeypatch.setattr(agent, "inference", SimpleNamespace(STT=MagicMock(), TTS=MagicMock()))
    monkeypatch.setattr(agent.langchain, "LLMAdapter", MagicMock())
    ready = asyncio.Event()
    ready.set()
    voice_factory = MagicMock(return_value=SimpleNamespace(ready=ready))
    monkeypatch.setattr(agent, "VoicePlanner", voice_factory)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test")
    ctx = MagicMock()
    ctx.job.metadata = json.dumps({"identity": "guest-test"})
    ctx.connect = AsyncMock()
    ctx.wait_for_participant = AsyncMock()
    ctx.room.local_participant.set_attributes = AsyncMock()
    callbacks = []
    ctx.add_shutdown_callback.side_effect = callbacks.append
    methods = {}

    def register(name):
        def save(function):
            methods[name] = function
            return function
        return save

    ctx.room.local_participant.register_rpc_method.side_effect = register
    try:
        await agent.entrypoint(ctx)
        for payload in ("on", "off", "on"):
            assert await methods["planner.voice"](SimpleNamespace(caller_identity="guest-test", payload=payload)) == "ok"
        voice_factory.assert_called_once()
        session.generate_reply.assert_called_once_with(
            tool_choice="none", instructions=f"Greet the user with exactly this text: {agent.WELCOME}",
        )
        session.say.assert_not_called()
        assert all(word in agent.WELCOME for word in ("city", "budget", "time", "mood", "interests", "constraints"))
    finally:
        for callback in callbacks:
            await callback()
