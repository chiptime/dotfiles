import { constants } from "node:fs";
import { open, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

export type ReadContext = {
	directory: string;
	ask(input: {
		permission: string;
		patterns: string[];
		always: string[];
		metadata: Record<string, unknown>;
	}): Promise<void>;
};

export function contains(root: string, target: string) {
	const rel = relative(root, target);
	return (
		rel !== "" &&
		rel !== ".." &&
		!rel.startsWith(`..${sep}`) &&
		!isAbsolute(rel)
	);
}

export function scopedReader(roots: string[]) {
	return async (
		args: { filePath: string; offset?: number; limit?: number },
		ctx: ReadContext,
	) => {
		const denied = () => new Error("Scoped read denied");
		if (process.platform !== "linux" || typeof args.filePath !== "string")
			throw denied();
		const requested = resolve(ctx.directory, args.filePath);
		if (!roots.some((root) => contains(root, requested))) throw denied();
		const offset = args.offset ?? 1;
		const limit = args.limit ?? 2000;
		if (
			!Number.isInteger(offset) ||
			offset < 1 ||
			!Number.isInteger(limit) ||
			limit < 1 ||
			limit > 2000
		)
			throw denied();
		// Pin the inode before checking its actual target. Native ReadTool reopens by
		// pathname; this tool never does. NONBLOCK prevents a planted FIFO from hanging.
		const file = await open(
			requested,
			constants.O_RDONLY | constants.O_NONBLOCK,
		).catch(() => {
			throw denied();
		});
		try {
			const actual = await realpath(`/proc/self/fd/${file.fd}`).catch(() => {
				throw denied();
			});
			if (!roots.some((root) => contains(root, actual))) throw denied();
			const stat = await file.stat();
			if (!stat.isFile() || stat.nlink !== 1 || stat.size > 20 * 1024 * 1024)
				throw denied();
			// Match the v1.18.30 native external-directory convention (absolute dir
			// glob). Keep OpenCode's permission engine, without enabling native read.
			await ctx.ask({
				permission: "external_directory",
				patterns: [join(dirname(actual), "*")],
				always: [],
				metadata: {},
			});
			// teams_read is owned by this plugin: both the ask pattern and the config
			// rule are the absolute resolved path, so the pair is self-consistent and
			// immune to the CLI's per-session worktree (which is the run's work
			// directory, not "/"). The worktree-relative "/" convention applies only
			// to the NATIVE read tool's internal resource derivation, which stays deny.
			await ctx.ask({
				permission: "teams_read",
				patterns: [actual],
				always: [],
				metadata: {},
			});
			// Bound allocation even if another same-user writer grows the file.
			const bytes = Buffer.alloc(stat.size + 1);
			let length = 0;
			while (length < bytes.length) {
				const read = await file.read(
					bytes,
					length,
					bytes.length - length,
					length,
				);
				if (!read.bytesRead) break;
				length += read.bytesRead;
			}
			if (length > stat.size) throw denied();
			const data = bytes.subarray(0, length);
			const mime = data
				.subarray(0, 8)
				.equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
				? "image/png"
				: data[0] === 255 && data[1] === 216 && data[2] === 255
					? "image/jpeg"
					: /^GIF8[79]a/.test(data.subarray(0, 6).toString())
						? "image/gif"
						: data.subarray(0, 4).toString() === "RIFF" &&
							  data.subarray(8, 12).toString() === "WEBP"
							? "image/webp"
							: undefined;
			if (mime)
				return {
					output: "Image read successfully",
					attachments: [
						{
							type: "file" as const,
							mime,
							url: `data:${mime};base64,${data.toString("base64")}`,
						},
					],
				};
			if (data.includes(0)) throw denied();
			const lines = data.toString("utf8").split("\n");
			const selected: string[] = [];
			let size = 0;
			for (
				let i = offset - 1;
				i < Math.min(lines.length, offset - 1 + limit);
				i++
			) {
				const line = `${i + 1}: ${lines[i]!.slice(0, 2000)}`;
				if (size + Buffer.byteLength(line) > 48 * 1024) break;
				selected.push(line);
				size += Buffer.byteLength(line) + 1;
			}
			const next = offset + selected.length;
			return {
				output:
					selected.join("\n") +
					(next <= lines.length
						? `\nContinue with offset=${next}.`
						: "\nEnd of file."),
			};
		} finally {
			await file.close();
		}
	};
}
