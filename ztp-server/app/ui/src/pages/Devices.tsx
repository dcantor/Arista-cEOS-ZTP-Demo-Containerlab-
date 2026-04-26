import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Device, EosImage } from "../api";
import { useSSE } from "../hooks/useSSE";
import StatusPill from "../components/StatusPill";
import LogDrawer, { View, ViewKind } from "../components/LogDrawer";

export default function Devices() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [images, setImages] = useState<EosImage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [views, setViews] = useState<View[]>([]);
  const [activeIdx, setActiveIdx] = useState<number>(0);

  const openView = (host: string, kind: ViewKind) => {
    setViews((vs) => {
      const i = vs.findIndex((v) => v.host === host && v.kind === kind);
      if (i >= 0) {
        setActiveIdx(i);
        return vs;
      }
      setActiveIdx(vs.length);
      return [...vs, { host, kind }];
    });
  };
  const closeView = (idx: number) => {
    setViews((vs) => {
      const next = vs.filter((_, i) => i !== idx);
      setActiveIdx((cur) => {
        if (next.length === 0) return 0;
        if (cur === idx) return Math.min(idx, next.length - 1);
        if (cur > idx) return cur - 1;
        return cur;
      });
      return next;
    });
  };
  const closeAllViews = () => {
    setViews([]);
    setActiveIdx(0);
  };

  const refresh = () => {
    api.devices().then(setDevices).catch((e) => setError(String(e)));
    api.eosImages().then(setImages).catch(() => {/* non-fatal */});
  };
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
              <th className="px-3 py-2">Vendor</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">VM</th>
              <th className="px-3 py-2">ZTP</th>
              <th className="px-3 py-2">MAC</th>
              <th className="px-3 py-2">Mgmt IP</th>
              <th className="px-3 py-2">EOS image (ZTP)</th>
              <th className="px-3 py-2">Last seen</th>
              <th className="px-3 py-2">Events</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {devices.map((d) => (
              <DeviceRow key={d.name} d={d} images={images}
                onView={openView} onChange={refresh} />
            ))}
            {devices.length === 0 && (
              <tr><td colSpan={12} className="px-3 py-6 text-center text-slate-500">No devices yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <AddDeviceForm onAdded={refresh} />

      {views.length > 0 && (
        <>
          {/* spacer so the table doesn't sit under the drawer */}
          <div className="h-[45vh]" aria-hidden />
          <LogDrawer
            views={views}
            activeIdx={activeIdx}
            onActivate={setActiveIdx}
            onClose={closeView}
            onCloseAll={closeAllViews}
          />
        </>
      )}
    </section>
  );
}

