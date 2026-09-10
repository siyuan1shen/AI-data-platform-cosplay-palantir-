import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { StatusMessage } from "../components/StatusMessage";

const PortfolioPage = lazy(async () => ({ default: (await import("../pages/developer/PortfolioPage")).PortfolioPage }));
const BuildWorkbenchPage = lazy(async () => ({ default: (await import("../pages/developer/BuildWorkbenchPage")).BuildWorkbenchPage }));
const ProjectionModelPage = lazy(async () => ({ default: (await import("../pages/developer/ProjectionModelPage")).ProjectionModelPage }));
const AdvancedToolsPage = lazy(async () => ({ default: (await import("../pages/developer/AdvancedToolsPage")).AdvancedToolsPage }));
const ManagementAgentPage = lazy(async () => ({ default: (await import("../pages/executive/ManagementAgentPage")).ManagementAgentPage }));
const DecisionsPage = lazy(async () => ({ default: (await import("../pages/executive/DecisionsPage")).DecisionsPage }));
const ProjectionPage = lazy(async () => {
  const module = await import("../pages/executive/ProjectionPage");
  return { default: module.ProjectionPage };
});

export function App() {
  return (
    <Suspense fallback={<StatusMessage title="正在打开功能页" description="正在加载所需模块。" />}>
      <Routes>
        <Route path="/developer" element={<AppShell surface="developer"><PortfolioPage /></AppShell>} />
        <Route path="/developer/build" element={<AppShell surface="developer"><BuildWorkbenchPage /></AppShell>} />
        <Route path="/developer/model" element={<AppShell surface="developer"><ProjectionModelPage /></AppShell>} />
        <Route path="/developer/advanced" element={<AppShell surface="developer"><AdvancedToolsPage /></AppShell>} />
        <Route path="/developer/release" element={<Navigate replace to="/developer/advanced" />} />
        <Route path="/executive" element={<AppShell surface="executive"><ProjectionPage /></AppShell>} />
        <Route path="/executive/agent" element={<AppShell surface="executive"><ManagementAgentPage /></AppShell>} />
        <Route path="/executive/decisions" element={<AppShell surface="executive"><DecisionsPage /></AppShell>} />
        <Route path="*" element={<Navigate replace to="/developer" />} />
      </Routes>
    </Suspense>
  );
}
