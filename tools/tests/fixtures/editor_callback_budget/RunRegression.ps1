[CmdletBinding()]
param([string]$Baseline)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$work = Join-Path $root '.tmp/t3-editor-callback-budget'
New-Item -ItemType Directory -Path $work -Force | Out-Null
$relative = 'MCPForUnity/Editor/Services/EditorMainThreadWakeup.cs'
if ($Baseline) {
    $lines = & git -C $root show ($Baseline + ':' + $relative)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read baseline scheduler' }
    $source = $lines -join "`n"
} else {
    $source = [IO.File]::ReadAllText((Join-Path $root $relative))
}
[IO.File]::WriteAllText((Join-Path $work 'EditorMainThreadWakeup.cs'), $source)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Program.cs') -Destination $work
$project = @'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net11.0</TargetFramework><NoWarn>0067</NoWarn></PropertyGroup>
</Project>
'@
[IO.File]::WriteAllText((Join-Path $work 'Harness.csproj'), $project)
Write-Output 'MANAGED SEAMS: actual callback scheduler; simulated Editor tick/update; no Editor launched'
& dotnet run --project (Join-Path $work 'Harness.csproj') -c Release
exit $LASTEXITCODE
