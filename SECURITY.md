# Security Policy

## Supported use

This project is intended to run as a local service under your direct control.

Basic security expectations:
- Keep the service bound to localhost unless you have a specific reason not to.
- Do not commit `config.json`.
- Do not commit credential files.
- Review logs and rewrite rules before publishing customizations.
- Rotate credentials immediately if you suspect they may have been exposed.

## Reporting a vulnerability

If you find a security issue in the code or documentation, please report it privately to the maintainer before disclosing details publicly.

A helpful report includes:
- The affected file or component
- The potential impact
- Steps to reproduce the issue
- A suggested fix, if you have one

## Sensitive files to keep local

Examples of files that should never be committed to version control:
- `config.json`
- Local smoke-test configs
- Credential files under `~/.claude/`
- Internal session notes or incident reports
