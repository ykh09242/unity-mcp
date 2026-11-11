using MCPForUnity.Editor.Services.ScriptQuality;
using NUnit.Framework;

namespace ScriptDiagnosticsHost.Tests;

public class InstallationTests
{
    [Test]
    public async Task GitPackageShapeInstallsAndInterruptedReplacementPreservesWorkingHost()
    {
        string repository = Path.GetFullPath(Path.Combine(TestContext.CurrentContext.TestDirectory, "../../../../../../.."));
        string source = Path.Combine(repository, "MCPForUnity/Editor/ScriptDiagnosticsHost~");
        Assert.That(Directory.Exists(source), Is.True, source);
        string root = Path.Combine(
            repository,
            ".superpowers/sdd/2026-11-11-source-freshness-and-script-diagnostics",
            "host-install-" + Guid.NewGuid().ToString("N")
        );
        string package = Path.Combine(root, "Packages/com.coplaydev.unity-mcp/Editor/ScriptDiagnosticsHost~");
        Directory.CreateDirectory(package);
        foreach (string name in AnalysisHostInstallation.SourceFiles)
            File.Copy(Path.Combine(source, name), Path.Combine(package, name));
        string cache = Path.Combine(root, "Library/MCPForUnity/ScriptDiagnosticsHost");
        using var owner = new AnalysisHostProcess();
        await AnalysisHostInstallation.InstallAsync(package, cache, owner, CancellationToken.None);
        string host = AnalysisHostInstallation.ResolveVerifiedHost(cache);
        Assert.That(File.Exists(host), Is.True);
        byte[] pointer = File.ReadAllBytes(Path.Combine(cache, "current.json"));
        File.WriteAllText(Path.Combine(cache, "unowned.txt"), "preserve");
        using var canceled = new CancellationTokenSource();
        var replacement = AnalysisHostInstallation.InstallAsync(package, cache, owner, canceled.Token);
        Assert.That(owner.ActiveProcessId, Is.GreaterThan(0));
        canceled.Cancel();
        Assert.CatchAsync<OperationCanceledException>(async () => await replacement);
        Assert.That(File.ReadAllBytes(Path.Combine(cache, "current.json")), Is.EqualTo(pointer));
        Assert.That(File.ReadAllText(Path.Combine(cache, "unowned.txt")), Is.EqualTo("preserve"));
        Assert.That(AnalysisHostInstallation.ResolveVerifiedHost(cache), Is.EqualTo(host));
        // Ordinary execution uses the published runtime files without a restore/build.
        var result = await owner.RunAsync("dotnet", [host, "--capabilities"], Path.GetDirectoryName(host)!, null!, true, CancellationToken.None);
        Assert.That(System.Text.Encoding.UTF8.GetString(result), Does.Contain("\"protocol\":1"));
    }
}
