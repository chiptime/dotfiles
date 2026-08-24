/**
 * Pure renderer: template + prompt file → installed override config.
 * Replaces every "@prompt-file" marker with the prompt text; nothing else.
 */
export function renderTemplate(template: Record<string, any>, promptText: string): Record<string, any> {
	const out = structuredClone(template);
	for (const agent of Object.values(out.agent ?? {}) as Array<Record<string, any>>) {
		if (agent && agent.prompt === '@prompt-file') agent.prompt = promptText;
	}
	return out;
}
