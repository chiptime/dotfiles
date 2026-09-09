import { afterEach, describe, expect, test } from "bun:test";
import {
	chmodSync,
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseState } from "../src/core.ts";

/** cron.sh under test — the repo copy, exactly what the symlink resolves to. */
const CRON_SH = join(import.meta.dir, "..", "cron.sh");

const tmpDirs: string[] = [];
afterEach(() => {
	for (const dir of tmpDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function writeStub(path: string, body: string): void {
	writeFileSync(path, `#!/usr/bin/env bash\n${body}\n`);
	chmodSync(path, 0o755);
}

function lines(path: string): number {
	if (!existsSync(path)) return 0;
	return readFileSync(path, "utf8").split("\n").filter((l) => l.trim() !== "").length;
}

function content(path: string): string {
	return existsSync(path) ? readFileSync(path, "utf8") : "";
}

/**
 * Fresh sandbox per test: tmp HOME (state/log/PAUSE under it), tmp lock file,
 * and PATH prepended with a `zsh` shim. The shim proves the default agent
 * runner NEVER fires under test — even in RED, before the gate existed.
 */
function setup() {
	const tmp = mkdtempSync(join(tmpdir(), "t2t-cron-"));
	tmpDirs.push(tmp);
	const home = join(tmp, "home");
	const stateDir = join(home, ".local", "state", "teams-to-tasks");
	mkdirSync(stateDir, { recursive: true });
	const bin = join(tmp, "bin");
	mkdirSync(bin);

	const ctx = {
		home,
		stateFile: join(stateDir, "last_run"),
		logFile: join(stateDir, "cron.log"),
		pauseFile: join(stateDir, "PAUSE"),
		lockFile: join(tmp, "cron.lock"),
		pollMarker: join(tmp, "poll.count"),
		agentMarker: join(tmp, "agent.count"),
		zshMarker: join(tmp, "zsh.count"),
		notifyLog: join(tmp, "notify.log"),
		promptDump: join(tmp, "prompt.txt"),
		/** Run cron.sh with stubbed poller/agent (env seams) and stub notify. */
		run(opts: { pollRc?: number; agentRc?: number }) {
			const pollRc = opts.pollRc ?? 0;
			const agentRc = opts.agentRc ?? 0;
			const notifyExe = join(tmp, "notify-stub.sh");
			writeStub(notifyExe, `printf '%s\\n' "$1" >> ${ctx.notifyLog}`);
			writeStub(
				join(bin, "zsh"),
				`printf zsh >> ${ctx.zshMarker}\nexit 127`,
			);
			const env: Record<string, string> = {};
			for (const [k, v] of Object.entries(process.env)) {
				if (v !== undefined) env[k] = v;
			}
			env.HOME = home;
			env.PATH = `${bin}:${env.PATH ?? ""}`;
			env.TEAMS_LOCK_FILE = ctx.lockFile;
			env.TEAMS_NOTIFY_EXE = notifyExe;
			env.TEAMS_POLL_CMD = `echo poll >> ${ctx.pollMarker}; exit ${pollRc}`;
			env.TEAMS_AGENT_CMD =
				`echo agent >> ${ctx.agentMarker}; ` +
				`printf '%s' "$TEAMS_PROMPT" > ${ctx.promptDump}; ` +
				`exit ${agentRc}`;
			const proc = Bun.spawnSync(["bash", CRON_SH, "sweep"], {
				env,
				stdout: "pipe",
				stderr: "pipe",
			});
			return { rc: proc.exitCode, log: content(ctx.logFile) };
		},
	};
	return ctx;
}

describe("cron.sh — poller-first agent gating (sweep)", () => {
	test("poller rc 0 → agent exactly once with the PREV window; state advances to poll-start T after agent success", () => {
		const c = setup();
		writeFileSync(c.stateFile, "2026-09-09T18:30:00.000Z");
		const r = c.run({ pollRc: 0, agentRc: 0 });
		expect(r.rc).toBe(0);
		expect(lines(c.pollMarker)).toBe(1);
		expect(lines(c.agentMarker)).toBe(1);
		expect(lines(c.zshMarker)).toBe(0); // default agent runner never fires under test
		// The agent swept the window captured BEFORE polling:
		expect(content(c.promptDump)).toContain("2026-09-09T18:30:00.000Z");
		// Deferred advancement: cron (not the poller) wrote the poll-start T,
		// in the ISO-8601 UTC form the poller can re-read:
		const written = readFileSync(c.stateFile, "utf8");
		expect(written).not.toBe("2026-09-09T18:30:00.000Z");
		expect(written).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
		expect(parseState(written)).not.toBe(null);
		expect(r.log).toContain("[ok] sweep completed");
		expect(content(c.notifyLog)).toContain("✅ Teams sweep");
	});

	test("poller rc 1 → no agent, no notify, log only; cron leaves state to the poller", () => {
		const c = setup();
		writeFileSync(c.stateFile, "2026-09-09T18:30:00.000Z");
		const r = c.run({ pollRc: 1, agentRc: 0 });
		expect(r.rc).toBe(0);
		expect(lines(c.pollMarker)).toBe(1);
		expect(lines(c.agentMarker)).toBe(0);
		expect(lines(c.zshMarker)).toBe(0);
		expect(readFileSync(c.stateFile, "utf8")).toBe("2026-09-09T18:30:00.000Z");
		expect(existsSync(c.notifyLog)).toBe(false);
		expect(r.log).toContain("[quiet]");
	});

	test("poller rc 2 / 124 / 137 (failure family) → no agent, notify, state untouched", () => {
		for (const rc of [2, 124, 137]) {
			const c = setup();
			writeFileSync(c.stateFile, "2026-09-09T18:30:00.000Z");
			const r = c.run({ pollRc: rc, agentRc: 0 });
			expect(r.rc).toBe(0);
			expect(lines(c.pollMarker)).toBe(1);
			expect(lines(c.agentMarker)).toBe(0);
			expect(lines(c.zshMarker)).toBe(0);
			expect(readFileSync(c.stateFile, "utf8")).toBe("2026-09-09T18:30:00.000Z");
			expect(content(c.notifyLog)).toContain(`Poller fallido (rc ${rc})`);
			expect(r.log).toContain(`[poll-fail] poller rc=${rc}`);
		}
	});

	test("poller rc 0 but agent fails → state stays byte-identical, critical notify", () => {
		const c = setup();
		writeFileSync(c.stateFile, "2026-09-09T18:30:00.000Z");
		const r = c.run({ pollRc: 0, agentRc: 1 });
		expect(r.rc).toBe(0);
		expect(lines(c.pollMarker)).toBe(1);
		expect(lines(c.agentMarker)).toBe(1);
		expect(readFileSync(c.stateFile, "utf8")).toBe("2026-09-09T18:30:00.000Z");
		expect(content(c.notifyLog)).toContain("Run fallido");
		expect(r.log).toContain("state not advanced");
	});

	test("PAUSE file present → neither poller nor agent runs", () => {
		const c = setup();
		writeFileSync(c.pauseFile, "");
		const r = c.run({ pollRc: 0, agentRc: 0 });
		expect(r.rc).toBe(0);
		expect(existsSync(c.pollMarker)).toBe(false);
		expect(existsSync(c.agentMarker)).toBe(false);
		expect(existsSync(c.stateFile)).toBe(false);
		expect(r.log).toContain("[paused]");
	});

	test("lock held elsewhere → neither poller nor agent runs", async () => {
		const c = setup();
		const holder = Bun.spawn(["bash", "-c", `flock ${c.lockFile} sleep 10`], {
			stdout: "ignore",
			stderr: "ignore",
		});
		try {
			await Bun.sleep(300); // let the holder acquire the lock
			const r = c.run({ pollRc: 0, agentRc: 0 });
			expect(r.rc).toBe(0);
			expect(existsSync(c.pollMarker)).toBe(false);
			expect(existsSync(c.agentMarker)).toBe(false);
			expect(r.log).toContain("[skip]");
		} finally {
			holder.kill();
			await holder.exited;
		}
	});
});
