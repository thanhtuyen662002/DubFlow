# ADR-0043: AI keys saved in Windows Credential Manager

Status: Proposed, Issue #366. Supersedes the session-only key rule of ADR-0042 (and of the posting-suggestion feature before it).

## Context
DubFlow kept the user's Claude or Gemini key in memory only. After every restart the user had to find the key and paste it again. Users expect to enter it once, in settings.

Keeping a key in WebView `localStorage` (as rc7 once did) or in a plain file would leave it readable by anything that reads that profile or folder.

## Decision
1. **Storage.** The Tauri host saves each provider's key as a generic credential in Windows Credential Manager:
   - target `DubFlow/ai-key/<claude|gemini>`;
   - `CRED_PERSIST_LOCAL_MACHINE`, so it stays on this machine and does not roam;
   - Windows encrypts it with DPAPI for the signed-in Windows user;
   - written through `windows-sys` (`CredWriteW`, `CredReadW`, `CredDeleteW`), which is already in the lockfile.

   Keys never go to WebView storage or to a file.
2. **Commands.** The host exposes:
   - `ai_key_status`: masked "…abcd" per provider;
   - `ai_key_get`, `ai_key_set`, `ai_key_delete`.

   Only `claude` and `gemini` are accepted, and a key must be 8–512 printable ASCII characters without spaces.
3. **Loading.** At start-up the webview loads the saved keys into memory (`AiSession.restore`) and uses each only for its own provider's request. The provider choice is not a secret and is kept in `localStorage`.
4. **Managing keys.** "Cài đặt AI" (sidebar) sets the default provider and shows, replaces or deletes each saved key. The key prompts in the features save too, and say where the key is kept and how to remove it.
5. **Failures.** A key the host could not save is not kept in memory, and the error is shown.

## Consequences
- Anyone signed in as the same Windows user can use DubFlow's saved key, as with any credential that user saves.
- Credential Manager is Windows-only. Elsewhere, saving reports that it is unsupported.
- The 1.1.12–1.1.15 diagnostic report never reads credentials, and nothing here adds them to it.
