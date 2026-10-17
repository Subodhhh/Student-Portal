import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ROLE_LABEL } from "../auth/nav";
import { Alert, Button, Card, DataTable, Field, PageHeader } from "../components/ui";
import { useAction } from "../hooks/useApi";
import { api, setCsrfToken } from "../services/api";

export default function AccountPage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const { busy, error, run } = useAction();
  if (!me) return null;
  const submit = () => run(async () => {
    await api("/api/v1/auth/change-password", { method: "POST", body: { current_password: current, new_password: next } });
    setCsrfToken(null);
    navigate("/login", { replace: true }); // the server ended every session
    window.location.reload();
  });
  return (
    <>
      <PageHeader subtitle={`${me.full_name} · ${me.staff_code} · ${me.email}`} />
      <div className="grid cols-2">
        <Card>
          <h2>Roles</h2>
          <DataTable rows={me.assignments} rowKey={(a) => a.assignment_id}
            columns={[{ header: "Role", render: (a) => ROLE_LABEL[a.role] }, { header: "Provider", render: (a) => a.provider_id?.slice(0, 8) ?? "All" }, { header: "Branch", render: (a) => a.branch_id?.slice(0, 8) ?? "All" }]} />
        </Card>
        <Card>
          <h2>Change password</h2>
          <p className="muted small">Changing your password signs you out everywhere.</p>
          {error && <Alert kind="error">{error}</Alert>}
          <Field label="Current password" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
          <Field label="New password" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" hint="At least 12 characters." />
          <Button busy={busy} disabled={!current || next.length < 12} onClick={() => void submit()}>Change password</Button>
        </Card>
      </div>
    </>
  );
}
