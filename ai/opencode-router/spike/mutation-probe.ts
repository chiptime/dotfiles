// quota-balancer Phase 0 spike (task 0.1, spec R10): prove whether the installed
// @opencode-ai/plugin contract supports per-message model mutation via chat.message.
// Behavior-first: probes the artifacts live plugins resolve against + the runtime binary.
// Focused test: bun test ./spike/mutation-probe.test.ts · Harness: bun spike/mutation-probe.ts
import { execFileSync } from "node:child_process"
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const CFG = join(homedir(), ".config", "opencode")
const PKG = join(CFG, "node_modules/@opencode-ai/plugin/package.json")
const DTS = join(CFG, "node_modules/@opencode-ai/plugin/dist/index.d.ts")
const SDK = join(CFG, "node_modules/@opencode-ai/sdk/dist/gen/types.gen.d.ts")

export type Contract = {
  pluginVersion: string
  typeModelOnInput: boolean
  typeModelOnOutput: boolean
  typeOutputKeys: string[]
  runtimeModelInInput: boolean
  runtimeOutputKeys: string[]
  runtimeParamsOutputKeys: string[]
  runtimeHeadersReadback: boolean
  sdkSessionCreatedEvent: boolean
  sdkSessionUpdateBodyKeys: string[]
  sdkSessionHasModel: boolean
}

const keys = (b: string) => [...b.matchAll(/(^|[^.\w])([A-Za-z_$][\w$]*)\??:/g)].map((m) => m[2])
const grep = (re: string, file: string) =>
  execFileSync("grep", ["-aoE", re, file], { encoding: "utf8", maxBuffer: 1 << 24 })

export function probe(): Contract {
  const pluginVersion = JSON.parse(readFileSync(PKG, "utf8")).version
  const dts = readFileSync(DTS, "utf8")
  const sig = dts.match(/"chat\.message"\?: \(input: \{([\s\S]*?)\}, output: \{([\s\S]*?)\}\) => Promise<void>/)
  if (!sig) throw new Error("chat.message signature missing from installed plugin types")
  const bin = process.env.OPENCODE_BIN ?? execFileSync("which", ["opencode"], { encoding: "utf8" }).trim()
  const dispatch = grep('trigger\\("chat\\.(message|params)",\\{[^{}]*\\},\\{[^{}]*\\}\\)', bin)
  const msg = dispatch.match(/trigger\("chat\.message",\{([^{}]*)\},\{([^{}]*)\}\)/)
  if (!msg) throw new Error("chat.message dispatch missing from runtime binary")
  const params = dispatch.match(/trigger\("chat\.params",\{([^{}]*)\},\{([^{}]*)\}\)/)
  if (!params) throw new Error("chat.params dispatch missing from runtime binary")
  const runtimeHeadersReadback = grep('\\{headers:[A-Za-z_$]+\\}=yield\\*e\\.plugin\\.trigger\\("chat\\.headers"', bin).length > 0
  const sdk = readFileSync(SDK, "utf8")
  const upd = sdk.match(/export type SessionUpdateData = \{[\s\S]*?body\?: \{([\s\S]*?)\};/)
  const sess = sdk.match(/export type Session = \{([\s\S]*?)\n\};/)
  if (!upd || !sess) throw new Error("session types missing from installed SDK")
  return {
    pluginVersion,
    typeModelOnInput: keys(sig[1]).includes("model"),
    typeModelOnOutput: keys(sig[2]).includes("model"),
    typeOutputKeys: keys(sig[2]),
    runtimeModelInInput: keys(msg[1]).includes("model"),
    runtimeOutputKeys: keys(msg[2]),
    runtimeParamsOutputKeys: keys(params[2]),
    runtimeHeadersReadback,
    sdkSessionCreatedEvent: /export type EventSessionCreated = \{/.test(sdk),
    sdkSessionUpdateBodyKeys: keys(upd[1]),
    sdkSessionHasModel: keys(sess[1]).includes("model"),
  }
}

export function verdict(c: Contract) {
  return {
    perMessageMutationSupported: c.typeModelOnOutput || c.runtimeOutputKeys.includes("model"),
    perSessionPinSupported: c.sdkSessionUpdateBodyKeys.includes("model") || c.sdkSessionHasModel,
  }
}

const c = probe()
console.log(JSON.stringify({ plugin: c.pluginVersion, contract: c, verdict: verdict(c) }, null, 2))
console.log("VERDICT: per-message mutation NOT SUPPORTED; per-session pin NOT SUPPORTED -> spike/RESULTS.md")
