using System;
using System.Collections;
using System.IO;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.ScriptQuality;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.PackageManager;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class ScriptAnalysisHostTests
    {
        [Test]
        public void InspectionOfMissingHostDoesNotCreateDirectories()
        {
            string root = Path.Combine(Application.dataPath, "..", "Library", "MCPHostMissing-" + Guid.NewGuid().ToString("N"));
            Assert.That(AnalysisHostInstallation.Inspect(root, out string reason), Is.Null);
            Assert.That(reason, Is.EqualTo("analyzer_unavailable"));
            Assert.That(Directory.Exists(root), Is.False);
        }

        [Test]
        public void OversizedAndTruncatedFramesCannotBecomeDiagnostics()
        {
            using var oversized = new MemoryStream(new byte[] { 0, 0, 0, 127 });
            Assert.ThrowsAsync<InvalidDataException>(async () => await AnalysisHostProcess.ReadFrameAsync(oversized, CancellationToken.None));
            using var truncated = new MemoryStream(new byte[] { 100, 0, 0, 0, 123 });
            Assert.ThrowsAsync<EndOfStreamException>(async () => await AnalysisHostProcess.ReadFrameAsync(truncated, CancellationToken.None));
        }

        [UnityTest]
        public IEnumerator NativeSetupAndProtocolWorkFromTheInstalledPackage()
        {
            string package = PackageInfo.FindForAssembly(typeof(AnalysisHostProcess).Assembly).resolvedPath;
            string source = Path.Combine(package, "Editor", "ScriptDiagnosticsHost~");
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Library", "MCPHostFixture-" + Guid.NewGuid().ToString("N")));
            using var owner = new AnalysisHostProcess();
            var installation = AnalysisHostInstallation.InstallAsync(source, root, owner, CancellationToken.None);
            while (!installation.IsCompleted)
                yield return null;
            Assert.That(installation.IsFaulted, Is.False, installation.Exception?.ToString());
            string host = AnalysisHostInstallation.ResolveVerifiedHost(root);
            byte[] pointer = File.ReadAllBytes(Path.Combine(root, "current.json"));
            File.WriteAllText(Path.Combine(root, "unowned.txt"), "preserve");
            using var canceled = new CancellationTokenSource();
            canceled.Cancel();
            var interrupted = AnalysisHostInstallation.InstallAsync(source, root, owner, canceled.Token);
            while (!interrupted.IsCompleted)
                yield return null;
            Assert.That(interrupted.IsCanceled, Is.True);
            Assert.That(File.ReadAllBytes(Path.Combine(root, "current.json")), Is.EqualTo(pointer));
            Assert.That(File.ReadAllText(Path.Combine(root, "unowned.txt")), Is.EqualTo("preserve"));
            var execution = owner.RunAsync("dotnet", new[] { host, "--capabilities" }, Path.GetDirectoryName(host), null, true, CancellationToken.None);
            while (!execution.IsCompleted)
                yield return null;
            Assert.That(execution.IsFaulted, Is.False, execution.Exception?.ToString());
            Assert.That((int?)JObject.Parse(Encoding.UTF8.GetString(execution.Result))["protocol"], Is.EqualTo(1));
            Assert.That(owner.Occupied, Is.Zero);
        }
    }
}
