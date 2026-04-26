import { Link, NavLink, Route, Routes } from "react-router-dom";
import Devices from "./pages/Devices";
import DeviceDetail from "./pages/DeviceDetail";
import Configs from "./pages/Configs";
import ConfigEditor from "./pages/ConfigEditor";
import Leases from "./pages/Leases";
import Events from "./pages/Events";

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `px-3 py-2 rounded text-sm ${
          isActive ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800"
        }`
      }
    >
      {label}
    </NavLink>
  );
}

export default function App() {
  return (
    <div className="min-h-full flex flex-col">
      <header className="border-b border-slate-800 bg-slate-900">
        <div className="max-w-screen-2xl mx-auto px-4 py-3 flex items-center justify-between">
          <Link to="/" className="text-lg font-semibold tracking-tight">
            ZTP Server
          </Link>
          <nav className="flex gap-1 items-center">
            <NavItem to="/" label="Devices" />
            <NavItem to="/configs" label="Configs" />
            <NavItem to="/leases" label="DHCP" />
            <NavItem to="/events" label="Events" />
            <a
              href="/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="px-3 py-2 rounded text-sm text-slate-300 hover:bg-slate-800 inline-flex items-center gap-1"
              title="Open Swagger UI in a new tab"
            >
              API
              <span aria-hidden className="text-[10px] opacity-60">↗</span>
            </a>
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-screen-2xl w-full mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Devices />} />
          <Route path="/devices/:host" element={<DeviceDetail />} />
          <Route path="/configs" element={<Configs />} />
          <Route path="/edit/:host" element={<ConfigEditor />} />
          <Route path="/leases" element={<Leases />} />
          <Route path="/events" element={<Events />} />
        </Routes>
      </main>
    </div>
  );
}
