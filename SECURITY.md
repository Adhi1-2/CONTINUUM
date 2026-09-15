# Security policy

## Reporting a vulnerability

Do not open a public issue.

Report privately using GitHub's
[private vulnerability reporting](https://github.com/Cyrax321/CONTINUUM/security/advisories/new),
or by email to `cyrax8590@gmail.com` with `SECURITY` in the subject line.

Please include:

- what the flaw allows an attacker or an unauthorised caller to do
- the steps or code needed to reproduce it
- the commit or version affected
- any known limitations of a fix you are proposing, if you are proposing one

You will get an acknowledgement. This is a pre-1.0 project maintained without
a dedicated security team, so response times are best-effort rather than
guaranteed.

## Supported versions

Only the latest commit on `main` is supported. There are no maintained release
branches, and no backports.

## Scope

This project is a local library, a CLI, and an MCP server backed by a SQLite
file. Anything that can already read or write that file can alter run state
directly and does not need to go through this code. Reports should assume that
baseline.

In scope:

- bypassing the MCP authorization layer for mutating tools
- causing state to be reported as verified when it is not
- corrupting or forging the event hash chain undetected
- duplicating an external side effect that the action ledger should have
  suppressed

Out of scope:

- impersonation via a forged `clientInfo` name alone. The authorization layer
  still distinguishes callers by their declared name. What changed: when
  `CONTINUUM_MCP_TOKEN` is set, the server also verifies a shared secret the
  caller presents in the handshake, so a hostile process cannot impersonate an
  authorized caller without that secret. See `src/continuum/mcp/authz.py` and
  `STATUS.md` issue #1.
- anything requiring existing write access to the database file

## Why this policy exists

A real example, already in this repository's history. PR #3 proposed an
authorization gate whose guard returned early, permitting the call, when the
request context was missing. The failure was reachable, the PR added no test of
its own gate, so CI passed, and its `Fixes #1` footer would have closed the
tracking issue on merge.

It was caught in public review, which worked, but only because someone happened
to read the guard closely. A finding of that shape is exactly what should come
through this channel instead: privately, with a reproduction, before the
weakness is described in a public thread.
