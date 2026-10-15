import { Navigate, Route, Routes } from 'react-router';

import { RequireAuth, useMe } from './auth/auth';
import { AppLayout } from './layout/AppLayout';
import { CaseDetailPage } from './pages/CaseDetailPage';
import { CaseQueuePage } from './pages/CaseQueuePage';
import { HomePage } from './pages/HomePage';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';

function IndexRedirect() {
  const me = useMe();
  const first = me.modules[0];
  if (!first) return <NotFoundPage message="No payment module is available to your account." />;
  return <Navigate to={`/${first.slug}`} replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <IndexRedirect />
          </RequireAuth>
        }
      />
      <Route
        path="/:module"
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<HomePage />} />
        <Route path="cases" element={<CaseQueuePage />} />
        <Route path="cases/:caseId" element={<CaseDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
