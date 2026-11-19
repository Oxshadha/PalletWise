import { createContext, type Dispatch, type ReactNode, useContext, useReducer } from "react";

import type { Me } from "../api/client";
import {
  initialWorkspace,
  type WorkspaceAction,
  workspaceReducer,
  type WorkspaceState,
} from "./workspace";

interface Workspace {
  state: WorkspaceState;
  dispatch: Dispatch<WorkspaceAction>;
  me: Me;
  siteId: string;
  /** False while the site is LOADING, REBUILDING or FAILED: the copilot input is disabled. */
  dataReady: boolean;
}

const WorkspaceContext = createContext<Workspace | null>(null);

export function WorkspaceProvider({
  me,
  siteId,
  dataReady,
  children,
}: {
  me: Me;
  siteId: string;
  dataReady: boolean;
  children: ReactNode;
}) {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspace);
  return (
    <WorkspaceContext.Provider value={{ state, dispatch, me, siteId, dataReady }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): Workspace {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) throw new Error("useWorkspace outside WorkspaceProvider");
  return workspace;
}
