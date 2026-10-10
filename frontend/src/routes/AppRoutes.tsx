import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import LoginPage from "../pages/auth/LoginPage";
import ForgotPasswordPage from "../pages/auth/ForgotPasswordPage";
import ResetPasswordPage from "../pages/auth/ResetPasswordPage";

import StudentLayout from "../layouts/StudentLayout";
import TestLayout from "../layouts/TestLayout";

import DashboardPage from "../pages/student/DashboardPage";
import MyInstitutesPage from "../pages/student/MyInstitutesPage";
import TestsPage from "../pages/student/TestsPage";
import ScheduledTestsPage from "../pages/student/ScheduledTestsPage";
import TestDetailsPage from "../pages/student/TestDetailsPage";
import TestAttemptPage from "../pages/student/TestAttemptPage";
import ResultsPage from "../pages/student/ResultsPage";
import RankingPage from "../pages/student/RankingPage";
import NotificationsPage from "../pages/student/NotificationsPage";
import ProfilePage from "../pages/student/ProfilePage";
import PerformancePage from "../pages/student/PerformancePage";
import TestResultPage from "../pages/student/TestResultPage";

import ProtectedRoute from "../components/ProtectedRoute";


function AppRoutes() {
  return (
    <BrowserRouter>
      <Routes>

        {/* Public routes */}
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/forgot-password"
          element={<ForgotPasswordPage />}
        />
        <Route
          path="/reset-password"
          element={<ResetPasswordPage />}
        />

        {/* Protected student routes */}
        <Route element={<ProtectedRoute />}>
          <Route element={<StudentLayout />}>
            <Route
              path="/dashboard"
              element={<DashboardPage />}
            />

            <Route
              path="/my-institutes"
              element={<MyInstitutesPage />}
            />

            <Route
              path="/tests"
              element={<TestsPage />}
            />

            <Route
              path="/scheduled-tests"
              element={<ScheduledTestsPage />}
            />

            <Route
              path="/tests/:testId"
              element={<TestDetailsPage />}
            />

            <Route
              path="/results"
              element={<ResultsPage />}
            />

            <Route
              path="/ranking"
              element={<RankingPage />}
            />

            <Route
              path="/notifications"
              element={<NotificationsPage />}
            />

            <Route
              path="/profile"
              element={<ProfilePage />}
            />

            <Route
              path="/tests/:testId/performance"
              element={<PerformancePage />}
            />

            <Route
              path="/tests/result/:attemptId"
              element={<TestResultPage />}
            />
          </Route>
        </Route>

        {/* Protected test attempt route */}
        <Route element={<ProtectedRoute />}>
          <Route element={<TestLayout />}>
            <Route
              path="/tests/:testId/attempt"
              element={<TestAttemptPage />}
            />
          </Route>
        </Route>

        {/* Unknown routes */}
        <Route
          path="*"
          element={<Navigate to="/login" replace />}
        />

      </Routes>
    </BrowserRouter>
  );
}

export default AppRoutes;