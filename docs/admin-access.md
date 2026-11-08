# Admin access

Open `/admin/login` and enter the configured `ADMIN_TOKEN` to use the operations
page and registry moderation. The browser exchanges the credential through the
`X-Admin-Token` header. It never places the credential in a URL or cookie.

The browser session lasts 15 minutes. Changing or removing `ADMIN_TOKEN` revokes
existing admin sessions. Moderation and logout forms require the session's CSRF
token. Admin responses use `Cache-Control: no-store`.

API clients continue to send `X-Admin-Token` on every request. The browser session
does not authorize machine API endpoints. Query-string and form-body credentials
are rejected.
