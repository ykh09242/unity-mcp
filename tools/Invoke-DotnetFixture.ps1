# Run the standalone PowerShell fixtures with their existing SDK prerequisite.
$ErrorActionPreference = 'Stop'
function Test-FixtureDirectory([string]$Directory) {
    $probe = Join-Path $Directory ('unity-mcp-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $stream = [IO.FileStream]::new($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None, 1, [IO.FileOptions]::DeleteOnClose)
    try { $stream.WriteByte(0) } finally { $stream.Dispose() }
}
try {
    $directory = [IO.Path]::GetTempPath()
    Test-FixtureDirectory $directory
    for ($i = 0; $i -lt $args.Count; $i++) {
        $argument = $args[$i]
        if ($argument -eq '--') { break }
        if ($argument -match '^--?p(?:roperty)?:(?:BaseIntermediateOutputPath|OutputPath)=(.*)$' -or $argument -match '^--artifacts-path=(.*)$') {
            $directory = $Matches[1]
        } elseif ($argument -eq '--artifacts-path') {
            $directory = $args[++$i]
        } else {
            continue
        }
        [void][IO.Directory]::CreateDirectory($directory)
        Test-FixtureDirectory $directory
    }
} catch {
    [Console]::Error.WriteLine("Cannot prepare .NET fixture directory '$directory'. " + $_.Exception.Message)
    exit 1
}
& dotnet run --disable-build-servers @args
exit $LASTEXITCODE
