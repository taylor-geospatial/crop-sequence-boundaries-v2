# Security Policy

## Supported Versions

Security fixes are applied to the latest minor release on the `main` branch.
Older tagged releases are not backported unless a downstream user with a
specific deployment requests it.

| Version | Supported     |
| ------- | ------------- |
| 0.1.x   | Yes (current) |
| < 0.1   | No            |

## Reporting a Vulnerability

Please report suspected vulnerabilities through one of:

- GitHub Security Advisories (preferred): use the "Report a vulnerability"
    button under the Security tab of this repository. This creates a private
    channel between you and the maintainers.
- Email: `isaac.corley@taylorgeospatial.org`. Use the subject prefix
    `[csb-security]`. PGP is not currently offered; do not include exploit
    payloads or sensitive data in plaintext email.

Please include:

- A description of the issue and its impact.
- Steps to reproduce, including affected version (`csb --version`) and
    Docker image digest if applicable.
- Any known mitigations or workarounds.

## Coordinated Disclosure Timeline

- Acknowledgement of receipt: within 5 business days.
- Initial triage and severity assessment: within 10 business days.
- Target fix or mitigation: within 90 days of acknowledgement. Complex
    issues that require upstream coordination (e.g. GDAL, rasterio,
    tippecanoe) may take longer; the reporter will be kept informed.
- Public disclosure: coordinated with the reporter, normally after a
    patched release is available. The 90-day clock applies regardless of
    whether a fix has shipped, consistent with common federal disclosure
    norms.

Credit is given in the release notes unless the reporter requests anonymity.

## Scope

In scope:

- The `csb` Python package source under `src/`.
- The published wheel and sdist on PyPI.
- The container image published to `ghcr.io/<repo>/csb` from this
    repository's `Dockerfile`.
- Build and release workflows under `.github/workflows/`.

Out of scope:

- The upstream USDA Cropland Data Layer rasters and any NASS, USDA, or
    CropScape infrastructure. Issues with that data or its hosting must be
    reported directly to USDA NASS.
- Vulnerabilities in transitive dependencies that are already tracked by
    the upstream project; please report those upstream and reference the
    advisory here.
- Denial-of-service caused by user-supplied raster inputs that exceed
    documented memory or disk requirements.

## Non-Vulnerabilities

The following are not treated as security vulnerabilities:

- Missing rate limiting on local CLI commands.
- Output file permissions matching the invoking user's umask.
- Long pipeline runtimes on inputs outside the documented size envelope.
