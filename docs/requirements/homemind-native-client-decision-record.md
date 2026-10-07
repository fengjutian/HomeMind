# HomeMind native client — decision record

**Status:** not started. PWA accepted; this is the record of what a
native client would still need to settle, and why none of it is
implemented yet.

The rule from the remaining-features plan is deliberate: a React
Native or Flutter project opened before the PWA works would be a
rewrite waiting to happen, and a push pipeline pointed at a household
app with no users is a liability. Everything below is the checklist that
must be answered *before* the first line of native code.

## Why not now

The PWA already covers the surfaces a family uses daily — photos,
calendar, tasks, approvals, notifications — at 360px. What it cannot
cover is background work: a phone that is locked, on a metered
connection, in a pocket. That gap is real, and it is exactly the gap a
native client exists to close. It is not a gap that justifies guessing
at a signing key, a store account, and a push certificate before
anyone has used the product for a month.

## What must be decided first

| Decision | Why it blocks | Options |
|---|---|---|
| Code repository | A second repo means a second CI, a second release train, and a second place for a security fix to be missing from. | Same repo under `clients/`, or a separate repo with a shared API package. |
| Signing key custody | An iOS distribution key cannot be rotated; whoever holds it holds the app. | Platform account owner; a documented escrow; a third-party signer (not recommended for a family product). |
| Push delivery | APNs and FCM both need server-side credentials, and both leak content into a third party's logs. | APNs + FCM directly; a relay such as OneSignal; or APNs-only and accept that Android users get polling. |
| Secure storage | The JWT and refresh token must survive a reboot without being readable from a backup or a screenshot. | iOS Keychain + Android EncryptedSharedPreferences, or a wrapper such as `flutter_secure_storage`. |
| Photo library access | Reading the camera roll needs a permission prompt whose wording is a privacy statement, not a UI string. | Per-platform prompts with the reason spelled out; no blanket access. |
| Background upload | The PWA's limit is uploading in the foreground. A native client can upload with the screen off. | BackgroundTasks / WorkManager, with the same size cap until resumable upload exists. |
| Release channel | TestFlight and Play internal testing both need real accounts before a public listing makes sense. | Private channel first; public only after the support path exists. |

## What the server must provide first

The native client cannot ship against the current contract unchanged:

- **Push registration.** A table mapping `(user_id, device_token,
  platform)` to a user, so the server can address one device rather
  than every session. The plan explicitly defers this table — adding
  one before anything uses it means a migration to fix its shape.
- **Resumable upload.** The plan defers this too. A native client
  uploading a 200 MB video over a phone connection needs chunking with
  a resume token; without it, a dropped connection means starting over.
- **Deep links.** A notification tap should land on the specific task
  or event. `target_type` / `target_id` are already stored, so the
  routing table is small — but it needs to be specified before an app
  codes against it.

## What is deliberately not planned

- **A separate design language.** The native client renders the same
  server data; a second visual system doubles the surface a family has
  to learn for no gain.
- **Offline-first writes.** Without a conflict model for a household
  editing the same task from two phones, offline writes produce silent
  last-write-wins losses. Reads can be cached; writes wait.
- **Background agent execution.** The scheduler runs server-side. A
  native client that started agent work in the background would move
  the audit trail off the server, which is exactly what the approval
  model depends on.

## Revisit trigger

Open this again when **any** of these is true:

1. PWA usage shows families hitting the foreground-upload limit.
2. A family explicitly asks for background work on a specific platform.
3. The push contract (§ first item above) is designed, which means
   the server work exists and a client has somewhere to register.

Until then, the PWA is the mobile product.