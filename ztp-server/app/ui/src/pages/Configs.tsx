import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ConfigEntry } from "../api";

export default function Configs() {
  const [configs, setConfigs] = useState<ConfigEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.configs().then(setConfigs).catch((e) => setError(String(e))); }, []);

  return (
    <section>
      <h1 className="text-xl font-semibold mb-4">Per-host configs</h1>
      {error && <div className="text-rose-400 mb-3">{error}</div>}
      <div className="rounded border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400 text-left">
            <tr><th className="px-3 py-2">Host</th><th className="px-3 py-2">File</th><th className="px-3 py-2">Size</th><th className="px-3 py-2">Modified</th><th /></tr>
          </thead>
          <tbody>
            {configs.map((c) => (
              <tr key={c.host} className="border-t border-slate-800 hover:bg-slate-900/50">
                <td className="px-3 py-2 mono">{c.host}</td>
                <td className="px-3 py-2 mono text-xs text-slate-400">{c.filename}</td>
                <td className="px-3 py-2">{c.size} B</td>
                <td className="px-3 py-2 mono text-xs text-slate-400">{new Date(c.mtime * 1000).toISOString()}</td>
                <td className="px-3 py-2 text-right">
                  <Link to={`/edit/${c.host}`} className="text-sky-300 hover:underline">edit</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
