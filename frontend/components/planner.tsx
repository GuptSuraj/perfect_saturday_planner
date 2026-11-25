"use client";

import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarDays, Check, ChevronDown, CircleAlert, LoaderCircle, Mic, MicOff, Send, Sparkles,
  Volume2, X,
} from "lucide-react";
import { costRange, duration, ItineraryPanel, rupees } from "./itinerary";
import { usePlanner } from "./use-planner";
import type { Language, Preferences, TraceEvent } from "../types/planner";

const meter = [0.42, 0.68, 1, 0.62, 0.43];

function MiraMark() {
  return <span className="mira-mark" aria-hidden="true"><Sparkles size="54%" strokeWidth={1.7} /></span>;
}

function PreferencesPanel({ prefs, language }: { prefs: Preferences; language: Language }) {
  const t = (en: string, hi: string) => language === "hi" ? hi : en;
  const values = [
    { label: t("Location", "जगह"), value: [prefs.locality, prefs.city].filter(Boolean).join(", ") },
    { label: t("Budget", "बजट"), value: prefs.budget !== undefined ? rupees(prefs.budget) : "" },
    { label: t("Time available", "उपलब्ध समय"), value: prefs.available_minutes ? duration(prefs.available_minutes, language) : "" },
    { label: t("Mood", "मूड"), value: prefs.mood },
    { label: t("Interests", "दिलचस्पी"), value: prefs.interests?.join(", ") },
    { label: t("Other requirements", "अन्य ज़रूरतें"), value: prefs.constraints?.join(", ") },
    { label: t("Food", "खाना"), value: prefs.dietary && {
      vegetarian_options: t("Vegetarian options", "शाकाहारी विकल्प"),
      vegetarian_only: t("Fully vegetarian kitchen", "पूरी तरह शाकाहारी रसोई"),
      requires_confirmation: t("Confirm dietary requirements", "खाने की ज़रूरतों की पुष्टि करें"),
    }[prefs.dietary] },
    { label: t("Setting", "माहौल"), value: prefs.environment === "indoors" ? t("Indoors", "अंदर") : prefs.environment === "outdoors" ? t("Outdoors", "बाहर") : "" },
    { label: t("Accessibility", "सुगमता"), value: prefs.accessibility_required && t("Wheelchair-accessible entry", "व्हीलचेयर से प्रवेश") },
    { label: t("Start time", "शुरू होने का समय"), value: prefs.start_time },
    { label: t("Group size", "लोग"), value: prefs.people },
    { label: t("Travel", "यात्रा"), value: prefs.transport === "walk" ? t("Walking", "पैदल") : prefs.transport === "drive" ? t("Driving", "गाड़ी से") : "" },
  ].filter(({ value }) => Boolean(value));

  if (values.length === 0) return null;
  return (
    <details className="preferences">
      <summary>{t("Your preferences", "आपकी पसंद और ज़रूरतें")} <ChevronDown size={16} aria-hidden="true" /></summary>
      <dl>{values.map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </details>
  );
}

function TracePanel({ trace, language, expanded }: { trace: TraceEvent[]; language: Language; expanded: boolean }) {
  if (trace.length === 0) return null;
  return (
    <details className="trace-panel" open={expanded}>
      <summary>{language === "hi" ? "मीरा ने क्या किया" : "What Mira did"} <ChevronDown size={16} aria-hidden="true" /></summary>
      <ol className="trace-list">
        {trace.map((event) => (
          <li key={event.id} className={event.status}>
            {event.status === "running" ? <LoaderCircle size={15} className="spinner" aria-hidden="true" /> : event.status === "success" ? <Check size={15} aria-hidden="true" /> : <CircleAlert size={15} aria-hidden="true" />}
            <span>{event.description}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export function Planner() {
  const planner = usePlanner();
  const { language } = planner;
  const t = (en: string, hi: string) => language === "hi" ? hi : en;
  const [draft, setDraft] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const details = useRef<HTMLDialogElement>(null);
  const followLatest = useRef(true);
  const messages = useMemo(
    () => [...planner.messages].sort((a, b) => a.timestamp - b.timestamp),
    [planner.messages],
  );
  const connected = planner.connection === "connected";
  const pending = planner.connection === "connecting" || planner.connection === "reconnecting";
  const started = planner.messages.length > 0 || planner.voiceStarted;
  const speaking = planner.agentStatus === "speaking" && planner.voiceStarted;
  const status = planner.micChange ? t("Connecting voice…", "आवाज़ जुड़ रही है…") :
    pending ? t("Connecting…", "जुड़ रही हूँ…") :
    planner.userSpeaking ? t("Listening to you", "आपकी बात सुन रही हूँ") :
    speaking ? t("Mira is speaking", "मीरा बोल रही हैं") :
    planner.working ? t("Mira is checking your plan…", "मीरा आपकी योजना की जाँच कर रही हैं…") :
    connected && planner.agentStatus === "thinking" ? t("Mira is thinking…", "मीरा सोच रही हैं…") :
    planner.mic ? t("Listening", "सुन रही हूँ") :
    planner.voiceStarted ? t("Microphone muted", "माइक बंद है") : "";
  const level = Math.min(1, Math.sqrt(planner.voiceLevel) * 1.65);
  const talkLabel = t("Talk to Mira", "मीरा से बात करें");
  const voiceLabel = planner.mic ? t("Mute microphone", "माइक बंद करें") : planner.voiceStarted ? t("Unmute microphone", "माइक चालू करें") : talkLabel;
  const endLabel = t("End chat", "चैट बंद करें");
  const detailsLabel = planner.plan ? t("Itinerary", "योजना") : t("Details", "जानकारी");
  const messageLabel = t("Message Mira", "मीरा को लिखें");
  const sendLabel = t("Send message", "संदेश भेजें");
  const changing = planner.changingLanguage;

  useEffect(() => {
    const log = transcript.current;
    if (log && followLatest.current) log.scrollTop = log.scrollHeight;
  }, [planner.messages]);

  useEffect(() => {
    const log = transcript.current;
    if (!log) return;
    const resize = new ResizeObserver(() => {
      if (followLatest.current) log.scrollTop = log.scrollHeight;
    });
    resize.observe(log);
    return () => resize.disconnect();
  }, [started]);

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
    <div className="chat-app" lang={language === "hi" ? "hi-IN" : "en-IN"}>
      <header className="site-header">
        <div className="header-inner">
          <div className="brand">
            <MiraMark />
            <div><strong>{t("Mira", "मीरा")}</strong><span>{t("Saturday planner", "शनिवार की योजना")}</span></div>
          </div>
          <div className="header-actions">
            <div className="language-picker">
              <select aria-label={t("Conversation language", "बातचीत की भाषा")} value={language}
                disabled={pending || changing || planner.sending || planner.micChange}
                onChange={(event) => void planner.changeLanguage(event.target.value === "hi" ? "hi" : "en")}>
                <option value="en" lang="en">English</option>
                <option value="hi" lang="hi">हिंदी</option>
              </select>
              <ChevronDown size={13} aria-hidden="true" />
            </div>
            {(planner.plan || planner.trace.length > 0 || Object.keys(planner.prefs).length > 0) && (
              <button className="header-button" onClick={showDetails} aria-label={detailsLabel} title={detailsLabel}>
                <CalendarDays size={16} aria-hidden="true" />
                <span className="details-label">{detailsLabel}</span>
              </button>
            )}
            {(connected || pending) && (
              <button className="header-button end-button" onClick={planner.end} aria-label={endLabel} title={endLabel}>
                <X size={17} aria-hidden="true" /><span>{endLabel}</span>
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="chat-main" aria-labelledby="chat-title">
        <h1 id="chat-title" className="sr-only">{t("Chat with Mira", "मीरा से बातचीत")}</h1>
        {!started ? (
          <div className="welcome">
            <MiraMark />
            <h2>{t("Hi, I’m Mira.", "नमस्ते, मैं मीरा हूँ।")}</h2>
            <p>{t("Let’s plan your Saturday. Tell me where you’ll be and what you’re in the mood for.", "चलिए, शनिवार की योजना बनाते हैं। बताइए आप कहाँ होंगे और क्या करना चाहेंगे।")}</p>
            <button className="talk-button" disabled={planner.micChange || changing} onClick={() => void planner.toggleMic()}>
              {planner.micChange ? <LoaderCircle size={19} className="spinner" /> : <Mic size={19} />}
              {planner.micChange ? t("Connecting…", "जुड़ रही हूँ…") : talkLabel}
            </button>
          </div>
        ) : (
          <div className="chat-scroll" ref={transcript} role="log" aria-label={t("Conversation with Mira", "मीरा से बातचीत")}
            onScroll={(event) => {
              const log = event.currentTarget;
              followLatest.current = log.scrollHeight - log.scrollTop - log.clientHeight < 100;
            }}>
            <div className="chat-messages">
              {messages.map((message) => (
                <div className={`message-row ${message.role}`} key={message.id}>
                  {message.role !== "user" && <MiraMark />}
                  {message.role === "plan" ? planner.plan && (
                    <button className="plan-card" onClick={showDetails}>
                      <CalendarDays size={22} strokeWidth={1.65} aria-hidden="true" />
                      <span>
                        <strong>{planner.plan.title}</strong>
                        <small>{duration(planner.plan.duration_minutes, language)} · {costRange(planner.plan.estimated_cost_min, planner.plan.estimated_cost_max)} {t("estimated", "अनुमानित")}</small>
                      </span>
                      <span className="plan-link">{t("View plan", "योजना देखें")}</span>
                    </button>
                  ) : (
                    <p className="bubble" lang={/\p{Script=Devanagari}/u.test(message.text) ? "hi" : "en"}>
                      <span className="sr-only">{message.role === "assistant" ? t("Mira: ", "मीरा: ") : t("You: ", "आप: ")}</span>{message.text}
                    </p>
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
                <button className="enable-sound" onClick={planner.enableSound}><Volume2 size={16} /> {t("Enable sound", "आवाज़ चालू करें")}</button>
              )}
            </div>
          )}
          {planner.notice && <p className="notice" role="alert"><CircleAlert size={17} /> {planner.notice}</p>}
          <form className="composer" onSubmit={submit}>
            {started && (
              <button type="button" className={`composer-button mic-button ${planner.mic ? "mic-live" : ""}`}
                aria-label={voiceLabel} title={voiceLabel} disabled={planner.micChange || changing}
                onClick={() => void planner.toggleMic()}>
                {planner.micChange ? <LoaderCircle size={19} className="spinner" /> : planner.mic ? <Mic size={19} /> : <MicOff size={19} />}
              </button>
            )}
            <label className="sr-only" htmlFor="planner-message">{messageLabel}</label>
            <textarea
              id="planner-message" ref={input} value={draft} placeholder={`${messageLabel}…`} rows={1} maxLength={1200}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  if (!changing) void submit();
                }
              }}
            />
            <button className="composer-button send-button" aria-label={sendLabel} title={sendLabel} disabled={!draft.trim() || planner.sending || changing}>
              {planner.sending ? <LoaderCircle size={18} className="spinner" /> : <Send size={18} />}
            </button>
          </form>
          <footer className="site-footer"><a href="/about">{t("Privacy & terms", "गोपनीयता और शर्तें")}</a></footer>
        </div>
      </main>

      <dialog className="details-dialog" ref={details} aria-labelledby="details-title" onClick={(event) => {
        if (event.currentTarget === event.target) event.currentTarget.close();
      }}>
        <div className="details-surface">
          <div className="details-header">
            <h2 id="details-title">{planner.plan ? t("Your itinerary", "आपकी योजना") : t("Planning details", "योजना की जानकारी")}</h2>
            <button className="composer-button close-button" aria-label={t("Close details", "जानकारी बंद करें")} onClick={() => details.current?.close()}><X size={21} /></button>
          </div>
          <PreferencesPanel prefs={planner.prefs} language={language} />
          <TracePanel trace={planner.trace} language={language} expanded={!planner.plan} />
          {planner.plan && <ItineraryPanel plan={planner.plan} revisionNeeded={planner.revisionNeeded} language={language} />}
        </div>
      </dialog>
    </div>
  );
}
