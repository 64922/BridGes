import { AppShell } from "@/components/layout/AppShell";
import { ProjectLayout } from "@/components/layout/ProjectLayout";

interface ProjectRootLayoutProps {
  children: React.ReactNode;
  params: { projectId: string };
}

/**
 * Project-level layout.
 *
 * Switches the application shell to project navigation and wraps the page in
 * the project header, workbench tabs, and context inspector.
 */
export default function ProjectRootLayout({ children, params }: ProjectRootLayoutProps) {
  return (
    <AppShell mode="project" projectId={params.projectId}>
      <ProjectLayout projectId={params.projectId}>{children}</ProjectLayout>
    </AppShell>
  );
}
