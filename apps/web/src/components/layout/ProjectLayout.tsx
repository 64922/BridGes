import { InspectorPanel } from "./InspectorPanel";
import { MainContent } from "./MainContent";
import styles from "./ProjectLayout.module.css";
import { ProjectHeader } from "@/components/project/ProjectHeader";
import { WorkbenchNav } from "@/components/project/WorkbenchNav";

interface ProjectLayoutProps {
  projectId: string;
  children: React.ReactNode;
}

/**
 * Project-level layout.
 *
 * Composes the project header, workbench tabs, main canvas, and context
 * inspector into the project main shell. On desktop the inspector is a right
 * rail; on mobile it becomes a bottom drawer.
 */
export function ProjectLayout({ projectId, children }: ProjectLayoutProps) {
  return (
    <div className={styles.projectLayout}>
      <MainContent>
        <ProjectHeader projectId={projectId} />
        <WorkbenchNav projectId={projectId} />
        {children}
      </MainContent>
      <InspectorPanel />
    </div>
  );
}
