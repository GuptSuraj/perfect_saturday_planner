import { Planner } from "../components/planner";
import type { Viewport } from "next";

export const viewport: Viewport = { interactiveWidget: "resizes-content", viewportFit: "cover" };

export default function Home() {
  return <Planner />;
}
