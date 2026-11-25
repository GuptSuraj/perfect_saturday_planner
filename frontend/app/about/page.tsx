import Link from "next/link";
import { Sun } from "lucide-react";

export const metadata = { title: "Privacy and terms | Perfect Saturday" };

export default function About() {
  return (
    <main className="site-container legal-page">
      <Link href="/" className="brand"><span className="brand-icon"><Sun size={24} /></span><p>Perfect Saturday</p></Link>
      <section className="panel legal-content">
        <h1>Privacy and terms</h1>
        <h2>Planning sessions</h2>
        <p>Perfect Saturday uses your chat messages and preferences to build your itinerary. Voice starts when you enable your microphone. The app keeps the conversation and plan in memory for the session. There are no accounts or saved itineraries.</p>
        <p>LiveKit carries chat and audio. OpenAI processes voice and helps plan your day. Some deployments use Deepgram for transcription and Cartesia for spoken replies. Google Maps and Open-Meteo receive location queries. These providers may process data under their own terms.</p>
        <p>If you choose to use your current location as the start, the browser asks for permission. The planner uses those coordinates during the session and sends them to Google Maps to find the nearby city and estimate travel. You can enter a different starting point or start at the first stop.</p>
        <h2>Venue and forecast information</h2>
        <p>Hours, travel times, weather, ratings, and prices may change. Check venue details before leaving. The app offers suggestions and does not make bookings or take payments.</p>
        <ul>
          <li><a href="https://www.google.com/intl/en_US/help/terms_maps/">Google Maps terms</a></li>
          <li><a href="https://policies.google.com/privacy">Google privacy policy</a></li>
          <li><a href="https://livekit.com/privacy">LiveKit privacy policy</a></li>
        </ul>
      </section>
    </main>
  );
}
