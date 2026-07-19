import {
  Activity,
  BriefcaseBusiness,
  ClipboardList,
  FileStack,
  Gauge,
  HeartPulse,
  FlaskConical,
  Menu,
  Settings2,
  ShieldCheck,
  Webhook,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router-dom";

const navigation = [
  { to: "/", label: "Overview", icon: Gauge, end: true },
  { to: "/lab", label: "Test Lab", icon: FlaskConical },
  { to: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { to: "/projects", label: "Projects / Documents", icon: FileStack },
  { to: "/configuration", label: "Configuration", icon: Settings2 },
  { to: "/metrics", label: "Metrics", icon: Activity },
  { to: "/quality", label: "Evidence Quality", icon: ShieldCheck },
  { to: "/audit", label: "Audit", icon: ClipboardList },
  { to: "/webhooks", label: "Webhooks", icon: Webhook },
  { to: "/health", label: "System Health", icon: HeartPulse },
];

export function OperatorNavigation() {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <button
        className="mobile-menu-button"
        type="button"
        aria-label={open ? "Close navigation" : "Open navigation"}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {open ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
      </button>
      {open && (
        <button
          className="nav-scrim"
          type="button"
          aria-label="Close navigation"
          onClick={() => setOpen(false)}
        />
      )}
      <aside
        className={`sidebar ${open ? "sidebar--open" : ""} ${expanded ? "sidebar--expanded" : ""}`}
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
        onFocusCapture={() => setExpanded(true)}
      >
        <div className="brand">
          <div className="brand__mark" aria-hidden="true">
            R
          </div>
          <div>
            <strong>RAG Builder</strong>
            <span>Operator Console</span>
          </div>
        </div>
        <nav aria-label="Operator console">
          {navigation.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={label}
              onClick={() => {
                setOpen(false);
                setExpanded(false);
              }}
              className={({ isActive }) => `nav-link${isActive ? " nav-link--active" : ""}`}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer">
          <span className="trust-dot" aria-hidden="true" /> Trusted deployment
        </div>
      </aside>
    </>
  );
}
