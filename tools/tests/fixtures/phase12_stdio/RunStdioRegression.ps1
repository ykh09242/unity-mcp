[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$UnityData,
    [Parameter(Mandatory = $true)][string]$SdkPath,
    [string]$HostSourcePath,
    [string]$EvidencePath,
    [string]$DotnetPath = 'dotnet'
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (!$HostSourcePath) { $HostSourcePath = Join-Path $root 'MCPForUnity/Editor/Services/Transport/Transports/StdioBridgeHost.cs' }
$HostSourcePath = (Resolve-Path -LiteralPath $HostSourcePath).Path
$work = Join-Path ([IO.Path]::GetTempPath()) ('phase12-stdio-' + [Guid]::NewGuid().ToString('N'))
$inputs = @($HostSourcePath,
    (Join-Path $root 'MCPForUnity/Editor/Models/Command.cs'),
    (Join-Path $root 'MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs'),
    (Join-Path $PSScriptRoot 'UnityBoundaryStubs.cs'),
    (Join-Path $PSScriptRoot 'StdioRegressionHarness.cs'),
    (Join-Path $PSScriptRoot 'RunStdioRegression.ps1'))
$hashes = @($inputs | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash } })
$output = @()
$result = 1
$fresh = $false
New-Item -ItemType Directory -Path $work | Out-Null
try {
    # Injection changes only a constant initialization, not either tested method.
    # It avoids real environment mutation and the production five-second minimum.
    $original = [IO.File]::ReadAllText($HostSourcePath)
    $needle = 'private static readonly int FrameIOTimeoutMs = ResolveFrameIOTimeoutMs();'
    if ([regex]::Matches($original, [regex]::Escape($needle)).Count -ne 1) { throw 'Timeout injection requires exactly one production declaration' }
    $injected = $original.Replace($needle, 'private static readonly int FrameIOTimeoutMs = 80;')
    $hostCopy = Join-Path $work 'StdioBridgeHost.cs'
    [IO.File]::WriteAllText($hostCopy, $injected)
    $injectedHash = (Get-FileHash -LiteralPath $hostCopy -Algorithm SHA256).Hash
    $framework = Join-Path $UnityData 'MonoBleedingEdge/lib/mono/4.5'
    $executable = Join-Path $work 'StdioRegression.exe'
    $json = Join-Path $root '.compile-refs/Newtonsoft.Json.dll'
    Copy-Item -LiteralPath $json -Destination $work
    $arguments = @((Join-Path $SdkPath 'Roslyn/bincore/csc.dll'), '/nologo', '/noconfig', '/nostdlib+',
        '/target:exe', '/langversion:latest', ('/out:' + $executable))
    $arguments += @('mscorlib.dll', 'System.dll', 'System.Core.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $arguments += '/reference:' + (Join-Path $framework 'Facades/netstandard.dll')
    $arguments += '/reference:' + $json
    $arguments += @($hostCopy) + $inputs[1..4]
    $hashes | ForEach-Object { Write-Output ($_.path + ' SHA256=' + $_.sha256) }
    Write-Output ('INJECTED_HOST_SHA256=' + $injectedHash + ' timeout_ms=80')
    $compileOutput = @(& $DotnetPath @arguments 2>&1 | ForEach-Object { $_.ToString() })
    $compileExit = $LASTEXITCODE
    $compileOutput | ForEach-Object { Write-Output $_ }
    if ($compileExit -ne 0) { throw 'Production stdio host fixture compilation failed' }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = Join-Path $UnityData 'MonoBleedingEdge/bin/mono.exe'
    $start.Arguments = '"' + $executable + '" "' + $HostSourcePath + '"'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $child = [Diagnostics.Process]::Start($start)
    try {
        $stdout = $child.StandardOutput.ReadToEndAsync()
        $stderr = $child.StandardError.ReadToEndAsync()
        if (!$child.WaitForExit(45000)) {
            # This is the exact process object launched above, never a name-based kill.
            $child.Kill()
            $child.WaitForExit(2000) | Out-Null
            $result = 1
            $output += 'FAIL: owned Mono fixture exceeded 45000 ms process watchdog and was terminated'
        } else { $result = $child.ExitCode }
        $output += @($stdout.GetAwaiter().GetResult().Split([Environment]::NewLine, [StringSplitOptions]::RemoveEmptyEntries))
        $output += @($stderr.GetAwaiter().GetResult().Split([Environment]::NewLine, [StringSplitOptions]::RemoveEmptyEntries))
    } finally { $child.Dispose() }
    $output | ForEach-Object { Write-Output $_ }
    $fresh = $true
    foreach ($entry in $hashes) {
        if ((Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash -ne $entry.sha256) { $fresh = $false }
    }
    if (!$fresh) { $result = 1; Write-Output 'FAIL: input source changed during test' }
} finally {
    $resolved = [IO.Path]::GetFullPath($work)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (!$resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or !(Split-Path -Leaf $resolved).StartsWith('phase12-stdio-')) {
        throw 'Refusing cleanup outside owned temporary fixture'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
    Write-Output 'CLEANUP: owned temporary compile directory removed'
    if ($EvidencePath) {
        $evidence = [IO.Path]::GetFullPath($EvidencePath)
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($evidence)) | Out-Null
        @{ sources = $hashes; injected_host_sha256 = $injectedHash; timeout_ms = 80;
            source_freshness_confirmed = $fresh; compile_exit_code = $compileExit;
            exit_code = $result; output = $output; temporary_directory_removed = !(Test-Path -LiteralPath $work);
            boundary = 'Complete production host/response/model; timeout constant injection only; token-observing NetworkStream on owned loopback socket; in-memory Unity and service boundaries; Unity Mono byte-array branch' } |
            ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $evidence -Encoding utf8
    }
}
exit $result
