namespace UnityMcp.ScriptDiagnostics;

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        try
        {
            if (args is ["--capabilities"])
            {
                await WireProtocol.WriteResultAsync(
                    Console.OpenStandardOutput(),
                    new AnalysisResult
                    {
                        Complete = true,
                        Providers = new()
                        {
                            ["host"] = "1",
                            ["runtime"] = Environment.Version.ToString(),
                            ["Microsoft.Unity.Analyzers"] = "1.28.0",
                            ["Microsoft.CodeAnalysis.CSharp.CodeStyle"] = "5.9.0",
                        },
                    }
                );
                return 0;
            }
            if (args.Length != 0)
                throw new InvalidDataException("unsupported_arguments");
            var request = await WireProtocol.ReadRequestAsync(Console.OpenStandardInput());
            var result = await AnalyzerRunner.AnalyzeAsync(request);
            await WireProtocol.WriteResultAsync(Console.OpenStandardOutput(), result);
            return 0;
        }
        catch (Exception error)
        {
            // No source content or stack trace is written to the protocol channel.
            await Console.Error.WriteLineAsync(error.GetType().Name + ": " + error.Message[..Math.Min(1024, error.Message.Length)]);
            return 2;
        }
    }
}
