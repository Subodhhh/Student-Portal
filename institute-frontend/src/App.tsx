import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import "./styles/base.css";
import { AuthProvider } from "./auth/AuthContext";
import { ScopeProvider } from "./auth/ScopeContext";
import AppShell from "./components/AppShell";
import ProtectedRoute from "./components/ProtectedRoute";
import { Card } from "./components/ui";
import AcademicsPage from "./pages/AcademicsPage";
import AccountPage from "./pages/AccountPage";
import { ForgotPasswordPage, LoginPage, MfaPage, ResetPasswordPage, VerifyEmailPage } from "./pages/AuthPages";
import DashboardPage from "./pages/DashboardPage";
import CompositePage from "./pages/CompositePage";
import { ExamDetailPage, ExamPlansPage, ExamsPage } from "./pages/ExamsPages";
import { AuditPage, BillingPage, NotificationsPage, SettingsPage, SupportPage } from "./pages/OpsPages";
import { PaperDetailPage, PapersPage } from "./pages/PapersPage";
import { ProviderDetailPage, ProvidersPage } from "./pages/ProvidersPage";
import StaffPage from "./pages/StaffPage";
import StudentsPage from "./pages/StudentsPage";
import SystemHealthPage from "./pages/SystemHealthPage";

function Forbidden() {
  return <div className="auth-wrap"><Card className="auth-card"><h1>Access denied</h1><p>Your role cannot open this page.</p><Link to="/">Back to dashboard</Link></Card></div>;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/mfa" element={<MfaPage />} />
          <Route path="/verify-email" element={<VerifyEmailPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/forbidden" element={<Forbidden />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<ScopeProvider><AppShell /></ScopeProvider>}>
              <Route index element={<DashboardPage />} />
              <Route path="account" element={<AccountPage />} />
              <Route path="notifications" element={<NotificationsPage />} />
              <Route path="support" element={<SupportPage />} />
              <Route element={<ProtectedRoute perm="provider.read" />}>
                <Route path="providers" element={<ProvidersPage />} />
                <Route path="providers/:providerId" element={<ProviderDetailPage />} />
              </Route>
              <Route element={<ProtectedRoute perm="staff.read" />}><Route path="staff" element={<StaffPage />} /></Route>
              <Route element={<ProtectedRoute perm="enrollment.manage" />}><Route path="students" element={<StudentsPage />} /></Route>
              <Route element={<ProtectedRoute anyPerm={["batch.manage", "subject.manage", "teaching.assign"]} />}><Route path="academics" element={<AcademicsPage />} /></Route>
              <Route element={<ProtectedRoute perm="paper.read" />}>
                <Route path="papers" element={<PapersPage />} />
                <Route path="papers/:versionId" element={<PaperDetailPage />} />
              </Route>
              <Route element={<ProtectedRoute perm="examplan.manage" />}><Route path="exam-plans" element={<ExamPlansPage />} /></Route>
              <Route element={<ProtectedRoute perm="examplan.manage" />}><Route path="composite" element={<CompositePage />} /></Route>
              <Route element={<ProtectedRoute perm="settings.platform" />}><Route path="system-health" element={<SystemHealthPage />} /></Route>
              <Route element={<ProtectedRoute perm="exam.read" />}>
                <Route path="exams" element={<ExamsPage />} />
                <Route path="exams/:examId" element={<ExamDetailPage />} />
              </Route>
              <Route element={<ProtectedRoute perm="billing.read" />}><Route path="billing" element={<BillingPage />} /></Route>
              <Route element={<ProtectedRoute anyPerm={["settings.platform", "settings.provider", "settings.branch"]} />}><Route path="settings" element={<SettingsPage />} /></Route>
              <Route element={<ProtectedRoute perm="audit.read" />}><Route path="audit" element={<AuditPage />} /></Route>
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
