/**
 * Minimal ambient types for `@opencode-ai/plugin`.
 *
 * Why ambient instead of the published package (decision 2026-09-09): the
 * versioned SDK's `Plugin` type has drifted from these plugins' contract — it
 * now requires a Promise-returning factory plus the full SDK client, while
 * quota-balancer is a synchronous factory and both plugins only touch two
 * hooks and two client methods. Adopting the real types would force a runtime
 * change (`async` on the quota-balancer factory) for a type-only gain. Plugin
 * files are bundled and loaded by the opencode runtime; these declarations are
 * editor/tsc-only and never ship. Keep this surface EXACTLY as wide as what
 * src/plugin/*.ts actually uses.
 */
declare module '@opencode-ai/plugin' {
	export interface PluginInput {
		directory: string;
		worktree: string;
		client: {
			tui: {
				showToast(input: { body: { message: string; variant: 'info' | 'warning' | 'error' } }): Promise<void>;
			};
			app: {
				log(input: { body: { service: string; level: 'debug' | 'info' | 'warn' | 'error'; message: string } }): Promise<void>;
			};
		};
	}
	export interface OpenCodeHooks {
		'chat.message'?: (
			input: { sessionID: string; agent?: string; model?: { providerID: string; modelID: string }; messageID?: string },
			output: { message: unknown; parts: readonly unknown[] },
		) => Promise<void>;
		'tool.execute.before'?: (
			input: { tool: string; sessionID: string; callID?: string },
			output: { args: Record<string, unknown> },
		) => Promise<void>;
	}
	// opencode accepts synchronous and asynchronous plugin factories; the union
	// mirrors that instead of the SDK's Promise-only shape.
	export type Plugin = (input: PluginInput) => OpenCodeHooks | Promise<OpenCodeHooks>;
}
