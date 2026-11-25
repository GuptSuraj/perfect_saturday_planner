# Perfect Saturday

Mira is the voice and text planner for Perfect Saturday. She turns a conversation into a budgeted itinerary using real places, local forecasts, and route estimates for Indian cities. The plan appears as a card in the chat, and you can ask Mira to revise it in the same session.

[Open the live app](https://perfect-saturday-planner-gamma.vercel.app)

## Architecture

```text
Next.js / Vercel
    LiveKit WebRTC, lk.chat, lk.transcription, planner streams, itinerary RPC
LiveKit Cloud / Python 3.12
    Voice → OpenAI Realtime speech-to-speech → asynchronous planning tool
    Text-only → LiveKit LangChain LLMAdapter
LangGraph
    Model chooses tools → ToolNode executes → model observes results
    HTTPX providers + Pydantic proposals and validation
```

Voice mode uses semantic turn detection so the user can pause or interrupt naturally. The conversation stays open while GPT-5-mini and LangGraph search, calculate routes, and validate the itinerary. A new planning request can replace an in-progress search. Each LiveKit room has its own graph and history. The browser validates each itinerary payload and acknowledges it over RPC before the server records the new version.

| Tool | What it does |
| --- | --- |
| `update_preferences` | Merges partial preference updates and flags an itinerary for revision. |
| `search_places` | Fetches and grounds venues from Google Places API (New). |
| `get_place_details` | Checks hours, price, operating status, accessibility, and listed venue features. |
| `get_weather` | Gets a local hourly forecast from Open-Meteo for the selected Saturday. |
| `calculate_route` | Gets a walking or driving estimate from Google Routes, with a labelled approximation where possible. |
| `estimate_cost` | Calculates group costs and contingency using provider prices or estimated ranges. |
| `validate_itinerary` | Checks venue data, relevant constraints, budget, hours, weather, travel, and scheduling conflicts. |
| `display_itinerary` | Revalidates and publishes an itinerary only when the browser acknowledges it. |
| `confirm_itinerary` | Marks the displayed itinerary accepted after explicit user approval. |

## Run locally

Use Python 3.12 with `uv` and Node.js 24 with `npm`. Generate a LiveKit Cloud project key and enable Google Places API (New) and Google Routes API in the same billed Google Cloud project.

1. Copy `backend/.env.example` to `backend/.env.local` and `frontend/.env.example` to `frontend/.env.local`. Use the same `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` in both. Put the Google Maps key in the backend file. All credentials are server-side.
2. Set `OPENAI_API_KEY` in the backend for OpenAI Realtime and GPT-5-mini. Set `OPENAI_REALTIME_MODEL` or `OPENAI_REALTIME_VOICE` to override the `gpt-realtime` and `marin` defaults. `OPENAI_MODEL` configures the planning model. Without an OpenAI key, the worker uses the LiveKit Inference pipeline with Deepgram, Cartesia, and GPT-5-mini.
3. In separate terminals, run the worker and frontend.

```sh
cd backend
uv sync --locked
uv run src/agent.py download-files
uv run src/agent.py dev
```

```sh
cd frontend
npm ci
npm run dev
```

Open [localhost:3000](http://localhost:3000). Type to Mira or select **Talk to Mira**. Open an itinerary card to see the stops, timing, estimated cost, and sources. When testing a local worker against a project that already runs a deployed worker with the same dispatch name, use a separate project to ensure the room reaches the local worker.

## Deploy

1. In `backend/`, sign in with `lk cloud auth` and select the project with `lk project set-default`. Create a worker using `lk agent create --region ap-south --secrets-file /path/to/agent-secrets.env`. Include the Maps key and optional OpenAI settings in the secret file. LiveKit Cloud injects its project credentials. The worker registers the dispatch name `perfect-saturday`. For later changes, use `lk agent deploy` from the linked backend directory.
2. In `frontend/`, run `npx vercel login` and `npx vercel link`. Add the three LiveKit values to Vercel’s production environment using the dashboard or `vercel env add` with stdin. Run `npx vercel deploy --prod`. For Git-based deployment, set `frontend` as the project’s root directory.
3. Open the production domain in a signed-out browser session. Select **Talk to Mira** to hear her introduction, try interrupting her or asking a question during a search, and open the itinerary card. Try a second chat using only text.

The token endpoint issues five-minute, room-scoped tokens. The browser can publish microphone audio and data. The public demo applies a best-effort per-instance rate limit, plus server-side session duration, turn and tool limits.

[LiveKit deploy quickstart](https://docs.livekit.io/deploy/agents/quickstart/) · [LiveKit LangGraph adapter](https://docs.livekit.io/agents/models/llm/langchain/) · [Vercel CLI deployment](https://vercel.com/docs/cli/deploy)

## Verification

```sh
(cd backend && uv run --locked pytest -q)
(cd frontend && npm ci && npm run build)
```

The backend suite covers grounded time and cost totals, automatic scheduling around routes, hard constraints, regular and date-specific hours, display acknowledgement, budget revisions, weather suitability, and a simulated Places timeout with a labelled generic fallback. It also tests the graph routing a model tool call through its tool node.

Useful end-to-end prompts include a four-hour, ₹2,000 vegetarian afternoon in Indiranagar, a three-hour, ₹500 outdoor plan in Hyderabad, replacing a stop with a bookstore, and reducing the budget after a plan is displayed.

## Known limitations

- The MVP covers Indian cities, INR, and walking or driving. It excludes return travel and excludes travel to the first stop when the starting locality is unknown.
- Venue pricing, crowd levels, holiday hours, and drive fares can be incomplete or variable. The UI labels uncertain values. The Places vegetarian flag only verifies that vegetarian options are listed.
- Date-specific event discovery is outside this build. Live music availability is a venue attribute and cannot confirm a scheduled performance.
- Sessions expire after 12 minutes. Closing a session discards its server-side state. The basic rate limit is in memory per Vercel instance.

## AI coding tools

Codex adapted the official LiveKit Python and React starters, implemented the LangGraph tools and frontend, and exercised them with automated tests and live provider calls. Built-in image generation produced the original cover photo. [Cover asset](frontend/public/saturday-veranda.webp)

<details>
<summary>Cover image prompt</summary>

> Use case: photorealistic-natural. Asset type: decorative editorial cover photo for a Saturday itinerary planner in India. A quiet Bengaluru neighborhood cafe veranda with dark timber window frames and terracotta masonry at the left, small vintage wooden tables, soft foliage and deep green plants filtered with late-afternoon sunlight, inviting warm golden highlights, an open doorway in the upper middle. Horizontal 3:2 editorial travel photography, natural details and subtle film grain, compose the distinct plants, windows and terrace along the middle so a wide 3:1 crop also works. No visible people or writing, logos, signs, or watermark. No overlay or typography.

</details>

[LiveKit Python starter](https://github.com/livekit-examples/agent-starter-python) · [LiveKit React starter](https://github.com/livekit-examples/agent-starter-react)
