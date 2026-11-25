"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  CalendarDays, Check, ChevronDown, CircleAlert, LoaderCircle, Mic, MicOff, Send, Sparkles,
  Volume2, X,
} from "lucide-react";
import { costRange, duration, ItineraryPanel, rupees } from "./itinerary";
import { usePlanner } from "./use-planner";
import type { Preferences, TraceEvent } from "../types/planner";

const meter = [0.42, 0.68, 1, 0.62, 0.43];

function MiraMark() {
  return <span className="mira-mark" aria-hidden="true"><Sparkles size="54%" strokeWidth={1.7} /></span>;
}

function PreferencesPanel({ prefs }: { prefs: Preferences }) {
  const values = [
    { label: "Location", value: [prefs.locality, prefs.city].filter(Boolean).join(", ") },
    { label: "Budget", value: prefs.budget !== undefined ? rupees(prefs.budget) : "" },
    { label: "Time available", value: prefs.available_minutes ? duration(prefs.available_minutes) : "" },
    { label: "Mood", value: prefs.mood },
    { label: "Interests", value: prefs.interests?.join(", ") },
    { label: "Preferences", value: prefs.constraints?.join(", ") },
    { label: "Start time", value: prefs.start_time },
    { label: "Group size", value: prefs.people },
    { label: "Travel", value: prefs.transport === "walk" ? "Walking" : prefs.transport === "drive" ? "Driving" : "" },
  ].filter(({ value }) => Boolean(value));

  if (values.length === 0) return null;
  return (
    <details className="preferences">
      <summary>Trip preferences <ChevronDown size={16} aria-hidden="true" /></summary>
      <dl>{values.map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </details>
  );
}

function TracePanel({ trace }: { trace: TraceEvent[] }) {
  if (trace.length === 0) return null;
  return (
    <details className="trace-panel">
      <summary>Planning activity <ChevronDown size={16} aria-hidden="true" /></summary>
      <ol className="trace-list">
        {trace.slice().reverse().map((event) => (
          <li key={event.id} className={event.status}>
            {event.status === "running" ? <LoaderCircle size={15} className="spinner" /> : event.status === "success" ? <Check size={15} /> : <CircleAlert size={15} />}
            <span>{event.description}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export function Planner() {
  const planner = usePlanner();
  const [draft, setDraft] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const details = useRef<HTMLDialogElement>(null);
  const followLatest = useRef(true);
  const connected = planner.connection === "connected";
  const pending = planner.connection === "connecting" || planner.connection === "reconnecting";
  const started = planner.messages.length > 0 || planner.voiceStarted;
  const speaking = planner.agentStatus === "speaking" && planner.voiceStarted;
  const status = planner.micChange ? "Connecting voice…" :
    pending ? "Connecting…" :
    planner.userSpeaking ? "Listening to you" :
    speaking ? "Mira is speaking" :
    planner.working ? "Mira is looking up places…" :
    connected && planner.agentStatus === "thinking" ? "Mira is thinking…" :
    planner.mic ? "Listening" :
    planner.voiceStarted ? "Microphone muted" : "";
  const level = Math.min(1, Math.sqrt(planner.voiceLevel) * 1.65);
  const voiceLabel = planner.mic ? "Mute microphone" : planner.voiceStarted ? "Unmute microphone" : "Talk to Mira";

  useEffect(() => {
    const log = transcript.current;
    if (log && followLatest.current) log.scrollTop = log.scrollHeight;
  }, [planner.messages]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    followLatest.current = true;
    const submitted = draft;
    if (await planner.send(submitted)) {
      setDraft((value) => value === submitted ? "" : value);
      input.current?.focus();
    }
  }

  function showDetails() {
    if (details.current && !details.current.open) details.current.showModal();
  }

  return (
    <div className="chat-app">
      <header className="site-header">
        <div className="header-inner">
          <div className="brand">
            <MiraMark />
            <div><strong>Mira</strong><span>Saturday planner</span></div>
          </div>
          <div className="header-actions">
            {(planner.plan || planner.trace.length > 0) && (
              <button className="header-button" onClick={showDetails}>
                {planner.plan && <CalendarDays size={16} aria-hidden="true" />}
                {planner.plan ? "Itinerary" : "Activity"}
              </button>
            )}
            {(connected || pending) && (
              <button className="header-button end-button" onClick={planner.end}>End chat</button>
            )}
          </div>
        </div>
      </header>

      <main className="chat-main" aria-labelledby="chat-title">
        <h1 id="chat-title" className="sr-only">Chat with Mira</h1>
        {!started ? (
          <div className="welcome">
            <MiraMark />
            <h2>Hi, I’m Mira.</h2>
            <p>Let’s plan your Saturday. Tell me where you’ll be and what you’re in the mood for.</p>
            <button className="talk-button" disabled={planner.micChange} onClick={() => void planner.toggleMic()}>
              {planner.micChange ? <LoaderCircle size={19} className="spinner" /> : <Mic size={19} />}
              {planner.micChange ? "Connecting…" : "Talk to Mira"}
            </button>
          </div>
        ) : (
          <div className="chat-scroll" ref={transcript} role="log" aria-label="Conversation with Mira"
            onScroll={(event) => {
              const log = event.currentTarget;
              followLatest.current = log.scrollHeight - log.scrollTop - log.clientHeight < 100;
            }}>
            <div className="chat-messages">
              {planner.messages.map((message) => (
                <div className={`message-row ${message.role}`} key={message.id}>
                  {message.role !== "user" && <MiraMark />}
                  {message.role === "plan" ? planner.plan && (
                    <button className="plan-card" onClick={showDetails}>
                      <CalendarDays size={22} strokeWidth={1.65} aria-hidden="true" />
                      <span>
                        <strong>{planner.plan.title}</strong>
                        <small>{duration(planner.plan.duration_minutes)} · {costRange(planner.plan.estimated_cost_min, planner.plan.estimated_cost_max)} estimated</small>
                      </span>
                      <span className="plan-link">View plan</span>
                    </button>
                  ) : (
                    <p className="bubble"><span className="sr-only">{message.role === "assistant" ? "Mira: " : "You: "}</span>{message.text}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="audio-output" ref={planner.audioContainer} aria-hidden="true" />
        <div className="chat-bottom">
          {started && (status || planner.soundBlocked) && (
            <div className="voice-state">
              <div className="voice-reading" role="status">
                {planner.voiceStarted && <span className="meter" aria-hidden="true">
                  {meter.map((weight) => <i key={weight} style={{ transform: `scaleY(${0.23 + weight * level * 0.77})` }} />)}
                </span>}
                <span>{status}</span>
              </div>
              {planner.voiceStarted && planner.soundBlocked && (
                <button className="enable-sound" onClick={planner.enableSound}><Volume2 size={16} /> Enable sound</button>
              )}
            </div>
          )}
          {planner.notice && <p className="notice" role="alert"><CircleAlert size={17} /> {planner.notice}</p>}
          <form className="composer" onSubmit={submit}>
            {started && (
              <button type="button" className={`composer-button mic-button ${planner.mic ? "mic-live" : ""}`}
                aria-label={voiceLabel} title={voiceLabel} disabled={planner.micChange}
                onClick={() => void planner.toggleMic()}>
                {planner.micChange ? <LoaderCircle size={19} className="spinner" /> : planner.mic ? <MicOff size={19} /> : <Mic size={19} />}
              </button>
            )}
            <label className="sr-only" htmlFor="planner-message">Message Mira</label>
            <textarea
              id="planner-message" ref={input} value={draft} placeholder="Message Mira…" rows={1} maxLength={1200}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void submit();
                }
              }}
            />
            <button className="composer-button send-button" aria-label="Send message" title="Send message" disabled={!draft.trim() || planner.sending}>
              {planner.sending ? <LoaderCircle size={18} className="spinner" /> : <Send size={18} />}
            </button>
          </form>
          <footer className="site-footer"><a href="/about">Privacy &amp; terms</a></footer>
        </div>
      </main>

      <dialog className="details-dialog" ref={details} aria-labelledby="details-title" onClick={(event) => {
        if (event.currentTarget === event.target) event.currentTarget.close();
      }}>
        <div className="details-surface">
          <div className="details-header">
            <h2 id="details-title">{planner.plan ? "Your itinerary" : "Planning details"}</h2>
            <button className="composer-button close-button" aria-label="Close details" onClick={() => details.current?.close()}><X size={21} /></button>
          </div>
          {planner.plan && <ItineraryPanel plan={planner.plan} revisionNeeded={planner.revisionNeeded} />}
          <PreferencesPanel prefs={planner.prefs} />
          <TracePanel trace={planner.trace} />
        </div>
      </dialog>
    </div>
  );
}
