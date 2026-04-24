import { FormEvent, useEffect, useState } from "react";
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
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">ZTP</th>
              <th className="px-3 py-2">MAC</th>
              <th className="px-3 py-2">Mgmt IP</th>
              <th className="px-3 py-2">Last seen</th>
              <th className="px-3 py-2">Events</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => (
              <DeviceRow key={d.name} d={d} onView={setViewerHost} onChange={refresh} />
            ))}
            {devices.length === 0 && (
              <tr><td colSpan={9} className="px-3 py-6 text-center text-slate-500">No devices yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <AddDeviceForm onAdded={refresh} />

      {viewerHost && (
        <>
          <div className="h-[45vh]" aria-hidden />
          <LogDrawer host={viewerHost} onClose={() => setViewerHost(null)} />
        </>
      )}
    </section>
  );
}

function DeviceRow({ d, onView, onChange }: {
  d: Device; onView: (h: string) => void; onChange: () => void;
}) {
  const isManaged = d.source === "managed";
  const remove = async () => {
    if (!confirm(`Remove managed device ${d.name}? This drops the dnsmasq reservation.`)) return;
    try { await api.deleteManagedDevice(d.name); onChange(); }
    catch (e) { alert(`Failed: ${e}`); }
  };
  const sourceBadge = (
    <span className={`px-2 py-0.5 text-[10px] rounded mono border ${
      d.source === "topology" ? "bg-sky-500/10 text-sky-300 border-sky-500/40"
      : d.source === "managed" ? "bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/40"
      : "bg-slate-700/30 text-slate-400 border-slate-600"
    }`}>{d.source ?? "—"}</span>
  );

  return (
    <tr className="border-t border-slate-800 hover:bg-slate-900/50">
      <td className="px-3 py-2 mono">
        <Link to={`/devices/${d.name}`} className="text-sky-300 hover:underline">{d.name}</Link>
      </td>
      <td className="px-3 py-2">{sourceBadge}</td>
      <td className="px-3 py-2">{d.status}</td>
      <td className="px-3 py-2"><StatusPill event={d.last_event} /></td>
      <td className="px-3 py-2 mono">{d.mac ?? "-"}</td>
      <td className="px-3 py-2 mono">{d.ip ?? "-"}</td>
      <td className="px-3 py-2 mono text-xs text-slate-400">{d.last_seen ?? "-"}</td>
      <td className="px-3 py-2">{d.event_count ?? 0}</td>
      <td className="px-3 py-2 text-right">
        <div className="flex justify-end gap-2">
          {d.container && (
            <button
              onClick={() => onView(d.name)}
              className="px-2 py-1 rounded text-xs bg-emerald-600 hover:bg-emerald-500"
              title="Stream live docker logs for this device"
            >
              Live ZTP Viewer
            </button>
          )}
          {isManaged && (
            <button
              onClick={remove}
              className="px-2 py-1 rounded text-xs bg-rose-600 hover:bg-rose-500"
              title="Remove this device from dnsmasq"
            >
              Delete
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function AddDeviceForm({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [mac, setMac] = useState("");
  const [ip, setIp] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      await api.addManagedDevice(name.trim(), mac.trim(), ip.trim());
      setMsg(`Added ${name}. dnsmasq reloaded.`);
      setName(""); setMac(""); setIp("");
      onAdded();
    } catch (e) {
      setMsg(`Failed: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="mt-4">
        <button
          onClick={() => setOpen(true)}
          className="px-3 py-2 rounded bg-sky-600 hover:bg-sky-500 text-sm"
        >
          + Add device
        </button>
        {msg && <span className="ml-3 text-sm text-slate-300">{msg}</span>}
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="mt-4 rounded border border-slate-800 bg-slate-900/40 p-3">
      <div className="text-sm font-semibold mb-2">Register a new device</div>
      <p className="text-xs text-slate-500 mb-3">
        Adds a dnsmasq reservation: when a device with this MAC DHCPs in, it gets the
        chosen mgmt IP and is pointed at <code className="mono">/ztp/&lt;name&gt;.sh</code>.
        A per-host script and an empty config skeleton are auto-created.
      </p>
      <div className="grid grid-cols-3 gap-3 items-end">
        <label className="block text-xs">
          <div className="text-slate-400 mb-1">Name</div>
          <input
            value={name} onChange={(e) => setName(e.target.value)}
            placeholder="leaf3" required pattern="[a-zA-Z0-9_-]+"
            className="w-full mono px-2 py-1 bg-slate-950 border border-slate-700 rounded"
          />
        </label>
        <label className="block text-xs">
          <div className="text-slate-400 mb-1">MAC</div>
          <input
            value={mac} onChange={(e) => setMac(e.target.value)}
            placeholder="00:1c:73:01:02:03" required
            className="w-full mono px-2 py-1 bg-slate-950 border border-slate-700 rounded"
          />
        </label>
        <label className="block text-xs">
          <div className="text-slate-400 mb-1">Mgmt IP</div>
          <input
            value={ip} onChange={(e) => setIp(e.target.value)}
            placeholder="172.30.0.105" required
            className="w-full mono px-2 py-1 bg-slate-950 border border-slate-700 rounded"
          />
        </label>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button
          type="submit" disabled={busy}
          className="px-3 py-2 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-sm"
        >{busy ? "Adding…" : "Add device"}</button>
        <button
          type="button" onClick={() => { setOpen(false); setMsg(null); }}
          className="px-3 py-2 rounded border border-slate-700 hover:bg-slate-800 text-sm"
        >Cancel</button>
        {msg && <span className="text-sm text-slate-300">{msg}</span>}
      </div>
    </form>
  );
}
