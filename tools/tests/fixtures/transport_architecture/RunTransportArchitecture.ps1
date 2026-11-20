[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$UnityData,
    [Parameter(Mandatory=$true)][string]$SdkPath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot '../phase12_websocket/RunPhase12WebSocket.ps1'
$work = Join-Path ([IO.Path]::GetTempPath()) ('transport-architecture-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    & $runner -UnityData $UnityData -SdkPath $SdkPath -DotnetPath $DotnetPath -Evidence $work -Case transport_architecture
    $result = $LASTEXITCODE
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('transport-architecture-')) { throw 'Refusing cleanup outside owned temporary fixture' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
exit $result
