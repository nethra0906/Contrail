import { Nav } from "@/components/ui/Nav";
import { ScorecardView } from "@/components/scorecard/ScorecardView";

export default function ScorecardPage() {
  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-base)" }}>
      <Nav />
      <main className="flex-1 overflow-y-auto">
        <ScorecardView />
      </main>
    </div>
  );
}
