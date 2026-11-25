import {
  CarFront, Check, CloudSun, Footprints, MapPin, Star, TriangleAlert,
} from "lucide-react";
import type { Itinerary, Transit } from "../types/planner";

const money = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
export const rupees = (value: number) => `₹${money.format(value)}`;

export function duration(minutes: number) {
  return [minutes >= 60 ? `${Math.floor(minutes / 60)} hr` : "", minutes % 60 ? `${minutes % 60} min` : ""]
    .filter(Boolean).join(" ") || "0 min";
}

export function costRange(min: number, max: number) {
  return min === max ? rupees(max) : `${rupees(min)} – ${rupees(max)}`;
}

function localDate(date: string) {
  return new Intl.DateTimeFormat("en-IN", {
    weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Kolkata",
  }).format(new Date(`${date}T00:00:00+05:30`));
}

function Travel({ leg, starting = false }: { leg: Transit; starting?: boolean }) {
  const Icon = leg.mode === "walk" ? Footprints : CarFront;
  return (
    <div className="travel-leg">
      <Icon size={15} />
      <span>
        {starting ? "From your starting locality · " : ""}
        {leg.duration_minutes !== null ? duration(leg.duration_minutes) : "Time unavailable"}
        {" "}{leg.mode === "walk" ? "walk" : "drive"}
        {leg.distance_metres !== null && leg.distance_metres > 0 && (
          <> · {leg.distance_metres >= 1000 ? `${(leg.distance_metres / 1000).toFixed(1)} km` : `${leg.distance_metres} m`}</>
        )}
        {" · "}{leg.verification_status === "approximate" ? "Approximate" : leg.source}
      </span>
    </div>
  );
}

export function ItineraryPanel({ plan, revisionNeeded }: { plan: Itinerary; revisionNeeded: boolean }) {
  return (
    <div className="plan-content">
      <div className="plan-status">
        <span className={plan.status === "accepted" && !revisionNeeded ? "tag accepted" : "tag"}>
          {revisionNeeded ? "Updating" : plan.status === "accepted" ? <><Check size={14} /> Finalised</> : "Draft"}
        </span>
      </div>
      <div className="plan-intro">
        <p className="plan-location"><MapPin size={15} /> {plan.city} <span>·</span> {localDate(plan.date)}</p>
        <h3>{plan.title}</h3>
        <p className="plan-explanation">{plan.explanation}</p>
      </div>
      {plan.verification_status === "unverified" && (
        <div className="warning-strip"><TriangleAlert size={17} /> Unverified generic plan</div>
      )}
      <dl className="stats">
        <div><dt>Estimated cost</dt><dd>{costRange(plan.estimated_cost_min, plan.estimated_cost_max)}</dd></div>
        <div><dt>Budget left</dt><dd>{rupees(plan.remaining_budget)}</dd></div>
        <div><dt>Time planned</dt><dd>{duration(plan.duration_minutes)}</dd></div>
      </dl>
      <p className="plan-totals">
        {duration(plan.travel_minutes)} travel · {duration(plan.buffer_minutes)} buffer · {rupees(plan.contingency)} contingency
        {plan.transport_cost_max > 0 && ` · ${costRange(plan.transport_cost_min, plan.transport_cost_max)} transport`}
      </p>
      {plan.weather_summary && <div className="weather"><CloudSun size={22} /><div><strong>Saturday forecast</strong><p>{plan.weather_summary} <span>Open-Meteo</span></p></div></div>}
      <div className="timeline">
        {plan.starting_leg && <Travel leg={plan.starting_leg} starting />}
        <ol>
          {plan.activities.map((stop, index) => (
            <li key={stop.place_id || index}>
              <div className="stop-time"><span className="timeline-dot" /> <time>{stop.start_time}</time><span>–</span><time>{stop.end_time}</time></div>
              <article className="activity">
                <div className="activity-heading">
                  <h4>{stop.name}</h4><strong title={stop.cost_source}>{costRange(stop.estimated_cost_min, stop.estimated_cost_max)}</strong>
                </div>
                {stop.address && <p className="address">{stop.address}</p>}
                {stop.rating !== null && <p className="rating"><Star size={14} /> {stop.rating.toFixed(1)} {stop.rating_count !== null && <span>({money.format(stop.rating_count)} ratings)</span>}</p>}
                <p>{stop.description}</p><p className="why">{stop.reason}</p>
                {stop.warnings.length > 0 && <ul className="stop-warnings">{stop.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
                {stop.maps_url && <a className="maps-link" href={stop.maps_url} target="_blank" rel="noreferrer"><MapPin size={15} /><span translate="no">Google Maps</span></a>}
                {stop.attributions.length > 0 && <p className="address">{stop.attributions.join(" · ")}</p>}
              </article>
              {stop.travel_to_next && <Travel leg={stop.travel_to_next} />}
            </li>
          ))}
        </ol>
      </div>
      {(plan.assumptions.length > 0 || plan.warnings.length > 0) && (
        <details className="check-details">
          <summary>Planning notes and assumptions</summary>
          <ul>{[...plan.warnings, ...plan.assumptions].map((note) => <li key={note}>{note}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
