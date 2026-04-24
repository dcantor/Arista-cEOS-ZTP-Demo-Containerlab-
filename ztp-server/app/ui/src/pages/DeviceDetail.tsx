import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Device, ZtpEvent } from "../api";
import { useSSE } from "../hooks/useSSE";
import StatusPill from "../components/StatusPill";

export default function DeviceDetail() {
  const { host = "" } = useParams();
  const [device, setDevice] = useState<Device | null>(null);
  const [events, setEvents] = useState<ZtpEvent[]>([]);
  const [config, setConfig] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = async () => {
    const [devs, ev] = await Promise.all([api.devices(), api.events(500)]);
    setDevice(devs.find((d) => d.name === host) ?? null);
    setEvents(ev.filter((e) => e.host === host));
    try {
      setConfig((await api.config(host)).content);
    } catch {
      setConfig(null);
    }
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [host]);
  useSSE((m) => {
    if (m.type === "event" && m.event.host === host) refresh();
    if (m.type === "config_updated" && m.host === host) refresh();
  });

  const apply = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.applyConfig(host);
      setMsg(`Applied config from ${r.source_url} (running + startup updated, no reboot).`);
      refresh();
    } catch (e) {
      setMsg(`Failed: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold mono">{host}</h1>
        <Link to="/" className="text-sm text-sky-300 hover:underline">← all devices</Link>
      </div>
      {device && (
        <div className="grid grid-cols-2 gap-4 mb-6">
          <Card label="Container status">{device.status}</Card>
          <Card label="ZTP last event"><StatusPill event={device.last_event} /></Card>
          <Card label="Mgmt MAC"><span className="mono">{device.mac ?? "-"}</span></Card>
          <Card label="Mgmt IP (Docker)"><span className="mono">{device.ip ?? "-"}</span></Card>
          <Card label="First seen"><span className="mono text-xs">{device.first_seen ?? "-"}</span></Card>
          <Card label="Last seen"><span className="mono text-xs">{device.last_seen ?? "-"}</span></Card>
        </div>
      )}

      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={apply}
          disabled={busy || !device?.container}
          className="px-3 py-2 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-sm"
          title="Push the served per-host config into running + startup via Cli configure replace. No reboot."
        >
          {busy ? "Applying…" : "Apply config (live)"}
        </button>
        <Link to={`/edit/${host}`} className="px-3 py-2 rounded border border-slate-700 hover:bg-slate-800 text-sm">
          Edit config
        </Link>
        {msg && <span className="text-sm text-slate-300">{msg}</span>}
      </div>

      <h2 className="text-sm uppercase tracking-wide text-slate-400 mb-2">Event timeline</h2>
      <div className="rounded border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400 text-left">
            <tr><th className="px-3 py-2">Time</th><th className="px-3 py-2">Event</th><th className="px-3 py-2">From IP</th></tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id} className="border-t border-slate-800">
                <td className="px-3 py-2 mono text-xs">{e.ts}</td>
                <td className="px-3 py-2"><StatusPill event={e.event} /></td>
                <td className="px-3 py-2 mono">{e.ip ?? "-"}</td>
              </tr>
            ))}
            {events.length === 0 && (
              <tr><td colSpan={3} className="px-3 py-6 text-center text-slate-500">No events recorded for this host.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {config !== null && (
        <>
          <h2 className="text-sm uppercase tracking-wide text-slate-400 mt-6 mb-2">Served config (read-only)</h2>
          <pre className="mono text-xs bg-slate-900 border border-slate-800 rounded p-3 overflow-x-auto whitespace-pre-wrap">{config}</pre>
        </>
      )}
    </section>
  );
}

function Card({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900/50 p-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1">{children}</div>
    </div>
  );
}
