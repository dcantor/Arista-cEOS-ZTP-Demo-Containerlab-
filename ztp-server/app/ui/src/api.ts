export type Device = {
  name: string;
  container: string | null;
  status: string;
  mac: string | null;
  ip: string | null;
  source?: "topology" | "managed" | "absent";
  eos_image?: string | null;
  first_seen?: string;
  last_seen?: string;
  last_event?: string;
  event_count?: number;
};

export type EosImage = {
  filename: string;
  size: number;
  mtime: number;
};

export type ManagedDevice = {
  name: string;
  mac: string;
  mgmt_ip: string;
  created_at: string;
};

export type Lease = {
  mac: string;
  ip: string;
  hostname: string | null;
  expiry_epoch: number;
};

export type Pool = {
  range_start: string;
  range_end: string;
  total: number;
  used: number;
  free: number;
  leases: Lease[];
};

export type ZtpEvent = {
  id: number;
  ts: string;
  host: string;
  event: string;
  ip: string | null;
};

export type ConfigEntry = {
  host: string;
  filename: string;
  size: number;
  mtime: number;
};

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

export const api = {
  devices: () => j<Device[]>("/api/devices"),
  leases: () => j<Pool>("/api/leases"),
  events: (limit = 200) => j<ZtpEvent[]>(`/api/events?limit=${limit}`),
  configs: () => j<ConfigEntry[]>("/api/configs"),
  config: (host: string) => j<{ host: string; content: string }>(`/api/configs/${host}`),
  saveConfig: (host: string, content: string) =>
    j<{ ok: boolean; size: number }>(`/api/configs/${host}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  applyConfig: (host: string) =>
    j<{ node: string; status: string; source_url: string }>(
      `/api/devices/${host}/apply-config`,
      { method: "POST" },
    ),
  managedDevices: () => j<ManagedDevice[]>("/api/managed-devices"),
  addManagedDevice: (name: string, mac: string, mgmt_ip: string) =>
    j<ManagedDevice>("/api/managed-devices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, mac, mgmt_ip }),
    }),
  deleteManagedDevice: (name: string) =>
    j<{ ok: boolean; name: string }>(`/api/managed-devices/${name}`, {
      method: "DELETE",
    }),
  updateManagedDevice: (name: string, mac: string, mgmt_ip: string) =>
    j<ManagedDevice>(`/api/managed-devices/${name}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mac, mgmt_ip }),
    }),
  eosImages: () => j<EosImage[]>("/api/eos-images"),
  setDeviceEosImage: (host: string, eos_image: string | null) =>
    j<{ host: string; eos_image: string | null }>(
      `/api/devices/${host}/eos-image`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ eos_image }),
      },
    ),
};
