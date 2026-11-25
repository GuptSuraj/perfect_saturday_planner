import asyncio
import json
import logging
import os

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

    async def publish(channel: str, data: dict) -> bool:
        try:
            payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            if channel == "itinerary":
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
        current.generate_reply(user_input=message)

    @session.on("conversation_item_added")
    def on_item(event):
        nonlocal turns
        if event.item.type == "message" and event.item.role == "user":
            turns += 1
            if turns > 32:
                ctx.shutdown(reason="Planner conversation limit reached")

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
                        + ("Then ask which city they’ll be in and give them the floor."
                           if turns == 0 else
                           "Then give them the floor without repeating a question from the chat.")
                    ),
                )
            else:
                session.input.set_audio_enabled(enabled)
                session.output.set_audio_enabled(enabled)
                if enabled and voice_agent is None and not voice_intro_sent:
                    voice_intro_sent = True
                    session.say(f"{MIRA_GREETING} Where will you be?" if turns == 0 else MIRA_GREETING)
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
    await local.set_attributes({"planner.ready": "1"})

    async def time_limit():
        await asyncio.sleep(12 * 60)
        ctx.shutdown(reason="Planner demo time limit reached")

    limit = asyncio.create_task(time_limit())

    async def stop_timer():
        limit.cancel()
        await asyncio.gather(limit, return_exceptions=True)

    ctx.add_shutdown_callback(stop_timer)


if __name__ == "__main__":
    cli.run_app(server)
