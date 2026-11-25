import asyncio
import json
import logging
import os
from typing import Literal
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from livekit.agents import Agent, RunContext, TurnHandlingOptions, function_tool
from livekit.agents.llm import ChatContext
from livekit.plugins.openai import realtime
from openai.types.realtime.audio_transcription import AudioTranscription
from openai.types.realtime.realtime_audio_input_turn_detection import SemanticVad

from language import LANGUAGE_INSTRUCTIONS, localized
from schemas import Language, Model, Preferences, ShortText
from state import PlannerState
from tools import save_preferences

logger = logging.getLogger(__name__)

VOICE_INSTRUCTIONS = """
You're Mira, the voice of Perfect Saturday, a weekend planner for Indian cities. Sound like
a friend who knows how to make a good plan.
Keep a warm, unhurried delivery. Use contractions in English. One or two short sentences
is usually enough. Pick up on something specific the user just said. Avoid stock praise,
lists, scripted filler, repetitive affirmations, and narrating your process. If the user
is still talking, let them finish. If they interrupt or change their mind, follow the new
thread without repeating the part they've already heard.

Only ask for missing essentials: city, total INR budget, and available time. Ask for
missing budget and time together, even when the city is already known. Understand
"all day", "the whole day", or "पूरा दिन" as eight hours, and "2K" as 2,000 rupees. Never ask for
group size, start time, mood, transport, or area as a prerequisite. Assume one person
and pick a suitable start, around 10 am for a full day. Use any preferences they offer,
including interests, pace, travel mode, food preferences, and hard restrictions. Keep
preferences the user already gave unless they change them.
Set the dietary field for any vegetarian food request, including shakahari or शाकाहारी.
Pick vegetarian_options except when a pure-veg kitchen is expressly needed. Reflect food
requirements in the searches too. Save required indoor and wheelchair access settings
in their dedicated fields. When speaking Hindi, use Mira's feminine first-person forms.
Once the essentials are clear, start planning in that turn without another question.

Chat directly for greetings and preference shifts before they're ready to plan. Call
plan_saturday to build a real itinerary as soon as you have enough details, or when they
ask for real suggestions, changes, a check on the displayed plan, or explicitly accept it.
Use the user's latest terms when you describe the task and carry over relevant earlier
details. For a genuine correction while research is running, submit the corrected request;
it replaces the old one. Don't resubmit for a backchannel or a question you can answer.
Say one short, specific line before starting research, then let the user talk. When
the tool reports that research has started, avoid another acknowledgement. Don't
predict the wait. If they ask what's happening, briefly say what you're checking.

Use only real venue details supplied by the planner. Do not invent places, times, costs,
or availability. The tool's displayed and accepted flags are authoritative. Only say
something is on screen or accepted when the tool reports it. When a plan is ready, mention
one thing that fits what they wanted; the itinerary can speak for the rest. Invite a change
when it makes sense. If the user approves it, close warmly. Do not offer bookings or payments.
If real places could not be checked, offer to retry or build a rough plan without
verified venues. An explicit request for that rough plan is a new create or revise action,
not approval of the itinerary. Pass allow_unverified_plan=true and omit searches only
when the user expressly agrees to the fallback or asks for it.
"""


class VenueSearch(Model):
    query: ShortText
    locality: ShortText | None = None


