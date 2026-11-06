# Prepared-script commit regression

Run `Server/.venv/Scripts/python.exe tools/tests/fixtures/prepared_script_commit/run.py --output .tmp/prepared-script-commit` from the repository root.
Use `--baseline <commit>` to run the unchanged scenarios against an earlier source revision.

The .NET 9 fixture extracts the complete current `ApplyPreparedScript` method and its
raw-byte/option helpers without rewriting them, and uses the real response classes.
Only asset path resolution, syntax validation and Editor refresh are controlled boundaries.
It exercises actual filesystem replacement, displaced backups, BOM preservation,
postcommit observation failure and later external writes. It needs the existing
`.compile-refs/Newtonsoft.Json.dll`; no Unity Editor or optional package is started.
This is managed filesystem evidence, not native Editor execution. Output and owned
test files remain under the specified repository `.tmp` directory.
