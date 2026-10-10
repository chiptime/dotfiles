// Focused test for the Phase 0 mutation probe (spec R10). Run: bun test ./spike/mutation-probe.test.ts
import { describe, expect, test } from "bun:test"
import { probe, verdict, type Contract } from "./mutation-probe"
const c: Contract = probe()
describe("quota-balancer Phase 0 (R10): installed mutation contract", () => {
  test("type surface: chat.message model is read-only input; output = {message, parts}", () => {
    expect(c.typeModelOnInput).toBe(true)
    expect(c.typeModelOnOutput).toBe(false)
    expect(c.typeOutputKeys).toEqual(["message", "parts"])
  })
  test("runtime: model rides the input arg; no read-back surface carries model", () => {
    expect(c.runtimeModelInInput).toBe(true)
    expect(c.runtimeOutputKeys).not.toContain("model")
    expect(c.runtimeParamsOutputKeys).toEqual(["temperature", "topP", "topK", "maxOutputTokens", "options"]) // read-back hook, still model-free
    expect(c.runtimeHeadersReadback).toBe(true) // control: output readback is real
  })
  test("fallback premise: session.created event exists; session.update is title-only", () => {
    expect(c.sdkSessionCreatedEvent).toBe(true)
    expect(c.sdkSessionUpdateBodyKeys).toEqual(["title"])
    expect(c.sdkSessionHasModel).toBe(false)
  })
  test("verdict gates tasks 3.x", () => {
    expect(verdict(c)).toEqual({ perMessageMutationSupported: false, perSessionPinSupported: false })
  })
})
