import { connectEngram, toolData } from "./rpc";

export type Binding = { id: string; project: string };

export async function registerSession(id: string, directory: string): Promise<Binding> {
  const connection = await connectEngram(directory);
  try {
    const current = toolData(await connection.call("mem_current_project", { cwd: directory }));
    if (current.cwd !== directory || !current.project || current.project_source === "ambiguous" || current.available_projects?.length) {
      throw new Error("Engram binding: workspace project is missing or ambiguous; registration was not attempted.");
    }
    // This ID comes from the runtime hook, not an LLM or a generated identifier.
    const started = toolData(await connection.call("mem_session_start", { id, directory }));
    if (started.project !== current.project || !started.result) {
      throw new Error("Engram binding: session registration did not confirm the workspace project.");
    }
    return { id, project: current.project };
  } finally {
    await connection.close();
  }
}
