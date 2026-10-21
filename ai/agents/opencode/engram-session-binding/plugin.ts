import { registerSession, type Binding } from "./registration";

// Only these installed Engram tools accept session_id. Reads and lifecycle
// commands retain their original arguments; not every writer accepts project.
const WRITERS = new Set(["mem_save", "mem_save_prompt", "mem_session_summary", "mem_capture_passive", "mem_judge"]);
const PROJECT_WRITERS = new Set(["mem_save", "mem_save_prompt", "mem_session_summary"]);

export default async function EngramSessionBinding(
  context: { directory: string },
  options: { registerSession?: typeof registerSession } = {},
) {
  const register = options.registerSession ?? registerSession;
  const registrations = new Map<string, Promise<Binding>>();

  return {
    "tool.execute.before": async (
      input: { tool: string; sessionID: string },
      output: { args: Record<string, unknown> },
    ) => {
      const tool = input.tool.replace(/^engram[_:]/, "");
      if (!WRITERS.has(tool)) return;
      const args = output.args;
      if (!args || typeof args !== "object" || Array.isArray(args)) return;
      // Explicit IDs remain subject to Engram's existing ownership validation.
      if (args.session_id !== undefined) return;
      const id = input.sessionID;
      if (!id || !id.trim()) throw new Error("Engram binding: missing runtime session identity.");

      let pending = registrations.get(id);
      if (!pending) {
        pending = register(id, context.directory);
        registrations.set(id, pending);
      }
      let binding: Binding;
      try {
        binding = await pending;
        if (binding.id !== id || !binding.project) throw new Error("Engram binding: invalid session registration.");
      } catch (error) {
        if (registrations.get(id) === pending) registrations.delete(id);
        throw error;
      }
      // Do not mutate arguments or let a write run until registration succeeds.
      args.session_id = binding.id;
      if (PROJECT_WRITERS.has(tool) && args.project === undefined) args.project = binding.project;
    },
    event: async ({ event }: { event: { type: string; properties?: { info?: { id?: string } } } }) => {
      if (event.type === "session.deleted" && event.properties?.info?.id) {
        // Local cache only: never end or delete another Engram session.
        registrations.delete(event.properties.info.id);
      }
    },
  };
}
