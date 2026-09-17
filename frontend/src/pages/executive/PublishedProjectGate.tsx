import type { PropsWithChildren } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { StatusMessage } from "../../components/StatusMessage";

export function PublishedProjectGate({ projectId, children }: PropsWithChildren<{ projectId: string }>) {
  const publicationsQuery = useQuery({
    queryKey: ["publications", projectId],
    queryFn: () => api.listPublications(projectId),
  });

  if (publicationsQuery.isLoading) {
    return <StatusMessage title="正在检查企业投影版本" description="使用端只读取已经正式发布的企业投影。" />;
  }
  if (publicationsQuery.error) {
    return <StatusMessage tone="danger" title="无法读取发布状态" description={(publicationsQuery.error as Error).message} action={{ label: "重试", onClick: () => void publicationsQuery.refetch() }} />;
  }
  if (!publicationsQuery.data?.total) {
    return <StatusMessage title="当前企业还没有正式发布版本" description="请先在建设端完成企业投影审核并发布；使用端不会直接操作未发布草稿。" />;
  }
  return <>{children}</>;
}
