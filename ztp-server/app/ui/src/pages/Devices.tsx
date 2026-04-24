import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Device } from "../api";
import { useSSE } from "../hooks/useSSE";
import StatusPill from "../components/StatusPill";
import LogDrawer from "../components/LogDrawer";

export default function Devices() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [viewerHost, setViewerHost] = useState<string | null>(null);

  const refresh = () => api.devices().then(setDevices).catch((e) => setError(String(e)));

  useEffect(() => { refresh(); }, []);
  const { connected } = useSSE(() => refresh());

  return (
    <section>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Device inventory</h1>
        <span className={`text-xs mono ${connected ? "text-emerald-400" : "text-slate-500"}`}>
          {connected ? "● live" : "○ disconnected"}
        </span>
      </div>
      {error && <div className="text-rose-400 mb-3">{error}</div>}
      <div className="overflow-x-auto rounded border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400 text-left">
            <tr>
              <th className="px-3 py-2">Node</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">ZTP</th>
              <th className="px-3 py-2">Mgmt MAC</th>
              <th className="px-3 py-2">Mgmt IP (Docker)</th>
              <th className="px-3 py-2">Last seen</th>
              <th className="px-3 py-2">Events</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => (
              <tr key={d.name} className="border-t border-slate-800 hover:bg-slate-900/50">
                <td className="px-3 py-2 mono">
                  <Link to={`/devices/${d.name}`} className="text-sky-300 hover:underline">{d.name}</Link>
                </td>
                <td className="px-3 py-2">{d.status}</td>
                <td className="px-3 py-2"><StatusPill event={d.last_event} /></td>
                <td className="px-3 py-2 mono">{d.mac ?? "-"}</td>
                <td className="px-3 py-2 mono">{d.ip ?? "-"}</td>
                <td className="px-3 py-2 mono text-xs text-slate-400">{d.last_seen ?? "-"}</td>
                <td className="px-3 py-2">{d.event_count ?? 0}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => setViewerHost(d.name)}
                    disabled={!d.container}
                    className="px-2 py-1 rounded text-xs bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40"
                    title="Stream live docker logs for this device"
                  >
                    Live ZTP Viewer
                  </button>
                </td>
              </tr>
            ))}
            {devices.length === 0 && (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-500">No devices yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {viewerHost && (
        <>
          {/* spacer so the table doesn't sit under the drawer */}
          <div className="h-[45vh]" aria-hidden />
          <LogDrawer host={viewerHost} onClose={() => setViewerHost(null)} />
        </>
      )}
    </section>
  );
}
