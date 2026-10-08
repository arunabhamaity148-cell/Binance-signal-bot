# Security policy and boundaries

## Design boundary

The shipped Binance REST client uses public unauthenticated GET endpoints from a fixed allowlist. The repository is intended to generate advisory signals and has no order placement/modification/cancellation/close, leverage, or transfer capability. Static scans check this boundary; scans are useful evidence, not a proof against every possible defect or dependency compromise.

Never add trading credentials, private key material, or execution endpoints. Report security issues privately to the repository maintainers; do not publish exploit details before coordinated remediation. Rotate any secret accidentally committed and remove it from history as appropriate.

## Deployment guidance

Use least privilege, pinned reviewed dependencies, non-root execution, read-only filesystem/configuration, restricted network egress, and secret-free environments. The included Docker image defaults to config validation and has no application live loop. Do not treat it as a trading or live signal service.

## Verification

Before accepting changes, run all tests and scanners from a clean extract. Review dependency changes and generated artifacts. No scanner can certify profitability, operational safety, or complete absence of malicious behavior.
