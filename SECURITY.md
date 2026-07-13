# Security

Never commit `desktop_app/config.json`, the SQLite database, Pixela tokens,
Cloudflare Pages secrets, or Cloudflare Tunnel credentials.

If a credential is accidentally committed, remove it from the repository,
rotate it immediately at its provider, and review the Git history for other
copies. A later deletion commit does not remove a secret from earlier history.
