# Token API

Base URL: `https://YOUR_MAIL_HOST/api/v1`. [OpenAPI specification](openapi.json).

In **API tokens**, an administrator selects a mailbox, permissions, and allowed sender addresses. The secret is shown once. Default expiry is 90 days; choose 1–365 days or **Never**. A mailbox can have 20 active tokens. Tokens do not grant administrator access.

Send the secret in `Authorization: Bearer TOKEN` over HTTPS. Query-string tokens, browser sessions, and browser-origin requests are not accepted. This API is for servers and scripts; it has no CORS support. Keep tokens in your application's secret storage, never a browser bundle or source repository.

## Permissions

| Permission | Operations |
| --- | --- |
| `read` | List folders/messages, search, read without marking seen, download attachments/original messages |
| `send` | Submit mail from the token's explicitly selected sender addresses |
| `write` | Save/replace drafts, mark read/unread, star/unstar, move to Inbox/Archive/Junk/Trash |
| `delete` | Permanently delete a message already in Trash |

Permissions are independent. An address must remain allowed by both the token and the mailbox's current send-as grants. Receiving aliases alone do not authorize sending. Saving a draft does not grant permission to send it.

## List and read

The examples prompt for a token without placing it in shell history:

```bash
MAIL_URL=https://mail.example.com
read -r -s -p 'API token: ' MAIL_TOKEN; echo
curl --fail-with-body "$MAIL_URL/api/v1/messages?folder=inbox&page=1" \
  -H "Authorization: Bearer $MAIL_TOKEN"
```

A listing returns `items`, `folder`, `uidvalidity`, `page`, `pages`, and `total`. Pages contain up to 30 messages, newest UID first. Use `q` for an ASCII text search of up to 200 characters. Folder IDs are `inbox`, `sent`, `drafts`, `archive`, `trash`, and `junk`.

For every message-specific request, use the listing's **folder + uidvalidity + UID** together:

```bash
curl --fail-with-body "$MAIL_URL/api/v1/messages/42?folder=inbox&uidvalidity=123456" \
  -H "Authorization: Bearer $MAIL_TOKEN"
```

If a mailbox is recreated, an old UID may refer to a different message. The server rejects changed UIDVALIDITY with `409 stale_mailbox`; list again before acting. Moving a message changes its folder and may change its UID, so list the destination again. Pagination is a live view, not a snapshot. Reading does not change the Seen flag.

Download `/messages/42/attachments/INDEX?folder=inbox&uidvalidity=123456` using an attachment ID from the message. Use `original` instead of INDEX to download the message as an `.eml` file. Responses use attachment disposition and sanitized filenames.

## Send

```bash
curl --fail-with-body "$MAIL_URL/api/v1/send" \
  -H "Authorization: Bearer $MAIL_TOKEN" -H 'Content-Type: application/json' \
  --data '{"from":"notifications@example.com","to":"recipient@example.net","subject":"Status","body":"The job finished."}'
```

Fields are strings: `from`, `to`, `cc`, `bcc`, `subject`, and `body`. Recipient fields accept comma-separated addresses. `from` defaults to the mailbox address, which must be explicitly selected on the token when used. At least one recipient is required; total recipients are capped at 50. Bodies are plain text, at most 1 MiB; subjects at most 300 characters on one line.

For attachments, use multipart form data with the same fields and repeat the `attachments` field:

```bash
curl --fail-with-body "$MAIL_URL/api/v1/send" \
  -H "Authorization: Bearer $MAIL_TOKEN" \
  --form-string 'from=notifications@example.com' \
  --form-string 'to=recipient@example.net' --form-string 'subject=Report' \
  --form-string 'body=Attached.' --form 'attachments=@report.pdf'
```

Limits: 20 attachments, 16 MiB combined attachment content, 25 MiB encoded message, 2 MiB JSON request or 24 MiB multipart request. Attachments are never executed by the web interface. Outgoing Bcc addresses are omitted from delivered headers and retained in the sender's Sent copy.

