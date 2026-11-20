[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkPath,
    [string]$BaselinePath,
    [string]$UnityData = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data',
    [string]$SdkPath = 'C:/Program Files/dotnet/sdk/10.0.401',
    [string]$UnityDefines = 'UNITY_6000_0_OR_NEWER,UNITY_2023_1_OR_NEWER',
    [string]$DotnetPath = 'dotnet',
    [switch]$DisablePlanGuardControl
)
$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '../../../..')).Path
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$json = Join-Path $SdkPath 'Sdks/Microsoft.NET.Sdk/tools/net472/Newtonsoft.Json.dll'
New-Item -ItemType Directory -Force -Path $WorkPath | Out-Null
$WorkPath = (Resolve-Path -LiteralPath $WorkPath).Path
Copy-Item -LiteralPath $json -Destination $WorkPath
$arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', ('/define:UNITY_EDITOR,' + $UnityDefines), ('/out:' + (Join-Path $WorkPath 'BuildPreflight.exe')))
$references = @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'Facades/netstandard.dll') | ForEach-Object { Join-Path $framework $_ }
$references += $json
$arguments += $references | ForEach-Object { '/reference:' + $_ }
foreach ($relative in @('MCPForUnity/Editor/Tools/ManageBuild.cs', 'MCPForUnity/Editor/Tools/Build/BuildRunner.cs', 'MCPForUnity/Editor/Tools/Build/BuildTargetMapping.cs')) {
    $source = if ($BaselinePath) { Join-Path $BaselinePath ([IO.Path]::GetFileName($relative)) } else { Join-Path $repository $relative }
    Write-Output ('SOURCE_SHA256: ' + $relative + ' ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash)
    $arguments += $source
}
$planSource = Join-Path $repository 'MCPForUnity/Editor/Tools/Build/BuildPlan.cs'
if ($BaselinePath) {
    $baselinePlan = Join-Path $BaselinePath 'BuildPlan.cs'
    if (Test-Path -LiteralPath $baselinePlan) { $planSource = $baselinePlan }
}
Write-Output ('SOURCE_SHA256: MCPForUnity/Editor/Tools/Build/BuildPlan.cs ' + (Get-FileHash -LiteralPath $planSource -Algorithm SHA256).Hash)
$arguments += $planSource
$arguments += @(
    'MCPForUnity/Editor/Tools/Build/BuildJob.cs',
    'MCPForUnity/Editor/Tools/Build/BuildProfileApi.cs',
    'MCPForUnity/Editor/Tools/Build/BuildQueueCheckpoint.cs',
    'MCPForUnity/Editor/Helpers/ToolParams.cs',
    'MCPForUnity/Editor/Helpers/ParamCoercion.cs',
    'MCPForUnity/Editor/Helpers/StringCaseUtility.cs',
    'MCPForUnity/Runtime/Serialization/JsonScalarConversion.cs',
    'MCPForUnity/Editor/Helpers/Response.cs'
) | ForEach-Object { Join-Path $repository $_ }
$arguments += Join-Path $PSScriptRoot 'BuildPreflightHarness.cs'
Write-Output ('UNITY_DEFINES: ' + $UnityDefines)
& $DotnetPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Build preflight harness compilation failed' }
$harnessArguments = @($WorkPath)
if ($DisablePlanGuardControl) { $harnessArguments += '--disable-plan-guard-control' }
$mono = (Resolve-Path -LiteralPath (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe')).Path
Push-Location -LiteralPath $WorkPath
try {
    & $mono (Join-Path $WorkPath 'BuildPreflight.exe') @harnessArguments
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
