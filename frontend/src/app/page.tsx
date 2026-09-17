import { AircraftPanel } from "@/components/map/AircraftPanel";
import { LiveMap } from "@/components/map/LiveMap";
import { Nav } from "@/components/ui/Nav";

export default function Home() {
  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-base)" }}>
      <Nav />
      <main className="relative flex flex-1 overflow-hidden">
        <div className="relative flex-1">
          <LiveMap />
        </div>
        <AircraftPanel />
      </main>
      <footer
        className="border-t px-4 py-1.5 text-[11px]"
        style={{ borderColor: "var(--border-subtle)", background: "var(--bg-base)", color: "var(--text-tertiary)" }}
      >
        Aircraft position data (c) adsb.lol contributors, licensed under ODbL. Coverage: continental United States.
      </footer>
    </div>
  );
}
