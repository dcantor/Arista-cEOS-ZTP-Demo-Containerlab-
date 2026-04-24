import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ZtpEvent } from "../api";
import { useSSE } from "../hooks/useSSE";
import StatusPill from "../components/StatusPill";

export default function Events() {
  const [events, setEvents] = useState<ZtpEvent[]>([]);
  const refresh = () => api.events(500).then(setEvents).catch(() => {});

  useEffect(() => { refresh(); }, []);
  useSSE(() => refresh());

  return (
    <section>
      <h1 className="text-xl font-semibold mb-4">ZTP event log</h1>
      <div className="rounded border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400 text-left">
            <tr><th className="px-3 py-2">Time</th><th className="px-3 py-2">Host</th><th className="px-3 py-2">Event</th><th className="px-3 py-2">From IP</th></tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id} className="border-t border-slate-800">
                <td className="px-3 py-2 mono text-xs">{e.ts}</td>
                <td className="px-3 py-2 mono">
                  <Link to={`/devices/${e.host}`} className="text-sky-300 hover:underline">{e.host}</Link>
                </td>
                <td className="px-3 py-2"><StatusPill event={e.event} /></td>
                <td className="px-3 py-2 mono">{e.ip ?? "-"}</td>
              </tr>
            ))}
            {events.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-6 text-center text-slate-500">No events yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
