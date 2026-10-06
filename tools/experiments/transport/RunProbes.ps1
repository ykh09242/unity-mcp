param(
    [string]$OutputDirectory = "reports/CS-20261006-mcp-usability/phase7/prototypes"
)
$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../../.."))
$pythonPath = Join-Path $repoRoot "Server/.venv/Scripts/python.exe"
$fixtureDirectory = Join-Path $PSScriptRoot (".artifacts/" + [Guid]::NewGuid().ToString("N") + "/fixtures")
$outputPath = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputDirectory))
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
& dotnet restore (Join-Path $PSScriptRoot "dotnet/TransportProbe.csproj") --locked-mode --packages (Join-Path $PSScriptRoot ".artifacts/nuget")
if ($LASTEXITCODE) { throw "Experiment package restore failed" }
& dotnet build (Join-Path $PSScriptRoot "dotnet/TransportProbe.csproj") -c Release --no-restore
if ($LASTEXITCODE) { throw "Experiment build failed" }
& $pythonPath (Join-Path $PSScriptRoot "prepare.py") $fixtureDirectory
if ($LASTEXITCODE) { throw "Fixture generation failed" }
& $pythonPath (Join-Path $PSScriptRoot "python_server.py") export $fixtureDirectory
if ($LASTEXITCODE) { throw "Tool descriptor export failed" }
& (Join-Path $PSScriptRoot "dotnet/bin/Release/net10.0/TransportProbe.exe") raw $fixtureDirectory |
    Set-Content -LiteralPath (Join-Path $outputPath "raw-streams.json") -Encoding utf8
if ($LASTEXITCODE) { throw "Raw stream probe failed" }
& $pythonPath (Join-Path $PSScriptRoot "mcp_bench.py") (Join-Path $outputPath "mcp-sdk-stdio.json") $fixtureDirectory
if ($LASTEXITCODE) { throw "MCP SDK comparison failed" }
& $pythonPath (Join-Path $PSScriptRoot "startup_bench.py") (Join-Path $outputPath "startup.json") $fixtureDirectory
if ($LASTEXITCODE) { throw "Startup probe failed" }
