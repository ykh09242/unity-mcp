function Get-StdioProductionSources([string]$Root) {
    @(
        'MCPForUnity/Editor/Constants/ProductInfo.cs',
        'MCPForUnity/Editor/Helpers/OperationTimeouts.cs',
        'MCPForUnity/Editor/Models/Command.cs',
        'MCPForUnity/Editor/Services/Transport/BoundedResponseJson.cs',
        'MCPForUnity/Editor/Services/Transport/CommandPhaseTiming.cs',
        'MCPForUnity/Editor/Services/Transport/CommandProgress.cs',
        'MCPForUnity/Editor/Services/Transport/CommandResumeJournal.cs',
        'MCPForUnity/Editor/Services/Transport/ConnectionCommandWork.cs',
        'MCPForUnity/Editor/Services/Transport/LargeResultWriter.cs',
        'MCPForUnity/Editor/Services/Transport/PlainObjectJsonWriter.cs',
        'MCPForUnity/Editor/Services/Transport/StdioFrameBudget.cs',
        'MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs',
        'MCPForUnity/Editor/Services/Transport/TransportJson.cs',
        'MCPForUnity/Editor/Services/Transport/Transports/StdioBridgeAuthentication.cs'
    ) | ForEach-Object { Join-Path $Root $_ }
}
