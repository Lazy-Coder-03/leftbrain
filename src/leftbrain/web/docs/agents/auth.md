# Authenticating: a guide for agents

This page is written for a model, not a person. It is the whole of what leftbrain expects, in the
order you will need it. Every path below is relative to `https://leftbrain.idlesync.in`.

Read the section that matches your situation and stop.

## 1. You were given a key

If you hold a string starting `lblz_`, send it and you are done. There is no OAuth to do.

:::request
```http
POST /mcp
Authorization: Bearer lblz_YOUR_KEY
Accept: application/json, text/event-stream
Content-Type: application/json
```
:::

Everything else on this page exists for the case where nobody handed you one.

## 2. You got a 401

The body tells you what to do next, and the header tells you where the metadata is.

:::response
```json
{
  "ok": false,
  "error": "missing key",
  "message": "send Authorization: Bearer <key>",
  "how_to_authorize": {
    "if_you_have_a_browser": "https://leftbrain.idlesync.in/.well-known/oauth-protected-resource/mcp",
    "if_you_have_no_browser": "POST https://leftbrain.idlesync.in/oauth/device_authorization",
    "tell_your_user": "leftbrain needs authorising. I can give you a short code to approve at https://leftbrain.idlesync.in/device",
    "static_key_alternative": "https://leftbrain.idlesync.in/dashboard",
    "documentation": "https://leftbrain.idlesync.in/docs/agents/auth"
  }
}
```
:::

Discovery, if you are following the MCP specification rather than this page:

1. `GET /.well-known/oauth-protected-resource/mcp` — names the authorization server.
2. `GET /.well-known/oauth-authorization-server` — names `/authorize`, `/token`, `/register`,
   `/revoke` and `/oauth/device_authorization`.

Then choose section 3 or section 4 by whether you can open a browser **and** receive a redirect on
a loopback port. If either is false, use section 4.

## 3. You can open a browser

Standard OAuth 2.1 authorization code with PKCE. `S256` is required; `plain` is refused.

**Identify yourself.** Either host a Client ID Metadata Document and use its HTTPS URL as your
`client_id` (preferred — nothing to register, and it works against any server), or register:

:::request
```http
POST /register
Content-Type: application/json

{"redirect_uris": ["http://localhost/callback"],
 "client_name": "Your agent's name",
 "token_endpoint_auth_method": "none"}
```
:::

A loopback `redirect_uri` may come back on any port: register `http://localhost/callback` and
return on `http://localhost:51234/callback` if that is the port you got. Host, scheme and path
still have to match exactly.

**Then** send your user to `/authorize` with `response_type=code`, your `client_id`,
`redirect_uri`, `state`, `code_challenge` and `code_challenge_method=S256`. They will see a
consent page. When they approve, you get a `code` on your `redirect_uri`; exchange it:

:::request
```http
POST /token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&code=…&client_id=…&redirect_uri=…&code_verifier=…
```
:::

Access tokens last an hour. Refresh with `grant_type=refresh_token`; both tokens rotate, so store
the new refresh token and discard the old one.

## 4. You cannot open a browser

Use the device grant (RFC 8628). Register as in section 3 first, then:

:::request
```http
POST /oauth/device_authorization
Content-Type: application/x-www-form-urlencoded

client_id=…&scope=mcp
```
:::

:::response
```json
{"device_code": "…", "user_code": "WXYZ-1234",
 "verification_uri": "https://leftbrain.idlesync.in/device",
 "verification_uri_complete": "https://leftbrain.idlesync.in/device?code=WXYZ-1234",
 "expires_in": 600, "interval": 5}
```
:::

**Show your user the `verification_uri` and the `user_code` exactly as they came back.** Do not
paraphrase the code and do not reformat it. Say something like:

> To connect leftbrain, visit **https://leftbrain.idlesync.in/device** and enter the code
> **WXYZ-1234**. I'll wait.

Then poll `/token` every `interval` seconds with
`grant_type=urn:ietf:params:oauth:grant-type:device_code` and your `device_code`. Handle:

| `error` | what it means | what to do |
| --- | --- | --- |
| `authorization_pending` | they have not approved yet | keep polling |
| `slow_down` | you are polling too fast | add 5 seconds and continue |
| `access_denied` | they declined | stop, and tell them so |
| `expired_token` | ten minutes passed | start again from the top, with a new code |

## 5. What your user will see

A consent page naming you, showing where the result is sent, and listing the tools. When they
approve, leftbrain **creates an API key** for them, named after you and the machine they approved
from — `Your agent's name · Windows`; through the device grant, `Your agent's name · device`,
because the browser that approved need not be the machine you run on. It appears on their
dashboard alongside keys they made by
hand: readable, re-scopable, revocable, and counting against their key limit.

If you are told there are no key slots left, relay the message **verbatim**. It names the fix
(revoke a key at `/dashboard`), and "authorization failed" does not.

## 6. Once you know what you need, give the rest back

When you have learned which tools you actually call, ask to drop the others. This is something you
**should** do, not merely something you can.

:::request
```http
POST /keys/me/scope
Authorization: Bearer <your token or lblz_ key>
Content-Type: application/json

{"tools": ["math", "convert"]}
```
:::

The answer is **202 and a pending request**, not a change:

:::response
```json
{"ok": true, "result": {
  "status": "pending_approval",
  "approve_url": "https://leftbrain.idlesync.in/keys/scope-request/…",
  "tell_your_user": "I would like to give up some of my own access to leftbrain, keeping only math, convert. Approve at …",
  "expires_in": 900,
  "check": "GET /keys/me"}}
```
:::

Show your user the `approve_url`. Nothing changes until they approve it. Read `GET /keys/me`
afterwards to see whether they did — do not poll the proposal, and do not assume it worked.

**You can only narrow.** Asking for a tool you do not already hold returns `403 forbidden` and
creates no request at all. Widening is the owner's decision, made on their dashboard. If you get
that 403, do not retry it — relay it and move on.

## 7. Reading a refusal

A call outside your scope is not a transport error. You get `200` with a contract failure:

:::response
```json
{"ok": false, "error": "forbidden",
 "message": "this key may not call convert; allowed: math"}
```
:::

The message names what you may call. Read it and stop; do not retry the same call. If it says the
server does not provide the tools your key is scoped to, your key was scoped for a different
deployment — tell your user to re-scope it at `/dashboard`.
