import CreateTestPage from "./pages/CreateTestPage";
import ScheduledTestsPage from "./pages/ScheduledTestsPage";
import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import "./styles/base.css";
import { AuthProvider } from "./auth/AuthContext";
import { ScopeProvider } from "./auth/ScopeContext";
import AppShell from "./components/AppShell";
import ProtectedRoute from "./components/ProtectedRoute";
import { Card } from "./components/ui";
import AccountPage from "./pages/AccountPage";
import BatchesPage from "./pages/BatchesPage";
import { ForgotPasswordPage, LoginPage, MfaPage, ResetPasswordPage, VerifyEmailPage } from "./pages/AuthPages";
import DashboardPage from "./pages/DashboardPage";
import CompositePage from "./pages/CompositePage";
import { ExamDetailPage, ExamPlansPage, ExamsPage } from "./pages/ExamsPages";
import { AuditPage, BillingPage, NotificationsPage, SettingsPage, SupportPage } from "./pages/OpsPages";
import { PaperDetailPage } from "./pages/PapersPage";
import QuestionBankPage from "./pages/QuestionBankPage";
import { BranchDetailPage, ProviderDetailPage, ProvidersPage } from "./pages/ProvidersPage";
import RoleManagementPage, { RoleCategoryPage } from "./pages/StaffPage";
import StudentsPage from "./pages/StudentsPage";
import SubjectsPage from "./pages/SubjectsPage";
import SystemHealthPage from "./pages/SystemHealthPage";
import TestWizardPage from "./pages/TestWizardPage";

function Forbidden() {
  return <div className="auth-wrap"><Card className="auth-card"><h1>Access denied</h1><p>Your role cannot open this page.</p><Link to="/">Back to dashboard</Link></Card></div>;
}

/** The route tree (exported so tests can mount it in a memory router). */
export function AppRoutes() {
  return (
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
            <Route path="providers/:providerId/branches/:branchId" element={<BranchDetailPage />} />
          </Route>
          <Route element={<ProtectedRoute perm="staff.read" />}>
            <Route path="staff" element={<RoleManagementPage />} />
            <Route path="staff/:category" element={<RoleCategoryPage />} />
          </Route>
          <Route element={<ProtectedRoute perm="enrollment.manage" />}><Route path="students" element={<StudentsPage />} /></Route>
          <Route element={<ProtectedRoute anyPerm={["subject.manage", "teaching.assign"]} />}><Route path="subjects" element={<SubjectsPage />} /></Route>
          <Route element={<ProtectedRoute perm="batch.manage" />}><Route path="batches" element={<BatchesPage />} /></Route>
          {/* old address of the combined subjects/batches page */}
          <Route path="academics" element={<Navigate to="/subjects" replace />} />
          <Route element={<ProtectedRoute perm="paper.read" />}>
            <Route path="papers" element={<QuestionBankPage />} />
            <Route path="papers/:versionId" element={<PaperDetailPage />} />
          </Route>
          <Route element={<ProtectedRoute perm="examplan.manage" />}>
          <Route path="exam-plans" element={<ExamPlansPage />} />
          <Route path="exam-plans/new" element={<TestWizardPage />} />
        </Route>
          <Route element={<ProtectedRoute perm="examplan.manage" />}><Route path="composite" element={<CompositePage />} /></Route>
          <Route element={<ProtectedRoute perm="test.create" />}>
            <Route path="tests" element={<ScheduledTestsPage />} />
            <Route path="tests/new" element={<CreateTestPage />} />
          </Route>
          {/* Internal diagnostics: not in any menu, still available to the platform owner at this address. */}
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
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
