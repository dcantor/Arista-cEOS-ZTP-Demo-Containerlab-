import { useEffect, useRef, useState } from "react";

export type SSEMessage =
  | { type: "event"; event: { id: number; ts: string; host: string; event: string; ip: string | null } }
  | { type: "config_updated"; host: string }
  | { type: "reprovision"; host: string };

export function useSSE(onMessage: (m: SSEMessage) => void) {
  const [connected, setConnected] = useState(false);
  const ref = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource("/api/stream");
    ref.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        onMessage(data);
      } catch {
        // ignore malformed
      }
    };
    return () => {
      es.close();
      ref.current = null;
    };
    // intentionally not depending on onMessage to avoid reconnect storms
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { connected };
}
