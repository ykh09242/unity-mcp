# Package source diagnostics

Package mutation admission rejects embedded credentials and arbitrary query keys before
native Package Manager work. Credential-free SSH sources and supported Git selectors
remain accepted.

Legacy package job fields and nested response strings are redacted when projected for
storage or diagnostics. URL and SCP candidates extend through the next whitespace
boundary: quotes and angle brackets may be secret data, so punctuation does not end a
candidate. User information, query strings and fragments are removed. An ambiguous
diagnostic suffix may be omitted with the secret; host and path context is retained
where possible. Regex scans have a finite timeout and return a fully redacted value
when that budget is exceeded.

Regression coverage: `PackageSourceSecurityTests`, including punctuation-bearing
authority and query values in nested responses, persisted sources and job status.
