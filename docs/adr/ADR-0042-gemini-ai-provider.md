# ADR-0042: Gemini as a second AI provider

Status: Proposed, Issue #362. Extends the session-only Claude key used by posting suggestions and product videos (ADR-0041). Key storage is superseded by ADR-0043: keys are now saved in Windows Credential Manager.

## Context
The desktop's AI features (posting suggestions; reading product photos and writing scripts) call Claude with the user's own key, from the webview. Some users have a Google Gemini key instead.

## Decision
1. **The user picks the provider beside each AI action:** Claude (Anthropic) or Gemini (Google).
   - The choice and one key per provider live only in memory for the app session (`createAiSession`).
   - Keys are never written to storage, and each key is used only for its own provider.
2. **One call shape.** `askJson(provider, key, {system, schema, parts, effort})` returns the parsed JSON answer.
   - **Claude:** the Anthropic SDK, as before (`claude-opus-5-5`, structured output).
   - **Gemini:** a plain `fetch` to `v1beta/models/gemini-3.8-flash:generateContent`, with no new dependency.
     - The key goes in the `x-goog-api-key` header, never in the URL.
     - The request has `systemInstruction`, user `parts` (text and `inlineData` JPEG), and `generationConfig.responseMimeType: application/json` with `responseSchema`. The schema is the same JSON Schema, converted to Gemini's subset: upper-case types, no `additionalProperties`.
     - Thought parts are skipped. Blocked prompts, safety stops and `MAX_TOKENS` become plain Vietnamese errors.
3. **Only what the user sends.** The key prompt names the provider that receives the data (subtitles, or photos and product facts) and bills the user. The CSP `connect-src` adds `https://generativelanguage.googleapis.com` only.

## Consequences
- Answers from the two providers can differ. Both go through the same normalizers (`normalizeSuggestion`, `normalizeInfo`, `normalizeScripts`), and the user reviews everything before use.
- The Gemini model id is pinned in one constant. Google retires models, so a later release may need to change it.
- The Gemini request was checked against recorded responses and the published request format, not against the live service. The first real use with a key is the live check.