`202` with `status: "accepted"` means the local SMTP server accepted the message for delivery; it does not prove delivery to the recipient. The response includes `message_id`, `refused` recipient addresses, and an optional `warning` if saving the Sent copy failed. Partial recipient refusals must be handled individually. Do not resend the whole message to recipients already accepted.

`502 send_uncertain` includes `do_not_retry: true`: the connection failed during submission and acceptance is unknown. Check delivery or ask the operator to inspect the queue. A client timeout also leaves the outcome unknown. There is no idempotency-key support; never automatically retry a send after an ambiguous network failure.

## Drafts and message actions

`POST /drafts` accepts the same JSON/multipart fields as sending, but recipients may be empty. It requires `write`, saves to Drafts, and returns the new `uid` and `uidvalidity`. To replace a draft, also provide `draft_uid` and its `uidvalidity`. A successful replacement removes the previous draft; inspect any returned warning before retrying.

```bash
curl --fail-with-body "$MAIL_URL/api/v1/messages/42/action" \
  -H "Authorization: Bearer $MAIL_TOKEN" -H 'Content-Type: application/json' \
  --data '{"folder":"inbox","uidvalidity":"123456","action":"trash"}'
```

Actions: `read`, `unread`, `star`, `unstar`, `archive`, `trash`, `inbox`, `junk`. Action `delete` requires the separate `delete` permission and `folder: "trash"`; it is irreversible. Successful actions return `ok: true` and an optional `warning`, including if an audit record could not be saved after the operation completed.

## Python

This example uses only the Python standard library. Provide `MAIL_URL` and `MAIL_TOKEN` through the process environment; in the Bash session above, run `export MAIL_URL MAIL_TOKEN` first:

```python
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

base = os.environ["MAIL_URL"].rstrip("/")
token = os.environ["MAIL_TOKEN"]
query = urlencode({"folder": "inbox", "page": 1})
request = Request(base + "/api/v1/messages?" + query,
                  headers={"Authorization": "Bearer " + token})
with urlopen(request, timeout=30) as response:
    listing = json.load(response)
for message in listing["items"]:
    print(message["uid"], message["subject"])
```

Poll at a modest interval, such as once per minute, and track folder/UIDVALIDITY/UID. The server does not provide webhooks or background indexing. Handle deletions and mailbox generation changes during subsequent polls.

## Errors and limits

Errors have the shape `{"error":{"code":"insufficient_scope","message":"…"}}`.

| HTTP | Meaning |
| --- | --- |
| 400 | Invalid fields, folder, search, or missing UIDVALIDITY |
| 401 | Missing, invalid, expired, or revoked token |
| 403 | Permission denied, sender forbidden, insecure transport, or browser request |
| 404 | Message no longer exists |
| 409 | Stale mailbox generation; refresh the listing |
| 413 | Request body too large |
| 422 | All recipients refused |
| 429 | Rate limit; honor `Retry-After` |
| 502 | Mail service error or uncertain submission; inspect the error code |

Each token permits 120 requests per minute. Mailbox sending is limited to 60 submissions per hour across web/API traffic; the native sender guard also enforces the mailbox quota. Issuing additional tokens does not increase that sending allowance. Invalid authentication is limited by source IP. Last-used timestamps are updated at most once per minute.

## Replacement, revocation, and recovery

Use **Replace** to prefill a new token with the same mailbox and permissions. Store and deploy the new secret, verify it works, then revoke the old token. Revocation prevents subsequent operations. Password changes/resets and account disablement revoke all its tokens permanently; re-enabling an account does not restore them.

An operation already in progress may finish. The SMTP guard rechecks expiry/revocation/permissions at the end of DATA, but mail already accepted into the queue cannot be recalled. Public SMTP and IMAP reject the separate internal token credentials. Backups omit API tokens and web sessions; issue new tokens after restoring a backup.

The `0.3.0` API targets fresh installations. Existing `0.2` installations need a separately reviewed database and mail-configuration migration; replacing application files alone is insufficient.
