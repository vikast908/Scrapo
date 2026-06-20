# Security Policy

## Supported versions

Scrapo is beta (`0.x`). Security fixes land on the latest released minor version only.

## Reporting a vulnerability

Please report security issues privately. Use GitHub's **"Report a vulnerability"** button on the
Security tab of the repository (Security advisories), rather than opening a public issue or PR.

Include, if you can: affected version, a description of the issue, reproduction steps or a proof of
concept, and the impact you foresee. We will acknowledge the report, work on a fix, and credit you in
the release notes unless you prefer otherwise.

## Threat model and known limitations

Scrapo fetches arbitrary URLs and feeds page content to LLMs, and is meant to be embedded in apps and
driven by agents. Keep these in mind:

- **SSRF guard.** `scrapo` refuses to fetch loopback, link-local (including `169.254.169.254`),
  private RFC 1918 / ULA ranges, and well-known local hostnames *by host*, without DNS resolution.
  IP literals are parsed with `inet_aton`-style semantics, so obfuscated encodings of internal
  addresses (decimal `2130706433`, hex `0x7f000001`, short-form `127.1`, dotted-octal `0177.0.0.1`)
  are caught alongside the standard dotted-quad. A public hostname that *resolves* to an internal IP
  (DNS rebinding) is **not** caught here; if that is in your threat model, run scrapo behind an
  egress network policy. Set `allow_private_hosts=True` / `SCRAPO_ALLOW_PRIVATE_HOSTS=1` only when
  you intend to scrape internal services.
- **Agent tier (Tier 4) `goto` actions** chosen by the LLM go through the same SSRF guard, so a
  prompt-injected page cannot talk the agent into navigating to an internal target.
- **Prompt injection.** Page content goes into the extraction prompt. Schema validation limits the
  blast radius for structured extraction, but if you build agentic flows on top, treat fetched text
  as untrusted input.
- **`scrapo serve`.** Binds `127.0.0.1` by default and validates the `Host` header against an
  allowlist on loopback binds. Binding a public interface (`--host 0.0.0.0`) exposes an unauthenticated
  endpoint that makes the host fetch arbitrary URLs; do not do this on an untrusted network.
- **Stored snapshots may contain sensitive data.** Use `redact_snapshots=True` /
  `SCRAPO_REDACT_SNAPSHOTS=1` if you do not want PII persisted in the replay store.
- **The compliance features are tools, not guarantees.** You are responsible for complying with each
  site's terms of use and applicable law.
