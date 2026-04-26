import { useEffect, useRef, useState } from "react";

const MAX_LINES = 5000;

export default function LogDrawer({
  host,
  onClose,
  source = "logs",
}: {
  host: string;
  onClose: () => void;
  /** "logs" = wrapper docker logs (launcher output);
   *  "console" = VM serial console via QEMU telnet:5000 */
  source?: "logs" | "console";
}) {
  const [lines, setLines] = useState<string[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  const streamUrl = source === "console"
    ? `/api/devices/${host}/console/stream`
    : `/api/devices/${host}/logs/stream`;
  const title = source === "console" ? "VM Console" : "Live ZTP Viewer";

  useEffect(() => {
    setLines([]);
    setConnected(false);
    const es = new EventSource(streamUrl);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (ev) => {
      setLines((l) => {
        const next = l.length >= MAX_LINES ? l.slice(-MAX_LINES + 1) : l.slice();
        next.push(ev.data);
        return next;
      });
    };
    return () => { es.close(); };
  }, [streamUrl]);

  useEffect(() => {
    if (!paused && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [lines, paused]);

  return (
    <div className="fixed inset-x-0 bottom-0 h-[45vh] bg-slate-950 border-t border-slate-800 z-50 flex flex-col shadow-2xl">
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-800 bg-slate-900">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold mono">{title} · {host}</span>
          <span className={`text-[10px] mono ${connected ? "text-emerald-400" : "text-slate-500"}`}>
            {connected ? "● streaming" : "○ disconnected"}
          </span>
          <span className="text-xs text-slate-500">{lines.length} lines</span>
          {paused && <span className="text-xs text-amber-400">autoscroll paused</span>}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPaused((p) => !p)}
            className="px-2 py-1 rounded text-xs bg-slate-800 hover:bg-slate-700"
            title="Toggle autoscroll"
          >
            {paused ? "▶ resume" : "❚❚ pause"}
          </button>
          <button
            onClick={() => setLines([])}
            className="px-2 py-1 rounded text-xs bg-slate-800 hover:bg-slate-700"
          >
            clear
          </button>
          <button
            onClick={onClose}
            className="px-2 py-1 rounded text-xs bg-rose-600 hover:bg-rose-500"
          >
            ✕ close
          </button>
        </div>
      </div>
      <div
        ref={bodyRef}
        onWheel={(e) => {
          if (e.deltaY < 0) setPaused(true);
        }}
        className="flex-1 overflow-y-auto bg-black text-emerald-200 mono text-[11px] leading-snug p-2 whitespace-pre-wrap"
      >
        {lines.length === 0 ? (
          <span className="text-slate-500">waiting for output…</span>
        ) : (
          lines.join("\n")
        )}
      </div>
    </div>
  );
}
