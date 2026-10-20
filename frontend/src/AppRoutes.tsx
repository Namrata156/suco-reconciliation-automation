import { lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router';

import { RequireAuth, useMe } from './auth/auth';
import { AppLayout } from './layout/AppLayout';
import { CaseDetailPage } from './pages/CaseDetailPage';
import { CaseQueuePage } from './pages/CaseQueuePage';
import { DateDetailPage } from './pages/DateDetailPage';
import { DatesPage } from './pages/DatesPage';
import { FileDetailPage } from './pages/FileDetailPage';
import { FilesPage } from './pages/FilesPage';
import { HomePage } from './pages/HomePage';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { UploadPage } from './pages/UploadPage';

// Slice 3 screens load on first use (the main bundle was already over Vite's
// 500 kB advisory). AppLayout shows a placeholder while a chunk loads.
const settingsChunk = () => import('./pages/SettingsPage');
const ResultsPage = lazy(() => import('./pages/ResultsPage').then((m) => ({ default: m.ResultsPage })));
const GroupDetailPage = lazy(() =>
  import('./pages/GroupDetailPage').then((m) => ({ default: m.GroupDetailPage })),
);
const SettlementPage = lazy(() =>
  import('./pages/SettlementPage').then((m) => ({ default: m.SettlementPage })),
);
const SettingsPage = lazy(() => settingsChunk().then((m) => ({ default: m.SettingsPage })));
const ResponseCodesTab = lazy(() => settingsChunk().then((m) => ({ default: m.ResponseCodesTab })));
const NtslMappingTab = lazy(() => settingsChunk().then((m) => ({ default: m.NtslMappingTab })));
const ToleranceTab = lazy(() => settingsChunk().then((m) => ({ default: m.ToleranceTab })));
const PendingApprovalsPage = lazy(() =>
  import('./pages/PendingApprovalsPage').then((m) => ({ default: m.PendingApprovalsPage })),
);

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
        <Route path="files" element={<FilesPage />} />
        <Route path="files/upload" element={<UploadPage />} />
        <Route path="files/:fileId" element={<FileDetailPage />} />
        <Route path="dates" element={<DatesPage />} />
        <Route path="dates/:businessDate" element={<DateDetailPage />} />
        <Route path="dates/:businessDate/results" element={<ResultsPage />} />
        <Route path="dates/:businessDate/groups/:groupId" element={<GroupDetailPage />} />
        <Route path="dates/:businessDate/settlement" element={<SettlementPage />} />
        <Route path="settings" element={<SettingsPage />}>
          <Route index element={<Navigate to="response-codes" replace />} />
          <Route path="response-codes" element={<ResponseCodesTab />} />
          <Route path="ntsl-mapping" element={<NtslMappingTab />} />
          <Route path="tolerance" element={<ToleranceTab />} />
        </Route>
        <Route path="approvals" element={<PendingApprovalsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
