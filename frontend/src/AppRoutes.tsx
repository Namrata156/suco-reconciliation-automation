import { Center, Loader } from '@mantine/core';
import { lazy, Suspense, type ComponentType } from 'react';
import { Navigate, Route, Routes } from 'react-router';

import { RequireAuth, useMe } from './auth/auth';
import { AppLayout } from './layout/AppLayout';
import { NotFoundPage } from './pages/NotFoundPage';

/**
 * Every screen loads on first use (Slice 4: the single bundle was 908 kB,
 * over Vite's 500 kB advisory). The shell, auth and the not-found page stay
 * in the main chunk; AppLayout shows a placeholder while a screen loads, and
 * the Suspense below covers the login page.
 */
function named<M, K extends keyof M>(load: () => Promise<M>, key: K) {
  return lazy(() => load().then((m) => ({ default: m[key] as ComponentType })));
}

const pages = {
  login: () => import('./pages/LoginPage'),
  home: () => import('./pages/HomePage'),
  queue: () => import('./pages/CaseQueuePage'),
  caseDetail: () => import('./pages/CaseDetailPage'),
  files: () => import('./pages/FilesPage'),
  upload: () => import('./pages/UploadPage'),
  fileDetail: () => import('./pages/FileDetailPage'),
  dates: () => import('./pages/DatesPage'),
  dateDetail: () => import('./pages/DateDetailPage'),
  results: () => import('./pages/ResultsPage'),
  group: () => import('./pages/GroupDetailPage'),
  settlement: () => import('./pages/SettlementPage'),
  settings: () => import('./pages/SettingsPage'),
  approvals: () => import('./pages/PendingApprovalsPage'),
  alerts: () => import('./pages/AlertsPage'),
  audit: () => import('./pages/AuditPage'),
  users: () => import('./pages/UsersPage'),
};

const LoginPage = named(pages.login, 'LoginPage');
const HomePage = named(pages.home, 'HomePage');
const CaseQueuePage = named(pages.queue, 'CaseQueuePage');
const CaseDetailPage = named(pages.caseDetail, 'CaseDetailPage');
const FilesPage = named(pages.files, 'FilesPage');
const UploadPage = named(pages.upload, 'UploadPage');
const FileDetailPage = named(pages.fileDetail, 'FileDetailPage');
const DatesPage = named(pages.dates, 'DatesPage');
const DateDetailPage = named(pages.dateDetail, 'DateDetailPage');
const ResultsPage = named(pages.results, 'ResultsPage');
const GroupDetailPage = named(pages.group, 'GroupDetailPage');
const SettlementPage = named(pages.settlement, 'SettlementPage');
const SettingsPage = named(pages.settings, 'SettingsPage');
const ResponseCodesTab = named(pages.settings, 'ResponseCodesTab');
const NtslMappingTab = named(pages.settings, 'NtslMappingTab');
const ToleranceTab = named(pages.settings, 'ToleranceTab');
const PendingApprovalsPage = named(pages.approvals, 'PendingApprovalsPage');
const AlertsPage = named(pages.alerts, 'AlertsPage');
const AuditPage = named(pages.audit, 'AuditPage');
const AuditCaseEvents = named(pages.audit, 'AuditCaseEvents');
const AuditSettingsEvents = named(pages.audit, 'AuditSettingsEvents');
const AuditUserActivity = named(pages.audit, 'AuditUserActivity');
const AuditOperations = named(pages.audit, 'AuditOperations');
const UsersPage = named(pages.users, 'UsersPage');

function IndexRedirect() {
  const me = useMe();
  const first = me.modules[0];
  if (!first) return <NotFoundPage message="No payment module is available to your account." />;
  return <Navigate to={`/${first.slug}`} replace />;
}

const fallback = (
  <Center h="100vh">
    <Loader aria-label="Loading the screen" />
  </Center>
);

/** The shell for screens that are not about one payment module (alerts, audit, users). */
const globalShell = (
  <RequireAuth>
    <AppLayout global />
  </RequireAuth>
);

export function AppRoutes() {
  return (
    <Suspense fallback={fallback}>
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
        {/* Static paths rank above /:module, so "alerts" is never read as a module. */}
        <Route path="/alerts" element={globalShell}>
          <Route index element={<AlertsPage />} />
        </Route>
        <Route path="/audit" element={globalShell}>
          <Route element={<AuditPage />}>
            <Route index element={<Navigate to="case-events" replace />} />
            <Route path="case-events" element={<AuditCaseEvents />} />
            <Route path="settings-events" element={<AuditSettingsEvents />} />
            <Route path="user-activity" element={<AuditUserActivity />} />
            <Route path="operations" element={<AuditOperations />} />
          </Route>
        </Route>
        <Route path="/users" element={globalShell}>
          <Route index element={<UsersPage />} />
        </Route>
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
    </Suspense>
  );
}
