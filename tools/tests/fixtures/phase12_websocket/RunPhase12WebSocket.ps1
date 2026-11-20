[CmdletBinding()]
param(
    [string]$UnityData = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data',
    [string]$SdkPath = 'C:/Program Files/dotnet/sdk/10.0.401',
    [string]$DotnetPath = 'dotnet',
    [Parameter(Mandatory=$true)][string]$Evidence,
    [string]$Case = 'all',
    [ValidateRange(1, 2147483647)][int]$ProcessTimeoutMilliseconds = 30000,
    [ValidateRange(1, 2147483647)][int]$CleanupTimeoutMilliseconds = 10000
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$work = [IO.Path]::GetFullPath($Evidence)
New-Item -ItemType Directory -Path $work -Force | Out-Null
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$references = @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'System.Numerics.dll', 'System.Net.Http.dll') | ForEach-Object { Join-Path $framework $_ }
$references += Join-Path $framework 'Facades/netstandard.dll'
foreach ($path in @(
    (Join-Path $SdkPath 'Sdks/Microsoft.NET.Sdk/tools/net472/Newtonsoft.Json.dll'),
    (Join-Path $UnityData 'Resources/PackageManager/BuiltInPackages/com.unity.ext.nunit/net40/unity-custom/nunit.framework.dll')
)) {
    $references += $path
    Copy-Item -LiteralPath $path -Destination $work
}
$sources = @(
    'MCPForUnity/Editor/Constants/ProductInfo.cs',
    'MCPForUnity/Editor/Helpers/CommandOutcome.cs',
    'MCPForUnity/Editor/Helpers/OperationTimeouts.cs',
    'MCPForUnity/Editor/Services/EditorMainThreadWakeup.cs',
    'MCPForUnity/Editor/Services/ServerVersionDiagnostics.cs',
    'MCPForUnity/Editor/Services/ToolOperationIdentity.generated.cs',
    'MCPForUnity/Editor/Services/ToolOperationPolicy.generated.cs',
    'MCPForUnity/Editor/Services/ToolSettingsState.cs',
    'MCPForUnity/Editor/Services/ToolSettingsRegistration.cs',
    'MCPForUnity/Editor/Services/Transport/BoundedResponseJson.cs',
    'MCPForUnity/Editor/Services/Transport/CommandPhaseTiming.cs',
    'MCPForUnity/Editor/Services/Transport/CommandProgress.cs',
    'MCPForUnity/Editor/Services/Transport/CommandResumeJournal.cs',
    'MCPForUnity/Editor/Services/Transport/ConnectionCommandWork.cs',
    'MCPForUnity/Editor/Services/Transport/NativeCommandArbitrator.cs',
    'MCPForUnity/Editor/Services/Transport/PlainObjectJsonWriter.cs',
    'MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs',
    'MCPForUnity/Editor/Services/Transport/TransportCommandDispatcher.cs',
    'MCPForUnity/Editor/Services/Transport/TransportJson.cs',
    'MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs',
    'MCPForUnity/Editor/Services/Transport/WebSocketMessageReader.cs',
    'MCPForUnity/Editor/Services/Transport/LargeResultWriter.cs',
    'MCPForUnity/Editor/Services/Transport/TransportState.cs',
    'MCPForUnity/Editor/Services/Transport/IMcpTransportClient.cs',
    'MCPForUnity/Editor/Services/IToolDiscoveryService.cs',
    'MCPForUnity/Editor/Models/Command.cs',
    'MCPForUnity/Editor/Tools/CommandRegistry.cs',
    'MCPForUnity/Editor/Tools/BatchExecute.cs',
    'MCPForUnity/Editor/Helpers/ParamCoercion.cs',
    'MCPForUnity/Runtime/Serialization/JsonScalarConversion.cs',
    'MCPForUnity/Editor/Helpers/StringCaseUtility.cs',
    'MCPForUnity/Editor/Helpers/Response.cs',
    'tools/tests/fixtures/transport_architecture/UnityBoundaryStubs.cs',
    'tools/tests/fixtures/transport_architecture/LocalWebSocketPeer.cs',
    'tools/tests/fixtures/transport_architecture/CancellationSocketHarness.cs',
    'tools/tests/fixtures/transport_architecture/TransportArchitectureHarness.cs',
    'tools/tests/fixtures/phase12_websocket/Phase12Boundary.cs',
    'tools/tests/fixtures/phase12_websocket/Phase12WebSocketHarness.cs'
) | ForEach-Object { Join-Path $root $_ }
$arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+', '/target:exe', '/langversion:latest',
    ('/out:' + (Join-Path $work 'Phase12WebSocket.exe')))
$arguments += $references | ForEach-Object { '/reference:' + $_ }
$arguments += $sources
foreach ($source in $sources) { Write-Output ((Split-Path -Leaf $source) + '_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash) }
& $DotnetPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Phase12 WebSocket compilation failed' }
$runtimeOutput = Join-Path $work 'runtime.stdout.log'
$runtimeError = Join-Path $work 'runtime.stderr.log'
$process = Start-Process -FilePath (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') `
    -ArgumentList @(('"' + (Join-Path $work 'Phase12WebSocket.exe') + '"'), $Case) `
    -WorkingDirectory $work -WindowStyle Hidden -PassThru -RedirectStandardOutput $runtimeOutput -RedirectStandardError $runtimeError
if (!$process.WaitForExit($ProcessTimeoutMilliseconds)) {
    $cleanupFailure = $null
    try {
        $process.Kill()
        if (!$process.WaitForExit($CleanupTimeoutMilliseconds)) {
            $cleanupFailure = 'Owned WebSocket fixture exit could not be confirmed within the cleanup budget'
        }
    } catch {
        $cleanupFailure = 'Owned WebSocket fixture cleanup failed: ' + $_.Exception.Message
    } finally {
        Get-Content -LiteralPath $runtimeOutput
        Get-Content -LiteralPath $runtimeError
        $process.Dispose()
    }
    if ($null -ne $cleanupFailure) { throw $cleanupFailure }
    throw 'Owned WebSocket fixture exceeded its process timeout'
}
try {
    $process.Refresh()
    $result = $process.ExitCode
    Get-Content -LiteralPath $runtimeOutput
    Get-Content -LiteralPath $runtimeError
} finally {
    $process.Dispose()
}
exit $result
