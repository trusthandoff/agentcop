# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅ Yes     |
| < 0.2   | ❌ No      |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report security issues by emailing the maintainers directly:

- Shay Mizuno — via GitHub: [@shaymizuno](https://github.com/shaymizuno)

Include in your report:
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Any suggested mitigations you are aware of

You will receive an acknowledgement within 72 hours. We aim to release a patch
within 14 days of a confirmed vulnerability, and will credit reporters in the
release notes unless you request otherwise.

## Scope

agentcop is a forensic auditing library. The primary security concerns are:

- **Detector isolation** — detectors are pure functions and must not be able
  to exfiltrate data or cause side effects via the auditing pipeline.
- **Thread safety** — concurrent access to the `Sentinel` instance must not
  produce inconsistent state or data races.
- **Dependency vulnerabilities** — vulnerabilities in `pydantic` or optional
  adapter dependencies (OpenAI SDK, ddtrace, etc.) that affect agentcop users.

Out of scope: vulnerabilities in the systems agentcop is monitoring (LangChain,
LangGraph, etc.) — report those to the respective projects.
