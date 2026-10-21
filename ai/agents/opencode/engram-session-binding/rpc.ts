type ToolResult = { isError?: boolean; content?: { type: string; text?: string }[] };

export function toolData(result: ToolResult): Record<string, any> {
  if (result.isError) throw new Error("Engram binding: registration tool rejected the request.");
  const text = result.content?.find((part) => part.type === "text")?.text;
  if (!text) throw new Error("Engram binding: registration returned no structured result.");
  return JSON.parse(text);
}

// Short-lived, local-only MCP connection. It uses the same binary/environment
// as the configured Engram server, but never enables remote autosync.
export async function connectEngram(
  directory: string,
  options: { environment?: Record<string, string | undefined>; timeoutMs?: number; binary?: string; tools?: string } = {},
) {
  const child = Bun.spawn([options.binary ?? "engram", "mcp", `--tools=${options.tools ?? "mem_current_project,mem_session_start"}`], {
    cwd: directory,
    env: { ...process.env, ...options.environment, ENGRAM_CLOUD_AUTOSYNC: "0" },
    stdin: "pipe", stdout: "pipe", stderr: "ignore",
  });
  const reader = child.stdout.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let sequence = 0;
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; child.kill("SIGKILL"); }, options.timeoutMs ?? 5000);
  const close = async () => {
    clearTimeout(timeout);
    child.kill();
    await reader.cancel().catch(() => {});
    await child.exited;
  };
  const send = async (message: object) => {
    child.stdin.write(JSON.stringify(message) + "\n");
    await child.stdin.flush();
  };
  const request = async (method: string, params: object): Promise<any> => {
    const id = ++sequence;
    await send({ jsonrpc: "2.0", id, method, params });
    while (true) {
      const newline = buffer.indexOf("\n");
      if (newline >= 0) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (!line.trim()) continue;
        const response = JSON.parse(line);
        if (response.id !== id) continue;
        if (response.error || response.result === undefined) throw new Error("Engram binding: MCP registration request failed.");
        return response.result;
      }
      if (buffer.length > 65536) throw new Error("Engram binding: MCP registration response is too large.");
      const chunk = await reader.read();
      if (chunk.done) throw new Error(timedOut ? "Engram binding: registration timed out." : "Engram binding: MCP closed before registration completed.");
      buffer += chunk.value;
    }
  };
  try {
    await request("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "opencode-engram-session-binding", version: "1" } });
    await send({ jsonrpc: "2.0", method: "notifications/initialized" });
    return {
      call: (name: string, args: object): Promise<ToolResult> => request("tools/call", { name, arguments: args }),
      close,
    };
  } catch (error) {
    await close();
    throw error;
  }
}
