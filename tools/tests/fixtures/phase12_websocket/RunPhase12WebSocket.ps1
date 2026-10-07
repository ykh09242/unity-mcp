[CmdletBinding()]
param(
    [string]$UnityData = 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Data',
    [string]$SdkPath = 'C:/Program Files/dotnet/sdk/10.0.401',
    [string]$DotnetPath = 'dotnet',
    [Parameter(Mandatory=$true)][string]$Evidence,
    [string]$Case = 'all'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$evidenceRoot = [IO.Path]::GetFullPath((Join-Path $root 'reports/CS-20261006-mcp-usability/phase12/websocket')) + [IO.Path]::DirectorySeparatorChar
$work = [IO.Path]::GetFullPath($Evidence)
if (!$work.StartsWith($evidenceRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Evidence must be below the owned phase12/websocket directory' }
New-Item -ItemType Directory -Path $work -Force | Out-Null
$framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
$references = @('mscorlib.dll','System.dll','System.Core.dll','System.Net.Http.dll') | ForEach-Object { Join-Path $framework $_ }
$references += Join-Path $framework 'Facades/netstandard.dll'
foreach ($name in @('Newtonsoft.Json.dll','nunit.framework.dll')) {
    $path = Join-Path $root ('.compile-refs/' + $name)
    $references += $path
    Copy-Item -LiteralPath $path -Destination $work
}
$sources = @(
    'MCPForUnity/Editor/Services/Transport/ConnectionCommandWork.cs',
    'MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs',
    'MCPForUnity/Editor/Services/Transport/TransportCommandDispatcher.cs',
    'MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs',
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
    'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/TransportArchitectureTests.cs',
    'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/CooperativeCancellationTests.cs',
    'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/CooperativeHandlerCancellationTests.cs',
    'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/BatchExecuteCancellationTests.cs',
    'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/TransportCommandDispatcherTests.cs',
    'TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/WebSocketTransportClientTests.cs',
    'tools/tests/fixtures/phase12_websocket/Phase12WebSocketHarness.cs'
) | ForEach-Object { Join-Path $root $_ }
$arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo','/noconfig','/nostdlib+','/target:exe','/main:Phase12WebSocketHarness','/langversion:latest',('/out:'+(Join-Path $work 'Phase12WebSocket.exe')))
$arguments += $references | ForEach-Object { '/reference:' + $_ }
$arguments += $sources
foreach ($source in $sources) { Write-Output ((Split-Path -Leaf $source) + '_SHA256: ' + (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash) }
& $DotnetPath @arguments
if ($LASTEXITCODE -ne 0) { throw 'Phase12 WebSocket compilation failed' }
$runtimeOutput = Join-Path $work 'runtime.stdout.log'
$runtimeError = Join-Path $work 'runtime.stderr.log'
$process = Start-Process -FilePath (Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe') `
    -ArgumentList @(('"' + (Join-Path $work 'Phase12WebSocket.exe') + '"'), $Case) `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput $runtimeOutput -RedirectStandardError $runtimeError
if (!$process.WaitForExit(30000)) {
    $process.Kill()
    $process.WaitForExit()
    Get-Content -LiteralPath $runtimeOutput
    Get-Content -LiteralPath $runtimeError
    throw 'Owned WebSocket fixture exceeded its 30-second process timeout'
}
$process.Refresh()
Get-Content -LiteralPath $runtimeOutput
Get-Content -LiteralPath $runtimeError
exit $process.ExitCode
