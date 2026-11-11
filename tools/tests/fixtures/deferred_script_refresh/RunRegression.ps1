[CmdletBinding()]
param([string]$Baseline)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$work = Join-Path $root '.tmp/t3-deferred-script-refresh'
New-Item -ItemType Directory -Path $work -Force | Out-Null
function Read-Source([string]$relative) {
    if ($Baseline) {
        $text = & git -C $root show ($Baseline + ':' + $relative)
        if ($LASTEXITCODE -ne 0) { throw ('Cannot read baseline: ' + $relative) }
        return ($text -join "`n")
    }
    return [IO.File]::ReadAllText((Join-Path $root $relative))
}
$source = Read-Source 'MCPForUnity/Editor/Tools/ManageScript.cs'
$start = $source.IndexOf('    // Debounced refresh/compile scheduler')
if ($start -lt 0) { throw 'Refresh helper source boundary not found' }
$imports = @'
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Runtime.Helpers;
using UnityEditor;
namespace MCPForUnity.Editor.Tools {
'@
[IO.File]::WriteAllText((Join-Path $work 'Refresh.cs'), $imports + "`n" + $source.Substring($start))
foreach ($file in @('OwnedCompilationReservation.cs', 'Transport/NativeOperationBarriers.cs')) {
    [IO.File]::WriteAllText((Join-Path $work ([IO.Path]::GetFileName($file))), (Read-Source ('MCPForUnity/Editor/Services/' + $file)))
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Program.cs') -Destination $work
$json = [Security.SecurityElement]::Escape((Join-Path $root '.compile-refs/Newtonsoft.Json.dll'))
$project = @"
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net11.0</TargetFramework><DefineConstants>UNITY_EDITOR</DefineConstants><NoWarn>0649;0067</NoWarn></PropertyGroup>
  <ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>$json</HintPath></Reference></ItemGroup>
</Project>
"@
[IO.File]::WriteAllText((Join-Path $work 'Harness.csproj'), $project)
Write-Output 'MANAGED SEAMS: actual refresh scheduler, compilation reservation and admission barriers; simulated Unity lifecycle; no Editor launched'
& dotnet run --project (Join-Path $work 'Harness.csproj') -c Release
exit $LASTEXITCODE
