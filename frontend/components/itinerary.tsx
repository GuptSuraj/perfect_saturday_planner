import {
  CarFront, Check, CloudSun, Footprints, MapPin, Star, TriangleAlert,
} from "lucide-react";
import type { Itinerary, Language, Transit } from "../types/planner";

const money = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
export const rupees = (value: number) => `₹${money.format(value)}`;

export function duration(minutes: number, language: Language = "en") {
  const [hr, min] = language === "hi" ? ["घंटे", "मिनट"] : ["hr", "min"];
  return [minutes >= 60 ? `${Math.floor(minutes / 60)} ${hr}` : "", minutes % 60 ? `${minutes % 60} ${min}` : ""]
    .filter(Boolean).join(" ") || `0 ${min}`;
}

export function costRange(min: number, max: number) {
  return min === max ? rupees(max) : `${rupees(min)} – ${rupees(max)}`;
}

function localDate(date: string, language: Language) {
  return new Intl.DateTimeFormat(`${language}-IN`, {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Kolkata",
  }).format(new Date(`${date}T00:00:00+05:30`));
}

function Travel({ leg, language, starting = false }: { leg: Transit; language: Language; starting?: boolean }) {
  const t = (en: string, hi: string) => language === "hi" ? hi : en;
  const Icon = leg.mode === "walk" ? Footprints : CarFront;
  return (
    <div className="travel-leg">
      <Icon size={15} />
      <span>
        {starting ? t("From your starting locality · ", "शुरुआती जगह से · ") : ""}
        {leg.duration_minutes !== null ? duration(leg.duration_minutes, language) : t("Time unavailable", "समय उपलब्ध नहीं")}
        {" "}{leg.mode === "walk" ? t("walk", "पैदल") : t("drive", "गाड़ी से")}
        {leg.distance_metres !== null && leg.distance_metres > 0 && (
          <> · {leg.distance_metres >= 1000 ? `${(leg.distance_metres / 1000).toFixed(1)} km` : `${leg.distance_metres} m`}</>
        )}
        {" · "}{leg.verification_status === "approximate" ? t("Approximate", "अनुमानित") : leg.source}
      </span>
    </div>
  );
}

export function ItineraryPanel({ plan, revisionNeeded, language }: { plan: Itinerary; revisionNeeded: boolean; language: Language }) {
  const t = (en: string, hi: string) => language === "hi" ? hi : en;
  return (
    <div className="plan-content">
      <div className="plan-status">
        <span className={plan.status === "accepted" && !revisionNeeded ? "tag accepted" : "tag"}>
          {revisionNeeded ? t("Revision pending", "बदलाव बाकी है") : plan.status === "accepted" ? <><Check size={14} /> {t("Finalised", "पक्की")}</> : t("Draft", "मसौदा")}
        </span>
      </div>
      <div className="plan-intro">
        <p className="plan-location"><MapPin size={15} /> {plan.city} <span>·</span> {localDate(plan.date, language)}</p>
        <h3>{plan.title}</h3>
        <p className="plan-explanation">{plan.explanation}</p>
      </div>
      {plan.verification_status === "unverified" && (
        <div className="warning-strip"><TriangleAlert size={17} /> {t("Rough plan · venues unverified", "मोटी योजना · जगहों की पुष्टि नहीं हुई")}</div>
      )}
      <dl className="stats">
        <div><dt>{t("Estimated cost", "अनुमानित खर्च")}</dt><dd>{costRange(plan.estimated_cost_min, plan.estimated_cost_max)}</dd></div>
        <div><dt>{t("Budget left", "बचा बजट")}</dt><dd>{rupees(plan.remaining_budget)}</dd></div>
        <div><dt>{t("Time planned", "योजना की अवधि")}</dt><dd>{duration(plan.duration_minutes, language)}</dd></div>
      </dl>
      <p className="plan-totals">
        {duration(plan.travel_minutes, language)} {t("travel", "यात्रा")} · {duration(plan.buffer_minutes, language)} {t("buffer", "अतिरिक्त समय")} · {rupees(plan.contingency)} {t("contingency", "अतिरिक्त खर्च के लिए")}
        {plan.transport_cost_max > 0 && ` · ${costRange(plan.transport_cost_min, plan.transport_cost_max)} ${t("transport", "किराया")}`}
      </p>
      {plan.weather_summary && <div className="weather"><CloudSun size={22} /><div><strong>{t("Saturday forecast", "शनिवार का मौसम")}</strong><p>{plan.weather_summary} <span>Open-Meteo</span></p></div></div>}
      <div className="timeline">
        {plan.starting_leg && <Travel leg={plan.starting_leg} language={language} starting />}
        <ol>
          {plan.activities.map((stop, index) => (
            <li key={stop.place_id || index}>
              <div className="stop-time"><span className="timeline-dot" /> <time>{stop.start_time}</time><span>–</span><time>{stop.end_time}</time></div>
              <article className="activity">
                <div className="activity-heading">
                  <h4>{stop.name}</h4><strong title={stop.cost_source}>{costRange(stop.estimated_cost_min, stop.estimated_cost_max)}</strong>
                </div>
                {stop.address && <p className="address">{stop.address}</p>}
                {stop.rating !== null && <p className="rating"><Star size={14} /> {stop.rating.toFixed(1)} {stop.rating_count !== null && <span>({money.format(stop.rating_count)} {t("ratings", "रेटिंग")})</span>}</p>}
                <p>{stop.description}</p>
                <p className="why"><strong>{t("Why it fits", "यह आपके लिए क्यों सही है")}</strong>{stop.reason}</p>
                {stop.warnings.length > 0 && <ul className="stop-warnings">{stop.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
                {stop.maps_url && <a className="maps-link" href={stop.maps_url} target="_blank" rel="noreferrer"><MapPin size={15} /><span translate="no">Google Maps</span></a>}
                {stop.attributions.length > 0 && <p className="address">{stop.attributions.join(" · ")}</p>}
              </article>
              {stop.travel_to_next && <Travel leg={stop.travel_to_next} language={language} />}
            </li>
          ))}
        </ol>
      </div>
      {(plan.assumptions.length > 0 || plan.warnings.length > 0) && (
        <details className="check-details">
          <summary>{t("Planning notes and assumptions", "योजना से जुड़ी बातें और अनुमान")}</summary>
          <ul>{[...plan.warnings, ...plan.assumptions].map((note) => <li key={note}>{note}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
