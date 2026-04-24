export default function StatusPill({ event }: { event?: string | null }) {
  const color =
    event === "done" ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
    : event === "start" ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
    : "bg-slate-700/40 text-slate-300 border-slate-600";
  const label = event === "done" ? "provisioned" : event === "start" ? "in-progress" : event ?? "unknown";
  return (
    <span className={`inline-block px-2 py-0.5 text-xs border rounded mono ${color}`}>{label}</span>
  );
}
