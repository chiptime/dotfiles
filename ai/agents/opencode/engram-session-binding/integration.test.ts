import { expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import plugin from "./plugin";
import { connectEngram, toolData } from "./rpc";

test("real Engram rejects an ambiguous save, then isolates parent, sibling, and child saves", async () => {
  const root = mkdtempSync("/tmp/opencode/engram-binding-");
  const workspace = join(root, "workspace");
  const data = join(root, "data");
  mkdirSync(workspace);
  expect(Bun.spawnSync(["git", "init", "-q", workspace]).exitCode).toBe(0);
  expect(Bun.spawnSync(["git", "-C", workspace, "remote", "add", "origin", "https://example.invalid/team/binding-test.git"]).exitCode).toBe(0);
  const oldData = process.env.ENGRAM_DATA_DIR;
  const oldProject = process.env.ENGRAM_PROJECT;
  process.env.ENGRAM_DATA_DIR = data;
  delete process.env.ENGRAM_PROJECT;
  let connection: Awaited<ReturnType<typeof connectEngram>> | undefined;
  let db: Database | undefined;
  try {
    connection = await connectEngram(workspace, { tools: "mem_current_project,mem_session_start,mem_save" });
    for (const id of ["runtime-parent", "runtime-sibling"]) {
      toolData(await connection.call("mem_session_start", { id, directory: workspace }));
    }
    db = new Database(join(data, "engram.db"), { readonly: true });
    const count = () => (db!.query("SELECT count(*) AS n FROM observations").get() as { n: number }).n;
    const beforeRejected = count();
    const rejected = await connection.call("mem_save", { title: "Ambiguous", content: "Do not save", project: "binding-test", capture_prompt: false });
    expect(rejected.isError).toBe(true);
    expect(JSON.stringify(rejected)).toContain("multiple active runtime sessions match");
    expect(count()).toBe(beforeRejected);

    const hooks = await plugin({ directory: workspace });
    for (const sessionID of ["runtime-parent", "runtime-sibling", "runtime-child"]) {
      const output = { args: { title: `Save ${sessionID}`, content: `Only ${sessionID}`, capture_prompt: false } as Record<string, unknown> };
      await hooks["tool.execute.before"]({ tool: "engram_mem_save", sessionID }, output);
      expect(output.args.session_id).toBe(sessionID);
      expect(output.args.project).toBe("binding-test");
      const saved = toolData(await connection.call("mem_save", output.args));
      const row = db.query("SELECT session_id, project, content FROM observations WHERE id = ?").get(saved.id);
      expect(row).toEqual({ session_id: sessionID, project: "binding-test", content: `Only ${sessionID}` });
    }
    const sessions = db.query("SELECT id, ended_at FROM sessions ORDER BY id").all();
    expect(sessions).toEqual(["runtime-child", "runtime-parent", "runtime-sibling"].map((id) => ({ id, ended_at: null })));
    expect(count()).toBe(beforeRejected + 3);
  } finally {
    await connection?.close();
    db?.close();
    if (oldData === undefined) delete process.env.ENGRAM_DATA_DIR;
    else process.env.ENGRAM_DATA_DIR = oldData;
    if (oldProject === undefined) delete process.env.ENGRAM_PROJECT;
    else process.env.ENGRAM_PROJECT = oldProject;
    rmSync(root, { recursive: true, force: true });
  }
}, 20000);

test("registration transport times out and kills only its own subprocess", async () => {
  const root = mkdtempSync("/tmp/opencode/engram-binding-timeout-");
  const binary = join(root, "silent-server");
  writeFileSync(binary, "#!/bin/sh\nexec sleep 60\n", { mode: 0o700 });
  try {
    await expect(connectEngram(root, { binary, timeoutMs: 40 })).rejects.toThrow("timed out");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("registration rejects MCP tool errors and empty results", () => {
  expect(() => toolData({ isError: true, content: [{ type: "text", text: "{}" }] })).toThrow("rejected");
  expect(() => toolData({ content: [] })).toThrow("structured result");
});
