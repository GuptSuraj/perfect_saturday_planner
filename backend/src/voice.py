import asyncio
import json
import logging
import os
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from livekit.agents import Agent, RunContext, TurnHandlingOptions, function_tool
from livekit.agents.llm import ChatContext
from livekit.plugins.openai import realtime
from openai.types.realtime.audio_transcription import AudioTranscription
from openai.types.realtime.realtime_audio_input_turn_detection import SemanticVad

from state import PlannerState

logger = logging.getLogger(__name__)

VOICE_INSTRUCTIONS = """
You're Mira, the voice of Perfect Saturday, a weekend planner for Indian cities. Sound like
a friend who knows how to make a good plan. Speak in the user's language when you can.
Keep a warm, unhurried delivery. Use contractions in English. One or two short sentences
is usually enough. Pick up on something specific the user just said. Avoid stock praise,
lists, scripted filler, repetitive affirmations, and narrating your process. If the user
is still talking, let them finish. If they interrupt or change their mind, follow the new
thread without repeating the part they've already heard.

Help them settle on a Saturday. Ask one natural question at a time when something important
is missing. City, total budget in INR, and how much time they have are the essentials.
You can ask about two closely related details together if they naturally fit. Use details
they've already mentioned. Don't make them answer a questionnaire for optional details.

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
"""


class VoicePlanner(Agent):
    def __init__(self, *, graph, planner: PlannerState, chat_ctx: ChatContext) -> None:
        context = {
            "preferences": planner.preferences.model_dump(mode="json", exclude_none=True),
            "itinerary": planner.itinerary.title if planner.itinerary else None,
        }
        super().__init__(
            instructions=f"{VOICE_INSTRUCTIONS}\nPreferences and current plan: {json.dumps(context)}",
            chat_ctx=chat_ctx,
            stt=None,
            llm=realtime.RealtimeModel(
                model=os.getenv("OPENAI_REALTIME_MODEL") or "gpt-realtime",
                voice=os.getenv("OPENAI_REALTIME_VOICE") or "marin",
                input_audio_transcription=AudioTranscription(model="gpt-4o-mini-transcribe"),
                input_audio_noise_reduction="near_field",
                turn_detection=SemanticVad(
                    type="semantic_vad", eagerness="medium",
                    create_response=True, interrupt_response=True,
                ),
            ),
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
    ) -> dict:
        """Build, revise, check, or confirm a real Saturday itinerary in the background.

        Args:
            request: Latest user request and their relevant city, budget, time and preferences.
            action: create, revise, check, or confirm. Confirm only after explicit user approval.
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
            # Let LiveKit give the conversational model the floor while the graph works.
            if action in {"create", "revise"}:
                await context.update(
                    "Research has started for this request. The user can keep talking.",
                    template="{message}",
                    silent=True,
                )
            async with asyncio.timeout(100):
                return await self._graph.ainvoke({"messages": history}, {"recursion_limit": 28})

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
            return {"status": "unavailable", "summary": "The search didn't finish. Ask if they'd like to try again."}
        finally:
            if self._work is work:
                self._work = None

        plan = self._planner.itinerary
        reply = result["messages"][-1]
        return {
            "status": "complete",
            "displayed": plan is not None and plan is not previous_plan and not self._planner.revision_needed,
            "accepted": plan is not None and plan.status == "accepted" and not self._planner.revision_needed,
            "summary": reply.text if isinstance(reply, AIMessage) else "",
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
