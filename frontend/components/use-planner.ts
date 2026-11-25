"use client";

import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import {
  createLocalAudioTrack, Room, RoomEvent, Track, type LocalAudioTrack, type Participant,
  type RemoteTrack,
} from "livekit-client";
import {
  chatMessage, itinerary, preferenceUpdate, traceEvent,
  type ChatItem, type Itinerary, type Message, type Preferences, type TraceEvent,
} from "../types/planner";

type Connection = "idle" | "connecting" | "connected" | "reconnecting";

export function usePlanner() {
  const roomRef = useRef<Room | null>(null);
  const agentIdentity = useRef("");
  const startPromise = useRef<Promise<Room> | null>(null);
  const lastPlan = useRef<Itinerary | null>(null);
  const epoch = useRef(0);
  const audio = useRef(new Map<RemoteTrack, HTMLMediaElement>());
  const audioContainer = useRef<HTMLDivElement>(null);
  const [connection, setConnection] = useState<Connection>("idle");
  const [agentStatus, setAgentStatus] = useState("initializing");
  const [mic, setMic] = useState(false);
  const [voiceStarted, setVoiceStarted] = useState(false);
  const [voiceLevel, setVoiceLevel] = useState(0);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [soundBlocked, setSoundBlocked] = useState(false);
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [prefs, setPrefs] = useState<Preferences>({});
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [plan, setPlan] = useState<Itinerary | null>(null);
  const [revisionNeeded, setRevisionNeeded] = useState(false);
  const [notice, setNotice] = useState("");
  const [sending, setSending] = useState(false);
  const [micChange, setMicChange] = useState(false);
  const sendingRef = useRef(false);

  function detachAudio() {
    for (const [track, element] of audio.current) {
      track.detach(element);
      element.remove();
    }
    audio.current.clear();
  }

  useEffect(() => () => {
    void roomRef.current?.disconnect();
  }, []);

  useEffect(() => {
    if (!voiceStarted || connection !== "connected") return;
    const room = roomRef.current;
    if (!room) return;
    const readLevels = () => {
      const user = room.localParticipant;
      const agent = room.remoteParticipants.get(agentIdentity.current);
      setUserSpeaking(mic && user.isSpeaking);
      setVoiceLevel(Math.max(mic ? user.audioLevel : 0, agent?.audioLevel ?? 0));
    };
    const interval = window.setInterval(readLevels, 100);
    return () => window.clearInterval(interval);
  }, [connection, mic, voiceStarted]);

  function end() {
    epoch.current += 1;
    const room = roomRef.current;
    roomRef.current = null;
    startPromise.current = null;
    agentIdentity.current = "";
    setConnection("idle");
    setMic(false);
    setVoiceStarted(false);
    setVoiceLevel(0);
    setUserSpeaking(false);
    setSoundBlocked(false);
    setAgentStatus("initializing");
    setNotice("");
    detachAudio();
    void room?.disconnect();
  }

  async function start(): Promise<Room> {
    if (startPromise.current) return startPromise.current;
    if (roomRef.current && agentIdentity.current) return roomRef.current;
    const run = ++epoch.current;
    const room = new Room({ adaptiveStream: true, dynacast: true });
    roomRef.current = room;
    setConnection("connecting");
    setAgentStatus("initializing");
    setNotice("");
    setMessages([]);
    setPrefs({});
    setTrace([]);
    setPlan(null);
    lastPlan.current = null;
    setRevisionNeeded(false);

    let signalReady!: () => void;
    const ready = new Promise<void>((resolve) => { signalReady = resolve; });
    let orderedChat = false;
    const addMessage = (message: Message) => {
      if (epoch.current !== run || !message.text.trim()) return;
      setMessages((items) => [...items.filter((item) => item.id !== message.id), message]);
    };
    const inspect = (participant: Participant) => {
      if (epoch.current !== run || !participant.isAgent) return;
      if (participant.attributes["planner.ready"] === "1") {
        agentIdentity.current = participant.identity;
        orderedChat = participant.attributes["planner.ordered_chat"] === "1";
        setConnection("connected");
        signalReady();
      }
      const state = participant.attributes["lk.agent.state"];
      if (state) setAgentStatus(state);
    };

    room
      .on(RoomEvent.ParticipantConnected, inspect)
      .on(RoomEvent.ParticipantAttributesChanged, (_attributes, participant) => inspect(participant))
      .on(RoomEvent.Reconnecting, () => {
        if (epoch.current === run) setConnection("reconnecting");
      })
      .on(RoomEvent.Reconnected, () => {
        if (epoch.current === run) setConnection(agentIdentity.current ? "connected" : "connecting");
      })
      .on(RoomEvent.AudioPlaybackStatusChanged, (canPlay) => {
        if (epoch.current === run) setSoundBlocked(!canPlay);
      })
      .on(RoomEvent.Disconnected, () => {
        if (epoch.current !== run) return;
        end();
        setNotice("The conversation disconnected. Start a new one to continue.");
      })
      .on(RoomEvent.ParticipantDisconnected, (participant) => {
        if (epoch.current !== run || participant.identity !== agentIdentity.current) return;
        end();
        setNotice("The planner session ended. Start a new conversation to continue.");
      })
      .on(RoomEvent.TrackSubscribed, (track) => {
        if (epoch.current !== run || track.kind !== Track.Kind.Audio) return;
        const element = track.attach();
        element.autoplay = true;
        audio.current.set(track, element);
        audioContainer.current?.append(element);
      })
      .on(RoomEvent.TrackUnsubscribed, (track) => {
        const element = audio.current.get(track);
        if (!element) return;
        track.detach(element);
        element.remove();
        audio.current.delete(track);
      });

    room.registerTextStreamHandler("lk.transcription", async (reader, sender) => {
      try {
        if (orderedChat) return;
        const { attributes, id, timestamp } = reader.info;
        const role = sender.identity === room.localParticipant.identity ? "user" : "assistant";
        // RoomIO echoes text input without a tracked audio source. We add our local
        // text after sending, so only keep transcriptions from the microphone here.
        if (role === "user" && !attributes?.["lk.transcribed_track_id"]) return;
        const segment = attributes?.["lk.segment_id"] || id;
        if (role === "assistant") {
          let text = "";
          for await (const chunk of reader) {
            text += chunk;
            if (!orderedChat) addMessage({ id: segment, role, text, timestamp });
          }
        } else {
          const text = await reader.readAll();
          if (reader.info.attributes?.["lk.transcription_final"] === "true") {
            if (!orderedChat) addMessage({ id: segment, role, text, timestamp });
          }
        }
      } catch {
        // An interrupted stream can close before a transcript is complete.
      }
    });

    room.registerTextStreamHandler("planner.chat", async (reader, sender) => {
      try {
        const message = chatMessage.parse(JSON.parse(await reader.readAll()));
        if (sender.identity === agentIdentity.current) addMessage(message);
      } catch {
        // An invalid or interrupted stream cannot update the transcript.
      }
    });

    room.registerTextStreamHandler("planner.trace", async (reader, sender) => {
      try {
        const data = traceEvent.parse(JSON.parse(await reader.readAll()));
        if (epoch.current !== run || sender.identity !== agentIdentity.current) return;
        setTrace((items) => {
          const existing = items.find((item) => item.id === data.id);
          if (existing) {
            if (data.status === "running") return items;
            return items.map((item) => item.id === data.id ? data : item);
          }
          return [...items.slice(-79), data];
        });
      } catch {
        // Ignore malformed or interrupted trace events.
      }
    });

    room.registerTextStreamHandler("planner.preferences", async (reader, sender) => {
      try {
        const data = preferenceUpdate.parse(JSON.parse(await reader.readAll()));
        if (epoch.current !== run || sender.identity !== agentIdentity.current) return;
        setPrefs(data.preferences);
        setRevisionNeeded(data.revision_needed);
      } catch {
        // An invalid update leaves the last visible preferences intact.
      }
    });

    room.localParticipant.registerRpcMethod("planner.itinerary", async ({ callerIdentity, payload }) => {
      if (epoch.current !== run || callerIdentity !== agentIdentity.current) return "rejected";
      const data = JSON.parse(payload);
      const next = itinerary.parse(data);
      const previous = lastPlan.current;
      if (previous && (previous.itinerary_id !== next.itinerary_id || previous.version > next.version)) {
        return "rejected";
      }
      flushSync(() => {
        lastPlan.current = next;
        setPlan(next);
        setRevisionNeeded(false);
        if (previous?.version !== next.version) {
          setMessages((items) => [
            ...items.filter((item) => item.role !== "plan"),
            {
              id: `plan-${next.version}`, role: "plan",
              timestamp: typeof data.chat_timestamp === "number" ? data.chat_timestamp : Date.now(),
            },
          ]);
        }
      });
      return `${next.itinerary_id}:${next.version}:${next.status}`;
    });

    const opening = (async () => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const response = await fetch("/api/token", { method: "POST", signal: AbortSignal.timeout(12000) });
        const details = await response.json();
        if (!response.ok) throw new Error(details.error || "Could not start the conversation.");
        if (epoch.current !== run) throw new Error("Conversation cancelled.");
        await room.connect(details.serverUrl, details.participantToken);
        if (epoch.current !== run) {
          await room.disconnect();
          throw new Error("Conversation cancelled.");
        }
        room.remoteParticipants.forEach(inspect);
        await Promise.race([
          ready,
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => reject(new Error("The planner is taking too long to connect. Try again.")), 32000);
          }),
        ]);
        if (epoch.current !== run) throw new Error("Conversation cancelled.");
        return room;
      } catch (error) {
        if (epoch.current === run) {
          end();
          setNotice(error instanceof Error ? error.message : "Could not connect to the planner.");
        }
        throw error;
      } finally {
        if (timer) clearTimeout(timer);
        if (epoch.current === run) startPromise.current = null;
      }
    })();
    startPromise.current = opening;
    return opening;
  }

  async function send(text: string): Promise<boolean> {
    const content = text.trim();
    if (!content || sendingRef.current) return false;
    sendingRef.current = true;
    setSending(true);
    setNotice("");
    let room: Room | undefined;
    try {
      room = await start();
      // Render immediately, before an agent response has a chance to arrive.
      const id = crypto.randomUUID();
      setMessages((items) => [...items, { id, text: content, role: "user", timestamp: Date.now() }]);
      try {
        await room.localParticipant.sendText(content, {
          topic: "lk.chat", attributes: { "planner.message_id": id },
          destinationIdentities: [agentIdentity.current],
        });
        return true;
      } catch (error) {
        setMessages((items) => items.filter((item) => item.id !== id));
        throw error;
      }
    } catch {
      if (room && room === roomRef.current) setNotice("Message could not be sent. Try again.");
      return false;
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }

  async function toggleMic() {
    setMicChange(true);
    setNotice("");
    let room: Room | undefined;
    let track: LocalAudioTrack | undefined;
    try {
      if (mic && roomRef.current) {
        const participant = roomRef.current.localParticipant;
        const published = participant.getTrackPublication(Track.Source.Microphone)?.track;
        if (published) await participant.unpublishTrack(published, true);
        setMic(false);
        setUserSpeaking(false);
        return;
      }
      const connectionTask = start();
      void connectionTask.catch(() => undefined);
      const openingRoom = roomRef.current;
      void openingRoom?.startAudio().catch(() => undefined);
      try {
        track = await createLocalAudioTrack({
          echoCancellation: true, noiseSuppression: true, autoGainControl: true,
        });
      } catch {
        setNotice("Microphone unavailable. You can continue by typing below.");
        return;
      }
      room = await connectionTask;
      await room.localParticipant.publishTrack(track, { source: Track.Source.Microphone });
      const result = await room.localParticipant.performRpc({
        destinationIdentity: agentIdentity.current, method: "planner.voice",
        payload: "on", responseTimeout: 10500,
      });
      if (result !== "ok") throw new Error("Voice could not be enabled.");
      if (room !== roomRef.current) return;
      setMic(true);
      setVoiceStarted(true);
      setSoundBlocked(!room.canPlaybackAudio);
    } catch {
      if (track) {
        if (room) await room.localParticipant.unpublishTrack(track, true).catch(() => track?.stop());
        track.stop();
      }
      if (room && room === roomRef.current) setNotice("Voice couldn’t connect. You can keep typing.");
    } finally {
      setMicChange(false);
    }
  }

  function enableSound() {
    const room = roomRef.current;
    if (room) void room.startAudio().catch(() => setSoundBlocked(true));
  }

  const working = trace.some((event) => event.status === "running");
  return {
    connection, agentStatus, mic, voiceStarted, voiceLevel, userSpeaking, soundBlocked, messages,
    prefs, trace, plan, revisionNeeded, notice, working, sending, micChange, audioContainer,
    end, send, toggleMic, enableSound,
  };
}
