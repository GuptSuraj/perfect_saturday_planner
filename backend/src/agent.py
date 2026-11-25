import asyncio
import json
import logging
import os
from time import time

import httpx
from dotenv import load_dotenv
from livekit.agents import (
    Agent, AgentServer, AgentSession, JobContext, JobProcess, TurnHandlingOptions,
    cli, inference, room_io,
)
from livekit.plugins import langchain, silero

from graph import build_graph
from model import planning_model
from providers import Providers
from state import PlannerState
from voice import VoicePlanner

load_dotenv(".env.local")
load_dotenv()

logger = logging.getLogger("perfect-saturday")
server = AgentServer()
MIRA_GREETING = "Hi, I’m Mira. I’m here to help you plan your Saturday."


def prewarm(process: JobProcess):
    process.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="perfect-saturday")
async def entrypoint(ctx: JobContext):
    try:
        identity = json.loads(ctx.job.metadata)["identity"]
        if not isinstance(identity, str) or not identity.startswith("guest-"):
            raise ValueError("Invalid guest identity")
    except (TypeError, ValueError, KeyError):
        ctx.shutdown(reason="Missing planner session metadata")
        return

    await ctx.connect()
    try:
        async with asyncio.timeout(20):
            await ctx.wait_for_participant(identity=identity)
    except TimeoutError:
        ctx.shutdown(reason="Planner guest did not join")
        return

    local = ctx.room.local_participant
    http = httpx.AsyncClient(timeout=httpx.Timeout(6, connect=4))
    ctx.add_shutdown_callback(http.aclose)
    chat_updates: set[asyncio.Task] = set()

    async def publish(channel: str, data: dict) -> bool:
        try:
            payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            if channel == "itinerary":
                payload = json.dumps({**data, "chat_timestamp": time() * 1000}, ensure_ascii=False)
                acknowledgement = await local.perform_rpc(
                    destination_identity=identity, method="planner.itinerary",
                    payload=payload, response_timeout=3, max_round_trip_latency=3,
                )
                return acknowledgement == f"{data['itinerary_id']}:{data['version']}:{data['status']}"
            async with asyncio.timeout(2):
                await local.send_text(payload, topic=f"planner.{channel}", destination_identities=[identity])
            return True
        except Exception as error:
            logger.info("Planner transport failed (%s, %s)", channel, type(error).__name__)
            return False

    planner = PlannerState(publish)
    graph = build_graph(planner, Providers(http), planning_model(http))
    session = AgentSession(
        llm=langchain.LLMAdapter(graph=graph, config={"recursion_limit": 28}, stream_mode="custom"),
        stt=inference.STT(model="deepgram/nova-3", language="en-IN"),
        tts=inference.TTS(model="cartesia/sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(turn_detection="vad"),
        aec_warmup_duration=0.25,
    )
    turns = 0
    voice_agent: VoicePlanner | None = None
    voice_intro_sent = False
    voice_lock = asyncio.Lock()

    def publish_message(message_id: str, role: str, text: str, started_at: float) -> None:
        task = asyncio.create_task(publish("chat", {
            "id": message_id, "role": role, "text": text, "timestamp": started_at * 1000,
        }))
        chat_updates.add(task)
        task.add_done_callback(chat_updates.discard)

    def on_text(current: AgentSession, event: room_io.TextInputEvent):
        message = event.text.strip()
        if not message:
            return
        current.interrupt()
        if turns >= 32:
            current.say("This demo session has reached its turn limit. Start a new conversation to continue.")
            return
        if len(message) > 1200:
            current.say("Please keep your message under twelve hundred characters.")
            return
        message_id = (event.info.attributes or {}).get("planner.message_id") or event.info.stream_id
        publish_message(message_id, "user", message, time())
        current.generate_reply(user_input=message)

    @session.on("conversation_item_added")
    def on_item(event):
        nonlocal turns
        item = event.item
        if item.type != "message":
            return
        if item.role == "user":
            turns += 1
            if turns > 32:
                ctx.shutdown(reason="Planner conversation limit reached")
        # Typed input already has a stream ID shared with the browser. Speech
        # uses the item's start time, even when recognition finishes later.
        text = item.text_content
        if text and (item.role == "assistant" or (item.role == "user" and item.transcript_confidence is not None)):
            publish_message(item.id, item.role, text, item.created_at)

    @local.register_rpc_method("planner.voice")
    async def set_voice(data):
        nonlocal voice_agent, voice_intro_sent
        if data.caller_identity != identity or data.payload not in {"on", "off"}:
            return "invalid"
        async with voice_lock:
            enabled = data.payload == "on"
            if enabled and voice_agent is None and os.getenv("OPENAI_API_KEY"):
                await session.interrupt()
                voice_agent = VoicePlanner(
                    graph=graph,
                    planner=planner,
                    chat_ctx=session.history.copy(exclude_instructions=True, exclude_function_call=True),
                )
                session.update_agent(voice_agent)
                await asyncio.wait_for(voice_agent.ready.wait(), timeout=8)
                session.input.set_audio_enabled(True)
                session.output.set_audio_enabled(True)
                session.generate_reply(
                    tool_choice="none",
                    instructions=(
                        f"Voice just turned on. Start with this exact introduction: {MIRA_GREETING} "
                        + ("Then ask, naturally, their city, rough budget and how much time they have."
                           if turns == 0 else
                           "Then give them the floor without repeating a question from the chat.")
                    ),
                )
            else:
                session.input.set_audio_enabled(enabled)
                session.output.set_audio_enabled(enabled)
                if enabled and voice_agent is None and not voice_intro_sent:
                    voice_intro_sent = True
                    session.say(
                        f"{MIRA_GREETING} What city, budget and time are you working with?"
                        if turns == 0 else MIRA_GREETING
                    )
            return "ok"

    await session.start(
        agent=Agent(instructions=""),
        room=ctx.room,
        record=False,
        room_options=room_io.RoomOptions(
            participant_identity=identity,
            text_input=room_io.TextInputOptions(text_input_cb=on_text),
            text_output=room_io.TextOutputOptions(sync_transcription=False),
        ),
    )
    session.input.set_audio_enabled(False)
    session.output.set_audio_enabled(False)
    await local.set_attributes({"planner.ready": "1", "planner.ordered_chat": "1"})

    async def time_limit():
        await asyncio.sleep(12 * 60)
        ctx.shutdown(reason="Planner demo time limit reached")

    limit = asyncio.create_task(time_limit())

    async def stop_timer():
        limit.cancel()
        for task in chat_updates:
            task.cancel()
        await asyncio.gather(limit, *chat_updates, return_exceptions=True)

    ctx.add_shutdown_callback(stop_timer)


if __name__ == "__main__":
    cli.run_app(server)
