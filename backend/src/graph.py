import asyncio
import json
import logging
from datetime import datetime
from itertools import combinations

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from prompts import INSTRUCTIONS
from providers import Providers, direct_distance
from schemas import INDIA, upcoming_saturday
from state import PlannerState
from tools import make_tools

logger = logging.getLogger(__name__)
MAX_CALLS_PER_TURN = 20
MAX_ROUNDS = 11
WRITE_TOOLS = {"update_preferences", "display_itinerary", "confirm_itinerary"}


class TurnState(MessagesState):
    rounds: int
    calls: int
    acknowledged: bool
    displayed: bool
    preferences_recorded: bool


def build_graph(planner: PlannerState, providers: Providers, chat_model):
    tools = make_tools(planner, providers)
    llm = chat_model.bind_tools(tools)
    llm_with_preferences = chat_model.bind_tools([tool for tool in tools if tool.name != "update_preferences"])
    tools_node = ToolNode(tools, handle_tool_errors=True)

    async def decide(state: TurnState):
        now = datetime.now(INDIA)
        current = planner.itinerary
        context = {
            "current_local_time": now.isoformat(timespec="minutes"),
            "default_saturday": upcoming_saturday(now.date()).isoformat(),
            "preferences": planner.preferences.model_dump(mode="json", exclude_none=True),
            "last_displayed_itinerary": current.model_dump(mode="json") if current else None,
            "revision_needed": planner.revision_needed,
        }
        venues = [
            place for place in planner.places.values()
            if place.get("_city") == planner.city_key and place.get("_reference") and place.get("location")
        ][-12:]
        if venues:
            distances = sorted(
                (round(direct_distance(a["location"], b["location"])), a["_reference"], b["_reference"])
                for a, b in combinations(venues, 2)
            )
            context["nearby_candidate_pairs"] = [
                {"venues": [a, b], "straight_line_metres": distance}
                for distance, a, b in distances[:15] if distance <= 2500
            ]
        messages = [SystemMessage(content=f"{INSTRUCTIONS}\nServer session state: {json.dumps(context)}")]
        history = state["messages"]
        # LiveKit supplies prior user/assistant turns. Keep tool exchanges from this
        # invocation intact even when limiting the amount of earlier conversation.
        split = next(
            (i for i, msg in enumerate(history) if isinstance(msg, ToolMessage)
             or (isinstance(msg, AIMessage) and msg.tool_calls)),
            len(history),
        )
        messages.extend(history[:split][-24:])
        messages.extend(history[split:])
        if state.get("rounds", 0) >= MAX_ROUNDS or state.get("calls", 0) >= MAX_CALLS_PER_TURN or planner.calls >= 90:
            messages.append(SystemMessage(
                content="Tool execution cannot continue in this turn. Give a concise, honest status based on results already in hand."
            ))
            model = chat_model
        else:
            model = llm_with_preferences if state.get("preferences_recorded") else llm

        try:
            async with asyncio.timeout(28):
                reply = await model.ainvoke(messages)
        except Exception as error:
            logger.warning("Planner model request failed: %s (status %s)", type(error).__name__, getattr(error, "status_code", None))
            reply = AIMessage(content="I couldn't reach the planning service just now. Please try again in a moment.")
        if not reply.tool_calls:
            text = reply.text.strip()
            if not text:
                text = "I couldn't finish that plan in this turn. What would you like me to adjust or check next?"
            get_stream_writer()(text)
        return {"messages": [reply], "rounds": state.get("rounds", 0) + 1}

    async def act(state: TurnState):
        calls = state["messages"][-1].tool_calls
        room_left = MAX_CALLS_PER_TURN - state.get("calls", 0)
        names = {call["name"] for call in calls}
        if len(calls) > room_left or (len(calls) > 1 and names & WRITE_TOOLS):
            error = (
                "Too many calls. Try fewer tools or give the user a status update." if len(calls) > room_left
                else "Run preference, display and confirmation tools by themselves. Then use dependent results."
            )
            return {
                "messages": [ToolMessage(content=error, tool_call_id=call["id"], name=call["name"]) for call in calls],
            }
        acknowledged = state.get("acknowledged", False)
        if not acknowledged and names & {"search_places", "get_weather", "calculate_route"}:
            get_stream_writer()("I'll check the options for your Saturday. ")
            acknowledged = True
        previous = planner.itinerary
        result = await tools_node.ainvoke(state)
        displayed = planner.itinerary is not previous
        if displayed:
            reply = (
                "Your Saturday plan is confirmed."
                if planner.itinerary.status == "accepted"
                else "Your Saturday plan is ready. I’ve added it to the chat."
            )
            get_stream_writer()(reply)
            result["messages"].append(AIMessage(content=reply))
        return {
            **result, "calls": state.get("calls", 0) + len(calls),
            "acknowledged": acknowledged, "displayed": displayed,
        }

    graph = StateGraph(TurnState)
    graph.add_node("agent", decide)
    graph.add_node("tools", act)
    graph.add_conditional_edges(START, tools_condition, {"tools": "tools", "__end__": "agent"})
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", "__end__": END})
    graph.add_conditional_edges("tools", lambda state: END if state.get("displayed") else "agent")
    return graph.compile()
