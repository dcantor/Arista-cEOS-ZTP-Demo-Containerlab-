import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";

export default function ConfigEditor() {
  const { host = "" } = useParams();
  const [content, setContent] = useState<string>("");
  const [original, setOriginal] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.config(host).then((c) => { setContent(c.content); setOriginal(c.content); })
      .catch((e) => setError(String(e)));
  }, [host]);

  const dirty = content !== original;

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.saveConfig(host, content);
      setOriginal(content);
      setMsg(`Saved (${r.size} bytes).`);
    } catch (e) { setMsg(`Failed: ${e}`); }
    finally { setBusy(false); }
  };

  const saveAndApply = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.saveConfig(host, content);
      setOriginal(content);
      const r = await api.applyConfig(host);
      setMsg(`Saved and applied (${r.source_url}). Running + startup updated, no reboot.`);
    } catch (e) { setMsg(`Failed: ${e}`); }
    finally { setBusy(false); }
  };

  return (
    <section>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold mono">configs/{host}.cfg</h1>
        <Link to="/configs" className="text-sm text-sky-300 hover:underline">← all configs</Link>
      </div>
      {error && <div className="text-rose-400 mb-3">{error}</div>}
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
        className="w-full h-[60vh] mono text-xs bg-slate-900 border border-slate-800 rounded p-3"
      />
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={save}
          disabled={busy || !dirty}
          className="px-3 py-2 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-sm"
        >
          {busy ? "..." : dirty ? "Save" : "Saved"}
        </button>
        <button
          onClick={saveAndApply}
          disabled={busy}
          className="px-3 py-2 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-sm"
          title="Save the file, then push it live via Cli configure replace (no reboot)"
        >
          Save and apply
        </button>
        {msg && <span className="text-sm text-slate-300">{msg}</span>}
      </div>
    </section>
  );
}
