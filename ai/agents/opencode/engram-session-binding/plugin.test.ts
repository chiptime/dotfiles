import { describe, expect, test } from "bun:test";
import plugin from "./plugin";

const directory = "/workspace/example";
const input = (tool = "engram_mem_save", sessionID = "runtime-a") => ({ tool, sessionID });
type ToolInput = ReturnType<typeof input>;

async function setup(registerSession = async (id: string, _cwd: string) => ({ id, project: "example" })) {
  const hooks = await plugin({ directory }, { registerSession });
  return hooks["tool.execute.before"] as (input: ToolInput, output: { args: Record<string, unknown> }) => Promise<void>;
}

describe("Engram runtime session binding", () => {
  test("binds every session-aware writer, including MCP prefixes", async () => {
    const before = await setup();
    for (const name of ["mem_save", "mem_save_prompt", "mem_session_summary", "mem_capture_passive", "mem_judge"]) {
      for (const prefix of ["", "engram_", "engram:"]) {
        const output = { args: { content: "Original content", capture_prompt: false } as Record<string, unknown> };
        await before(input(prefix + name), output);
        const project = ["mem_save", "mem_save_prompt", "mem_session_summary"].includes(name) ? { project: "example" } : {};
        expect(output.args).toEqual({ content: "Original content", capture_prompt: false, session_id: "runtime-a", ...project });
      }
    }
  });

  test("leaves reads, unrelated tools, and explicit session IDs unchanged", async () => {
    let registrations = 0;
    const before = await setup(async (id) => { registrations++; return { id, project: "example" }; });
    for (const tool of ["read", "engram_mem_search", "engram_mem_current_project", "engram_mem_update", "engram_mem_session_end", "other_mem_save"]) {
      const output = { args: { project: "other" } };
      await before(input(tool), output);
      expect(output.args).toEqual({ project: "other" });
    }
    const explicit = { args: { session_id: "already-registered", project: "other" } };
    await before(input(), explicit);
    expect(explicit.args).toEqual({ session_id: "already-registered", project: "other" });
    expect(registrations).toBe(0);
  });

  test("awaits registration and deduplicates concurrent calls without mixing sessions", async () => {
    const seen: string[] = [];
    let release!: () => void;
    const ready = new Promise<void>((resolve) => { release = resolve; });
    const before = await setup(async (id, cwd) => {
      expect(cwd).toBe(directory);
      seen.push(id);
      await ready;
      return { id, project: "example" };
    });
    const a = { args: {} as Record<string, unknown> };
    const b = { args: { project: "explicit-project" } as Record<string, unknown> };
    const child = { args: {} as Record<string, unknown> };
    const calls = [before(input(), a), before(input(), b), before(input("engram_mem_save", "runtime-child"), child)];
    expect(a.args).toEqual({});
    expect(seen).toEqual(["runtime-a", "runtime-child"]);
    release();
    await Promise.all(calls);
    expect(a.args.session_id).toBe("runtime-a");
    expect(b.args).toEqual({ project: "explicit-project", session_id: "runtime-a" });
    expect(child.args.session_id).toBe("runtime-child");
    await before(input(), { args: {} });
    expect(seen).toHaveLength(2);
  });

  test("registration failure leaves arguments untouched and permits a later retry", async () => {
    let attempts = 0;
    const before = await setup(async (id) => {
      if (++attempts === 1) throw new Error("registration unavailable");
      return { id, project: "example" };
    });
    const output = { args: { content: "Keep me" } as Record<string, unknown> };
    await expect(before(input(), output)).rejects.toThrow("registration unavailable");
    expect(output.args).toEqual({ content: "Keep me" });
    await before(input(), output);
    expect(output.args.session_id).toBe("runtime-a");
    expect(attempts).toBe(2);
  });

  test("rejects missing runtime identity or mismatched registration without mutating arguments", async () => {
    const before = await setup(async () => ({ id: "wrong-session", project: "example" }));
    const output = { args: {} };
    await expect(before(input("engram_mem_save", ""), output)).rejects.toThrow("runtime session");
    await expect(before(input(), output)).rejects.toThrow("registration");
    expect(output.args).toEqual({});
  });
});
