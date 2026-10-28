param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$SourcePath,
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (-not $SourcePath) { $SourcePath = Join-Path $repositoryRoot 'MCPForUnity/Editor/Tools/ManageEditor.cs' }
if (-not $OutputRoot) { $OutputRoot = Join-Path $repositoryRoot '.tmp/deployment-consent-regression' }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$references = @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { Join-Path $framework $_ }
$references += Join-Path $framework 'Facades/netstandard.dll'
foreach ($path in @(
    (Join-Path $repositoryRoot '.compile-refs/Newtonsoft.Json.dll'),
    (Join-Path $repositoryRoot '.compile-refs/nunit.framework.dll'),
    (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.CoreModule.dll'),
    (Join-Path $UnityData 'Managed/UnityEngine/UnityEngine.SharedInternalsModule.dll'),
    (Join-Path $UnityData 'Managed/UnityEngine/UnityEditor.CoreModule.dll')
)) {
    $references += $path
    Copy-Item -LiteralPath $path -Destination $OutputRoot -Force
}
$arguments = @(
    (Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/langversion:latest', '/nowarn:0436,0649',
    ('/out:' + (Join-Path $OutputRoot 'ConsentHarness.exe'))
)
$arguments += $references | ForEach-Object { '/reference:' + $_ }
$arguments += @(
    $SourcePath,
    (Join-Path $PSScriptRoot 'ConsentHarness.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Editor/Services/ToolConsentService.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Editor/Services/IPackageDeploymentService.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Editor/Constants/EditorPrefKeys.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Editor/Helpers/ToolParams.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Editor/Helpers/ParamCoercion.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Editor/Helpers/StringCaseUtility.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Editor/Helpers/Response.cs'),
    (Join-Path $repositoryRoot 'MCPForUnity/Runtime/Serialization/JsonScalarConversion.cs'),
    (Join-Path $repositoryRoot 'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/ManageEditorDeploymentConsentTests.cs')
)
Write-Output ('MANAGE_EDITOR_SHA256: ' + (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash)
& dotnet @arguments
if ($LASTEXITCODE -ne 0) { throw 'Consent harness compilation failed' }
Write-Output 'MANAGED_EXECUTION: real ManageEditor deployment dispatch with synthetic services; no native Unity calls'
& (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') (Join-Path $OutputRoot 'ConsentHarness.exe')
exit $LASTEXITCODE
