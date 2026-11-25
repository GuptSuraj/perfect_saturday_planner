// Adapted from livekit-examples/agent-starter-react (Apache-2.0).
import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import {
  AccessToken, RoomAgentDispatch, RoomConfiguration, TrackSource,
} from "livekit-server-sdk";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store" };

function error(message: string, status: number) {
  return NextResponse.json({ error: message }, { status, headers });
}

export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  const scheme = request.headers.get("x-forwarded-proto") === "https" ? "https" : "http";
  // Next can normalize the Request URL to an internal hostname. The request
  // Host header is the origin the browser actually reached.
  if (origin && origin !== `${scheme}://${host}`) {
    return error("Start a session from the planner page.", 403);
  }
  const { LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL } = process.env;
  if (!LIVEKIT_API_KEY || !LIVEKIT_API_SECRET || !LIVEKIT_URL) {
    return error("The planner service is currently unavailable.", 503);
  }

  const identity = `guest-${randomUUID()}`;
  const roomName = `saturday-${randomUUID()}`;
  const token = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
    identity, name: "Guest", ttl: "5m",
  });
  token.addGrant({
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishSources: [TrackSource.MICROPHONE],
    canPublishData: true,
    canSubscribe: true,
  });
  token.roomConfig = new RoomConfiguration({
    emptyTimeout: 30,
    departureTimeout: 15,
    maxParticipants: 2,
    agents: [new RoomAgentDispatch({
      agentName: "perfect-saturday",
      metadata: JSON.stringify({ identity }),
    })],
  });

  return NextResponse.json({ serverUrl: LIVEKIT_URL, participantToken: await token.toJwt() }, { headers });
}
