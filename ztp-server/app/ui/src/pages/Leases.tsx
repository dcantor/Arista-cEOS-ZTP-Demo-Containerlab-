import { useEffect, useState } from "react";
import { api, Pool } from "../api";
import { useSSE } from "../hooks/useSSE";

export default function Leases() {
  const [pool, setPool] = useState<Pool | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => api.leases().then(setPool).catch((e) => setError(String(e)));
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);
  useSSE(() => refresh());

  return (
    <section>
      <h1 className="text-xl font-semibold mb-4">DHCP pool</h1>
      {error && <div className="text-rose-400 mb-3">{error}</div>}
      {pool && (
        <>
          <div className="grid grid-cols-3 gap-4 mb-6">
            <Stat label="Range">{pool.range_start} – {pool.range_end}</Stat>
            <Stat label="Used / Total">{pool.used} / {pool.total}</Stat>
            <Stat label="Free">{pool.free}</Stat>
          </div>
          <h2 className="text-sm uppercase tracking-wide text-slate-400 mb-2">Active leases</h2>
          <div className="rounded border border-slate-800 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-900 text-slate-400 text-left">
                <tr>
                  <th className="px-3 py-2">IP</th>
                  <th className="px-3 py-2">MAC</th>
                  <th className="px-3 py-2">Hostname</th>
                  <th className="px-3 py-2">Expires</th>
                </tr>
              </thead>
              <tbody>
                {pool.leases.map((l) => (
                  <tr key={`${l.ip}-${l.mac}`} className="border-t border-slate-800">
                    <td className="px-3 py-2 mono">{l.ip}</td>
                    <td className="px-3 py-2 mono">{l.mac}</td>
                    <td className="px-3 py-2 mono">{l.hostname ?? "-"}</td>
                    <td className="px-3 py-2 mono text-xs text-slate-400">
                      {new Date(l.expiry_epoch * 1000).toISOString()}
                    </td>
                  </tr>
                ))}
                {pool.leases.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-6 text-center text-slate-500">No active leases.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900/50 p-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-lg mono">{children}</div>
    </div>
  );
}
