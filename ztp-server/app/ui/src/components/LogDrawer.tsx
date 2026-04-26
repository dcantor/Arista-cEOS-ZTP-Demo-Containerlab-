import { MouseEvent, useEffect, useRef, useState } from "react";

const MAX_LINES = 5000;

export type View = { host: string };

const viewKey = (v: View) => v.host;

/**
 * Bottom drawer with one tab per open device VM-console view. Sessions
 * stay mounted even when not visible, so the EventSource keeps
 * streaming and switching tabs preserves scroll + history.
 */
export default function LogDrawer({
  views,
  activeIdx,
  onActivate,
  onClose,
  onCloseAll,
}: {
  views: View[];
  activeIdx: number;
  onActivate: (idx: number) => void;
  onClose: (idx: number) => void;
  onCloseAll: () => void;
}) {
  if (views.length === 0) return null;
  return (
    <div className="fixed inset-x-0 bottom-0 h-[45vh] bg-slate-950 border-t border-slate-800 z-50 flex flex-col shadow-2xl">
      <div className="flex items-stretch gap-1 px-2 py-1 border-b border-slate-800 bg-slate-900 overflow-x-auto">
        {views.map((v, i) => (
          <Tab
            key={viewKey(v)}
            view={v}
            active={i === activeIdx}
            onClick={() => onActivate(i)}
            onClose={(e) => {
              e.stopPropagation();
              onClose(i);
            }}
          />
        ))}
        <div className="flex-1" />
        <button
          onClick={onCloseAll}
          className="px-2 py-1 rounded text-xs bg-rose-600 hover:bg-rose-500"
          title="Close all tabs"
        >
          ✕ close all
        </button>
      </div>
      <div className="flex-1 relative">
        {views.map((v, i) => (
          <Session key={viewKey(v)} view={v} visible={i === activeIdx} />
        ))}
      </div>
    </div>
  );
}

function Tab({
  view,
  active,
  onClick,
  onClose,
}: {
  view: View;
  active: boolean;
  onClick: () => void;
  onClose: (e: MouseEvent) => void;
}) {
  return (
    <div
      onClick={onClick}
      className={`group flex items-center gap-2 px-3 py-1 rounded-t cursor-pointer text-xs select-none ${
        active
          ? "bg-slate-950 text-white border-x border-t border-slate-700"
          : "bg-slate-800/80 text-slate-400 hover:bg-slate-700"
      }`}
    >
      <span className="mono">{view.host}</span>
      <span className="text-[10px] mono text-violet-300">VM</span>
      <button
        onClick={onClose}
        className="ml-1 text-slate-500 hover:text-rose-300"
        title="Close this tab"
      >
        ×
      </button>
    </div>
  );
}

function Session({ view, visible }: { view: View; visible: boolean }) {
  const [lines, setLines] = useState<string[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  const streamUrl = `/api/devices/${view.host}/console/stream`;

  useEffect(() => {
    setLines([]);
    setConnected(false);
    const es = new EventSource(streamUrl);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (ev) => {
      setLines((l) => {
        const next =
          l.length >= MAX_LINES ? l.slice(-MAX_LINES + 1) : l.slice();
        next.push(ev.data);
        return next;
      });
    };
    return () => {
      es.close();
    };
  }, [streamUrl]);

  useEffect(() => {
    if (visible && !paused && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [lines, paused, visible]);

  return (
    <div
      className={`absolute inset-0 flex flex-col ${visible ? "" : "hidden"}`}
    >
      <div className="flex items-center gap-3 px-3 py-1 border-b border-slate-800 bg-slate-900/60">
        <span className="text-xs mono">{view.host} · VM Console</span>
        <span
          className={`text-[10px] mono ${
            connected ? "text-emerald-400" : "text-slate-500"
          }`}
        >
          {connected ? "● streaming" : "○ disconnected"}
        </span>
        <span className="text-xs text-slate-500">{lines.length} lines</span>
        {paused && (
          <span className="text-xs text-amber-400">autoscroll paused</span>
        )}
        <div className="flex-1" />
        <button
          onClick={() => setPaused((p) => !p)}
          className="px-2 py-0.5 rounded text-xs bg-slate-800 hover:bg-slate-700"
          title="Toggle autoscroll"
        >
          {paused ? "▶ resume" : "❚❚ pause"}
        </button>
        <button
          onClick={() => setLines([])}
          className="px-2 py-0.5 rounded text-xs bg-slate-800 hover:bg-slate-700"
        >
          clear
        </button>
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
