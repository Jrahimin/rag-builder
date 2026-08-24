import { Plus, Users } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { operatorApiClient, type AdminUser } from "../../api/operatorApiClient";
import { operatorQueryKeys, useAdminUsers } from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { useAdminAuth } from "../../auth/useAdminAuth";
import { formatDate, shortId } from "../../shared/formatters";

function adminStatus(admin: AdminUser) {
  if (admin.deleted_at) return "archived";
  return admin.is_active ? "active" : "disabled";
}

function roleLabel(role: string) {
  return role === "SUPER_ADMIN" ? "Super Admin" : "Admin";
}

export function AdminAdministration() {
  const queryClient = useQueryClient();
  const admins = useAdminUsers();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("admin") ?? "";
  const [creating, setCreating] = useState(false);

  const selectedId = useMemo(() => {
    const items = admins.data?.items ?? [];
    return items.some((item) => item.id === requestedId) ? requestedId : (items[0]?.id ?? "");
  }, [admins.data, requestedId]);
  const selected = admins.data?.items.find((item) => item.id === selectedId);

  useEffect(() => {
    if (selectedId && requestedId !== selectedId) {
      setParams({ admin: selectedId }, { replace: true });
    }
  }, [requestedId, selectedId, setParams]);

  if (admins.isPending) return <LoadingState label="Loading Admins" />;
  if (admins.isError)
    return <ErrorState error={admins.error} retry={() => void admins.refetch()} />;

  return (
    <div className="admin-workspace">
      <section className="panel admin-rail">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Operator access</p>
            <h2>Admins</h2>
          </div>
          <button className="icon-button" type="button" onClick={() => setCreating(true)}>
            <Plus size={17} aria-hidden="true" />
            <span className="sr-only">Create Admin</span>
          </button>
        </div>
        <p className="muted-copy">
          Console-created accounts are Admins. They have the same access as Super Admin for now.
        </p>
        {creating && (
          <CreateAdmin
            onCancel={() => setCreating(false)}
            onCreated={(admin) => {
              setCreating(false);
              void queryClient.invalidateQueries({ queryKey: operatorQueryKeys.adminUsers });
              setParams({ admin: admin.id });
            }}
          />
        )}
        {admins.data.items.length === 0 ? (
          <EmptyState
            compact
            title="No operators yet"
            detail="Create an Admin or bootstrap Super Admin from the CLI."
          />
        ) : (
          <div className="admin-rail__list">
            {admins.data.items.map((admin) => (
              <button
                key={admin.id}
                type="button"
                className={admin.id === selectedId ? "rail-card rail-card--active" : "rail-card"}
                onClick={() => setParams({ admin: admin.id })}
              >
                <span className="rail-card__icon">
                  <Users size={17} />
                </span>
                <span>
                  <strong>{admin.email}</strong>
                  <small>{roleLabel(admin.role)}</small>
                </span>
                <StatusBadge status={adminStatus(admin)} />
              </button>
            ))}
          </div>
        )}
      </section>
      <section className="admin-detail">
        {selected ? (
          <AdminDetail
            admin={selected}
            onChanged={() =>
              void queryClient.invalidateQueries({ queryKey: operatorQueryKeys.adminUsers })
            }
          />
        ) : (
          <EmptyState
            compact
            title="Select an Admin"
            detail="Account status and removal controls appear here."
          />
        )}
      </section>
    </div>
  );
}

function CreateAdmin({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (admin: AdminUser) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      onCreated(await operatorApiClient.createAdminUser(email, password));
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="stack-form compact-form" onSubmit={(event) => void submit(event)}>
      <label className="field-control">
        <span>Email</span>
        <input
          required
          type="email"
          maxLength={320}
          autoComplete="off"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </label>
      <label className="field-control">
        <span>Password (min 8 characters)</span>
        <input
          required
          type="password"
          minLength={8}
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
      <label className="field-control">
        <span>Confirm password</span>
        <input
          required
          type="password"
          minLength={8}
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
        />
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="button-row">
        <button className="button button--primary" disabled={busy} type="submit">
          Create Admin
        </button>
        <button className="button button--secondary" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function AdminDetail({ admin, onChanged }: { admin: AdminUser; onChanged: () => void }) {
  const { admin: current } = useAdminAuth();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const isSelf = current?.id === admin.id;
  const isSuperAdmin = admin.role === "SUPER_ADMIN";
  const canManage = !isSuperAdmin && !isSelf && !admin.deleted_at;

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try {
      await action();
      onChanged();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy("");
    }
  };

  return (
    <>
      <section className="panel detail-hero">
        <div>
          <p className="eyebrow">
            {roleLabel(admin.role)} · {shortId(admin.id)}
          </p>
          <h2>{admin.email}</h2>
          <p>
            {isSuperAdmin
              ? "Bootstrap Super Admin. Created from the CLI and protected in the console."
              : "Admin operator. Same console access as Super Admin."}
          </p>
        </div>
        <StatusBadge status={adminStatus(admin)} />
      </section>
      {error && (
        <div className="failure-box" role="alert">
          {error}
        </div>
      )}
      <section className="panel">
        <div className="section-heading">
          <h3>Account</h3>
          <span>Updated {formatDate(admin.updated_at)}</span>
        </div>
        <dl className="fact-grid panel-body">
          <div>
            <dt>Email</dt>
            <dd>{admin.email}</dd>
          </div>
          <div>
            <dt>Role</dt>
            <dd>{roleLabel(admin.role)}</dd>
          </div>
          <div>
            <dt>Last sign-in</dt>
            <dd>{admin.last_login_at ? formatDate(admin.last_login_at) : "Never"}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{formatDate(admin.created_at)}</dd>
          </div>
        </dl>
        <div className="button-row panel-body">
          {canManage && (
            <button
              className="button button--secondary"
              type="button"
              disabled={Boolean(busy)}
              onClick={() => {
                const nextActive = !admin.is_active;
                const confirmed =
                  nextActive ||
                  window.confirm(
                    `Disable ${admin.email}? They will be signed out and cannot log in.`,
                  );
                if (!confirmed) return;
                void run("status", () =>
                  operatorApiClient.setAdminUserStatus(admin.id, nextActive),
                );
              }}
            >
              {admin.is_active ? "Disable" : "Enable"}
            </button>
          )}
          {canManage && (
            <button
              className="button button--danger"
              type="button"
              disabled={Boolean(busy)}
              onClick={() => {
                if (
                  window.confirm(
                    `Remove ${admin.email}? They will be signed out. You can restore them later.`,
                  )
                ) {
                  void run("delete", () => operatorApiClient.deleteAdminUser(admin.id));
                }
              }}
            >
              Remove
            </button>
          )}
          {admin.deleted_at && !isSuperAdmin && (
            <button
              className="button button--primary"
              type="button"
              disabled={Boolean(busy)}
              onClick={() =>
                void run("restore", () => operatorApiClient.restoreAdminUser(admin.id))
              }
            >
              Restore disabled
            </button>
          )}
          {isSelf && <p className="muted-copy">You cannot disable or remove your own account.</p>}
          {isSuperAdmin && !isSelf && (
            <p className="muted-copy">Super Admin status and removal stay CLI-managed.</p>
          )}
        </div>
      </section>
    </>
  );
}
