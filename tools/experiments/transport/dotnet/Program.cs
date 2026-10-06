using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using ModelContextProtocol.Protocol;
using ModelContextProtocol.Server;
using System.Text.Json;

if (args.Length == 2 && args[0] == "raw")
{
    await RawBench.Run(args[1]);
    return;
}
if (args.Length != 2 || args[0] != "mcp")
    throw new ArgumentException("Usage: TransportProbe raw FIXTURE_DIR | mcp FIXTURE_DIR");
Console.Error.WriteLine($"owned_process_pid={Environment.ProcessId}");

var fixtures = args[1];
var options = new JsonSerializerOptions { PropertyNameCaseInsensitive = true };
var tools = JsonSerializer.Deserialize<Tool[]>(File.ReadAllText(Path.Combine(fixtures, "tools.json")), options)
    ?? throw new InvalidDataException("Missing tool descriptors");
var outputs = new[] { "small", "state", "large", "job" }.ToDictionary(
    name => name, name => JsonDocument.Parse(File.ReadAllBytes(Path.Combine(fixtures, name + ".json"))).RootElement.Clone());
var builder = Host.CreateApplicationBuilder([]);
builder.Logging.ClearProviders();
builder.Services.AddMcpServer().WithStdioServerTransport()
    .WithListToolsHandler((_, _) => ValueTask.FromResult(new ListToolsResult { Tools = tools }))
    .WithCallToolHandler((request, _) =>
    {
        var parameters = request.Params ?? throw new InvalidDataException("Missing tool parameters");
        string workload = parameters.Name switch
        {
            "read_console" => parameters.Arguments?["filter_text"].GetString()?.Split(':')[0]
                ?? throw new InvalidDataException("Missing fixture selector"),
            "get_test_job" => "job",
            _ => throw new InvalidDataException("Unknown experiment tool")
        };
        var output = outputs[workload];
        return ValueTask.FromResult(new CallToolResult
        {
            Content = [new TextContentBlock { Text = output.GetRawText() }],
            StructuredContent = output
        });
    });
await builder.Build().RunAsync();
