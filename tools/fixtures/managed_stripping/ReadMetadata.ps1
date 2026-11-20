param([Parameter(Mandatory)][string] $AssemblyPath)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Reflection.Metadata
$stream = [IO.File]::OpenRead($AssemblyPath)
try {
    $pe = [System.Reflection.PortableExecutable.PEReader]::new($stream)
    try {
        $reader = [System.Reflection.Metadata.PEReaderExtensions]::GetMetadataReader($pe)
        $definition = $reader.GetAssemblyDefinition()
        $assemblyName = $reader.GetString($definition.Name)
        if ($assemblyName -cne 'ManagedStripping.Runtime') {
            throw "Unexpected assembly name: $assemblyName"
        }
        $probe = $false
        $target = $false
        $method = $false
        foreach ($handle in $reader.TypeDefinitions) {
            $definition = $reader.GetTypeDefinition($handle)
            if ($reader.GetString($definition.Namespace) -cne 'ManagedStrippingFixture') { continue }
            switch -CaseSensitive ($reader.GetString($definition.Name)) {
                'ReflectionProbe' { $probe = $true }
                'ReflectionTarget' {
                    $target = $true
                    foreach ($methodHandle in $definition.GetMethods()) {
                        $methodDefinition = $reader.GetMethodDefinition($methodHandle)
                        if ($reader.GetString($methodDefinition.Name) -ceq 'Read') { $method = $true }
                    }
                }
            }
        }
        ConvertTo-Json -Compress -InputObject @{ probe = $probe; target = $target; method = $method }
    } finally {
        $pe.Dispose()
    }
} finally {
    $stream.Dispose()
}