class VoicePlanner(Agent):
    def __init__(self, *, graph, planner: PlannerState, chat_ctx: ChatContext) -> None:
        context = {
            "preferences": planner.preferences.model_dump(mode="json", exclude_none=True),
            "itinerary": planner.itinerary.title if planner.itinerary else None,
        }
        model = realtime.RealtimeModel(
            model=os.getenv("OPENAI_REALTIME_MODEL") or "gpt-realtime",
            voice=os.getenv("OPENAI_REALTIME_VOICE") or "marin",
            input_audio_transcription=AudioTranscription(
                model="gpt-transcribe", languages=["en", "hi"],
                prompt="Saturday planning in Indian English and Hindi. Preserve place names and rupee amounts. Do not translate.",
            ),
            input_audio_noise_reduction="near_field",
            turn_detection=SemanticVad(
                type="semantic_vad", eagerness="medium",
                create_response=True, interrupt_response=True,
            ),
        )
        super().__init__(
            instructions=f"{VOICE_INSTRUCTIONS}\n{LANGUAGE_INSTRUCTIONS}\nPreferences and current plan: {json.dumps(context)}",
            chat_ctx=chat_ctx,
            stt=None,
            llm=model,
            turn_handling=TurnHandlingOptions(turn_detection="realtime_llm"),
        )
        self.ready = asyncio.Event()
        self._graph = graph
        self._planner = planner
        self._work: asyncio.Task | None = None

    async def on_enter(self) -> None:
        self.ready.set()

    @function_tool()
    async def plan_saturday(
        self, context: RunContext, request: str, action: Literal["create", "revise", "check", "confirm"],
        language: Language,
        preferences: Preferences | None = None,
        searches: list[VenueSearch] | None = None,
    ) -> dict:
        """Build, revise, check, or confirm a real Saturday itinerary in the background.

        Args:
            request: Latest user request and their relevant city, budget, time and preferences.
            action: create, revise, check, or confirm. Confirm only after explicit user approval.
            language: Use hi for a Hindi or Hinglish planning request, en for English.
            preferences: Known city, total INR budget, and available minutes (480 for all day).
                Include other explicitly stated preferences in their specific fields.
                Set dietary to vegetarian_options for vegetarian or shakahari food.
                Leave optional defaults unset.
            searches: For a new plan, give one or two short venue searches, covering distinct
                needs in the SAME named neighbourhood or landmark area. A city is never a
                locality. Pick a compact area yourself unless the user gave one. For food
                and history in Hyderabad, use "historic sites" and "affordable restaurants",
                with "Old City around Charminar" as the locality for both. When the user
                asks for vegetarian food, search for affordable vegetarian restaurants.
                For a change that needs no new venues, omit searches.
        """
        if not request.strip():
            return {"status": "needs_clarification"}

        previous_work = self._work
        if previous_work and not previous_work.done() and action in {"check", "confirm"}:
            return {"status": "in_progress", "summary": "The current itinerary request is still running."}
        # Build from the actual conversation as well as the spoken model's task summary.
        history = [
            (HumanMessage if item.role == "user" else AIMessage)(content=item.text_content)
            for item in self.session.history.messages()[-16:]
            if item.role in {"user", "assistant"} and item.text_content
        ]
        history.append(HumanMessage(content=f"Latest planning request ({action}): {request[:1600]}"))

        async def run():
            if previous_work and not previous_work.done():
                previous_work.cancel()
                await asyncio.gather(previous_work, return_exceptions=True)
            if preferences is not None:
                saved = await save_preferences(self._planner, preferences, action == "revise")
                if saved["status"] != "ok":
                    return {"messages": [AIMessage(content=saved["error"])]}
                await self._planner.record_step("update_preferences", "success", "Used the preferences you shared.")
            prefs = self._planner.preferences
            preferences_ready = prefs.city is not None and prefs.budget is not None and prefs.available_minutes is not None
            if preferences_ready and action in {"create", "revise"} and searches:
                history.append(AIMessage(content="", tool_calls=[
                    {"name": "search_places", "args": search.model_dump(exclude_none=True), "id": f"call_{uuid4().hex}"}
                    for search in searches[:2]
                ]))
            # Let LiveKit give the conversational model the floor while the graph works.
            if action in {"create", "revise"}:
                await context.update(
                    "Research has started for this request. The user can keep talking.",
                    template="{message}",
                    silent=True,
                )
            async with asyncio.timeout(55):
                return await self._graph.ainvoke({
                    "messages": history,
                    "reply_language": language,
                    "preferences_recorded": preferences_ready and preferences is not None,
                }, {"recursion_limit": 28})

        work = asyncio.create_task(run(), name="saturday-voice-research")
        self._work = work
        previous_plan = self._planner.itinerary
        try:
            result = await work
        except asyncio.CancelledError:
            if self._work is not work:
                return {"status": "superseded", "summary": "A newer request is being handled. Do not announce this result."}
            raise
        except Exception as error:
            logger.warning("Voice planning request failed: %s", type(error).__name__)
            return {"status": "unavailable", "summary": localized(
                language, "The search didn’t finish. Would you like me to try again?",
                "खोज पूरी नहीं हो सकी। क्या मैं फिर कोशिश करूँ?",
            )}
        finally:
            if self._work is work:
                self._work = None

        plan = self._planner.itinerary
        reply = result["messages"][-1]
        displayed = plan is not None and plan is not previous_plan and not self._planner.revision_needed
        summary = reply.text if isinstance(reply, AIMessage) else ""
        prefs = self._planner.preferences
        if (
            action in {"create", "revise"} and not displayed and not self._planner.last_places_failure
            and prefs.city is not None and prefs.budget is not None and prefs.available_minutes is not None
        ):
            summary = localized(language, "I couldn’t verify a plan that fit just yet. I can try a tighter area.",
                                "अभी आपकी ज़रूरतों के मुताबिक योजना की पुष्टि नहीं हो सकी। मैं किसी छोटे इलाके में विकल्प देख सकती हूँ।")
        return {
            "status": "complete",
            "displayed": displayed,
            "accepted": plan is not None and plan.status == "accepted" and not self._planner.revision_needed,
            "summary": summary,
            "current_plan": (
                {
                    "title": plan.title,
                    "stops": [stop.name for stop in plan.activities],
                    "max_estimated_cost_inr": plan.estimated_cost_max,
                    "duration_minutes": plan.duration_minutes,
                }
                if plan is not None and not self._planner.revision_needed else None
            ),
        }