function DeviceRow({ d, images, onView, onChange }: {
  d: Device; images: EosImage[];
  onView: (host: string, kind: ViewKind) => void;
  onChange: () => void;
}) {
  const isManaged = d.source === "managed";
  const [editing, setEditing] = useState(false);
  const [macDraft, setMacDraft] = useState(d.mac ?? "");
  const [ipDraft, setIpDraft] = useState(d.ip ?? "");
  const [busy, setBusy] = useState(false);

  const startEdit = () => {
    setMacDraft(d.mac ?? "");
    setIpDraft(d.ip ?? "");
    setEditing(true);
  };
  const cancelEdit = () => setEditing(false);
  const saveEdit = async () => {
    setBusy(true);
    try {
      await api.updateManagedDevice(d.name, macDraft.trim(), ipDraft.trim());
      setEditing(false);
      onChange();
    } catch (e) { alert(`Failed: ${e}`); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!confirm(`Remove managed device ${d.name}? This drops the dnsmasq reservation.`)) return;
    try { await api.deleteManagedDevice(d.name); onChange(); }
    catch (e) { alert(`Failed: ${e}`); }
  };
  const onImageChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    try {
      await api.setDeviceEosImage(d.name, v === "" ? null : v);
      onChange();
    } catch (err) { alert(`Failed: ${err}`); }
  };
  const startVm = async () => {
    setBusy(true);
    try { await api.startVm(d.name); onChange(); }
    catch (e) { alert(`Failed: ${e}`); }
    finally { setBusy(false); }
  };
  const stopVm = async () => {
    if (!confirm(`Stop ${d.name}? The VM will shut down (config persists in overlay).`)) return;
    setBusy(true);
    try { await api.stopVm(d.name); onChange(); }
    catch (e) { alert(`Failed: ${e}`); }
    finally { setBusy(false); }
  };
  const vmStatus = d.vm_status ?? (d.container ? "stopped" : "unknown");
  const vmPill = (
    <span className={`inline-block px-2 py-0.5 text-xs border rounded mono ${
      vmStatus === "running" ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40"
      : vmStatus === "stopped" ? "bg-slate-700/40 text-slate-300 border-slate-600"
      : "bg-amber-500/20 text-amber-300 border-amber-500/40"
    }`}>{vmStatus}</span>
  );
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
      <td className="px-3 py-2">
        <span className={`px-2 py-0.5 text-[10px] rounded mono border ${
          d.vendor === "cisco"
            ? "bg-orange-500/10 text-orange-300 border-orange-500/40"
          : d.vendor === "nexus"
            ? "bg-violet-500/10 text-violet-300 border-violet-500/40"
            : "bg-cyan-500/10 text-cyan-300 border-cyan-500/40"
        }`}>{d.vendor ?? "arista"}</span>
      </td>
      <td className="px-3 py-2">{sourceBadge}</td>
      <td className="px-3 py-2">{d.status}</td>
      <td className="px-3 py-2">{vmPill}</td>
      <td className="px-3 py-2"><StatusPill event={d.last_event} /></td>
      <td className="px-3 py-2 mono">
        {editing && isManaged ? (
          <input
            value={macDraft}
            onChange={(e) => setMacDraft(e.target.value)}
            placeholder="aa:bb:cc:dd:ee:ff"
            className="mono text-xs bg-slate-950 border border-slate-700 rounded px-2 py-1 w-44"
          />
        ) : (d.mac ?? "-")}
      </td>
      <td className="px-3 py-2 mono">
        {editing && isManaged ? (
          <input
            value={ipDraft}
            onChange={(e) => setIpDraft(e.target.value)}
            placeholder="172.30.0.105"
            className="mono text-xs bg-slate-950 border border-slate-700 rounded px-2 py-1 w-32"
          />
        ) : (d.ip ?? "-")}
      </td>
      <td className="px-3 py-2">
        {(() => {
          const vend = d.vendor ?? "arista";
          // Only show images that match this device's vendor; managed
          // rows (no vendor known) get the full list.
          const opts = d.source === "managed"
            ? images
            : images.filter((img) => (img.vendor ?? "arista") === vend);
          return (
            <select
              value={d.eos_image ?? ""}
              onChange={onImageChange}
              className="mono text-xs bg-slate-900 border border-slate-700 rounded px-2 py-1"
              title={`Image to flash on next ZTP. (skip upgrade) leaves the running image alone. Filtered to ${vend}.`}
            >
              <option value="">(skip upgrade)</option>
              {opts.map((img) => (
                <option key={img.filename} value={img.filename}>{img.filename}</option>
              ))}
            </select>
          );
        })()}
      </td>
      <td className="px-3 py-2 mono text-xs text-slate-400">{d.last_seen ?? "-"}</td>
      <td className="px-3 py-2">{d.event_count ?? 0}</td>
      <td className="px-3 py-2 text-right">
        <div className="flex justify-end gap-2">
          {d.container && !editing && vmStatus !== "running" && (
            <button
              onClick={startVm}
              disabled={busy}
              className="px-2 py-1 rounded text-xs bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50"
              title="Start the vEOS VM in this wrapper"
            >
              {busy ? "…" : "Start"}
            </button>
          )}
          {d.container && !editing && vmStatus === "running" && (
            <button
              onClick={stopVm}
              disabled={busy}
              className="px-2 py-1 rounded text-xs bg-amber-600 hover:bg-amber-500 disabled:opacity-50"
              title="Stop the vEOS VM (graceful, then SIGKILL after 15 s)"
            >
              {busy ? "…" : "Stop"}
            </button>
          )}
          {d.container && !editing && (
            <button
              onClick={() => onView(d.name, "logs")}
              className="px-2 py-1 rounded text-xs bg-sky-600 hover:bg-sky-500"
              title="Stream the wrapper container's docker logs (launcher output)"
            >
              Live ZTP Viewer
            </button>
          )}
          {d.container && !editing && vmStatus === "running" && (
            <button
              onClick={() => onView(d.name, "console")}
              className="px-2 py-1 rounded text-xs bg-violet-600 hover:bg-violet-500"
              title="Stream the VM's serial console (BIOS/GRUB/OS boot, ZTP/POAP, EOS/IOS prompts). Requires the VM to be running."
            >
              VM Console
            </button>
          )}
          {isManaged && !editing && (
            <>
              <button
                onClick={startEdit}
                className="px-2 py-1 rounded text-xs bg-sky-600 hover:bg-sky-500"
                title="Edit MAC and mgmt IP for this device"
              >
                Edit
              </button>
              <button
                onClick={remove}
                className="px-2 py-1 rounded text-xs bg-rose-600 hover:bg-rose-500"
                title="Remove this device from dnsmasq"
              >
                Delete
              </button>
            </>
          )}
          {editing && (
            <>
              <button
                onClick={saveEdit}
                disabled={busy}
                className="px-2 py-1 rounded text-xs bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy ? "Saving…" : "Save"}
              </button>
              <button
                onClick={cancelEdit}
                disabled={busy}
                className="px-2 py-1 rounded text-xs border border-slate-700 hover:bg-slate-800"
              >
                Cancel
              </button>
            </>
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
