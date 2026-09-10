import {
  createContext,
  type PropsWithChildren,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Company, Project } from "../api/types";

interface WorkspaceValue {
  companies: Company[];
  projects: Project[];
  selectedCompanyId: string;
  selectedProjectId: string;
  selectedCompany?: Company;
  selectedProject?: Project;
  loading: boolean;
  error?: Error;
  selectCompany: (id: string) => void;
  selectProject: (id: string) => void;
  refresh: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);
const COMPANY_KEY = "enterprise-insight.company-id";
const PROJECT_KEY = "enterprise-insight.project-id";

export function WorkspaceProvider({ children }: PropsWithChildren) {
  const queryClient = useQueryClient();
  const [selectedCompanyId, setSelectedCompanyId] = useState(
    () => localStorage.getItem(COMPANY_KEY) ?? "",
  );
  const [selectedProjectId, setSelectedProjectId] = useState(
    () => localStorage.getItem(PROJECT_KEY) ?? "",
  );

  const companiesQuery = useQuery({ queryKey: ["companies"], queryFn: api.listCompanies });
  const projectsQuery = useQuery({
    queryKey: ["projects", selectedCompanyId],
    queryFn: () => api.listProjects(selectedCompanyId || undefined),
    enabled: Boolean(selectedCompanyId),
  });

  const companies = companiesQuery.data?.items ?? [];
  const projects = projectsQuery.data?.items ?? [];

  useEffect(() => {
    if (!companies.length) return;
    if (!companies.some((company) => company.id === selectedCompanyId)) {
      setSelectedCompanyId(companies[0].id);
    }
  }, [companies, selectedCompanyId]);

  useEffect(() => {
    // Do not clear a persisted project while the new company's project query is
    // still pending (or temporarily failed).  React Query exposes an empty
    // data array before the request resolves; treating that transient state as
    // authoritative made a page refresh silently jump to the first project.
    if (!selectedCompanyId || projectsQuery.isLoading || projectsQuery.isError) return;
    localStorage.setItem(COMPANY_KEY, selectedCompanyId);
    setSelectedProjectId((current) => {
      if (projects.some((project) => project.id === current)) return current;
      return projects[0]?.id ?? "";
    });
  }, [projects, projectsQuery.isError, projectsQuery.isLoading, selectedCompanyId]);

  useEffect(() => {
    if (selectedProjectId) localStorage.setItem(PROJECT_KEY, selectedProjectId);
    else localStorage.removeItem(PROJECT_KEY);
  }, [selectedProjectId]);

  const value = useMemo<WorkspaceValue>(
    () => ({
      companies,
      projects,
      selectedCompanyId,
      selectedProjectId,
      selectedCompany: companies.find((company) => company.id === selectedCompanyId),
      selectedProject: projects.find((project) => project.id === selectedProjectId),
      loading: companiesQuery.isLoading || projectsQuery.isLoading,
      error: (companiesQuery.error ?? projectsQuery.error) as Error | undefined,
      selectCompany: (id) => {
        setSelectedCompanyId(id);
        setSelectedProjectId("");
      },
      selectProject: setSelectedProjectId,
      refresh: async () => {
        await queryClient.invalidateQueries({ queryKey: ["companies"] });
        await queryClient.invalidateQueries({ queryKey: ["projects"] });
      },
    }),
    [
      companies,
      companiesQuery.error,
      companiesQuery.isLoading,
      projects,
      projectsQuery.error,
      projectsQuery.isLoading,
      queryClient,
      selectedCompanyId,
      selectedProjectId,
    ],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace 必须在 WorkspaceProvider 内使用");
  return value;
}
