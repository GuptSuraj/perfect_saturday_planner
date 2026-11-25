import { z } from "zod";

export const traceEvent = z.object({
  id: z.string(),
  tool: z.string(),
  status: z.enum(["running", "success", "warning"]),
  description: z.string(),
  duration_ms: z.number().optional(),
});

export const preferences = z.object({
  city: z.string().optional(), budget: z.number().optional(),
  available_minutes: z.number().optional(), mood: z.string().optional(),
  interests: z.array(z.string()).optional(), constraints: z.array(z.string()).optional(),
  locality: z.string().optional(), start_time: z.string().optional(),
  people: z.number().optional(), transport: z.enum(["walk", "drive"]).optional(),
  dietary: z.enum(["vegetarian_options", "vegetarian_only", "requires_confirmation"]).optional(),
  environment: z.enum(["indoors", "outdoors"]).optional(),
  accessibility_required: z.boolean().optional(),
  target_date: z.string().optional(),
});

export const preferenceUpdate = z.object({
  preferences,
  revision_needed: z.boolean(),
});

const transit = z.object({
  destination: z.string(),
  distance_metres: z.number().nullable(),
  duration_minutes: z.number().nullable(),
  mode: z.enum(["walk", "drive"]),
  source: z.string(),
  verification_status: z.enum(["route_estimate", "approximate", "unavailable"]),
});

export const itinerary = z.object({
  itinerary_id: z.string(),
  version: z.number().int().positive(),
  status: z.enum(["draft", "accepted"]),
  title: z.string(),
  city: z.string(),
  date: z.string(),
  currency: z.literal("INR"),
  budget: z.number(),
  estimated_cost_min: z.number(),
  estimated_cost_max: z.number(),
  transport_cost_min: z.number(),
  transport_cost_max: z.number(),
  contingency: z.number(),
  remaining_budget: z.number(),
  duration_minutes: z.number(),
  travel_minutes: z.number(),
  buffer_minutes: z.number(),
  starting_leg: transit.nullable(),
  activities: z.array(z.object({
    place_id: z.string().nullable(),
    name: z.string(),
    address: z.string().nullable(),
    start_time: z.string(),
    end_time: z.string(),
    duration_minutes: z.number(),
    description: z.string(),
    reason: z.string(),
    estimated_cost_min: z.number(),
    estimated_cost_max: z.number(),
    cost_source: z.string(),
    rating: z.number().nullable(),
    rating_count: z.number().nullable(),
    maps_url: z.url({ protocol: /^https$/ }).nullable(),
    travel_to_next: transit.nullable(),
    verification_status: z.enum(["verified_place", "unverified"]),
    warnings: z.array(z.string()),
    attributions: z.array(z.string()).default([]),
  })).min(1),
  explanation: z.string(),
  weather_summary: z.string().nullable(),
  assumptions: z.array(z.string()),
  warnings: z.array(z.string()),
  validation_status: z.literal("passed"),
  verification_status: z.enum(["grounded", "unverified"]),
});

export type Itinerary = z.infer<typeof itinerary>;
export type Transit = z.infer<typeof transit>;
export type TraceEvent = z.infer<typeof traceEvent>;
export type Preferences = z.infer<typeof preferences>;
export type Language = "en" | "hi";
export const chatMessage = z.object({
  id: z.string(),
  role: z.enum(["assistant", "user"]),
  text: z.string(),
  timestamp: z.number(),
});
export type Message = z.infer<typeof chatMessage>;
export type ChatItem = Message | { id: string; role: "plan"; timestamp: number };
