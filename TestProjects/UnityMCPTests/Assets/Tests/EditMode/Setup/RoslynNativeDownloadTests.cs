using System;
using System.Collections;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Setup
{
    public class RoslynNativeDownloadTests
    {
        [UnityTest]
        public IEnumerator CancellationAbortsARealStalledUnityWebRequest()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            using var cancellation = new CancellationTokenSource();
            try
            {
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                var accepted = listener.AcceptTcpClientAsync();
                var transfer = new RoslynInstaller.UnityTransfer($"http://127.0.0.1:{port}/stalled-fixture");
                var pending = RoslynDownload.FetchAsync(() => transfer, cancellation.Token);
                var elapsed = Stopwatch.StartNew();
                while (!accepted.IsCompleted && !pending.IsCompleted && elapsed.Elapsed < TimeSpan.FromSeconds(5))
                    yield return null;
                cancellation.Cancel();
                while (!pending.IsCompleted && elapsed.Elapsed < TimeSpan.FromSeconds(8))
                    yield return null;
                using var client = accepted.IsCompleted && !accepted.IsFaulted && !accepted.IsCanceled ? accepted.GetAwaiter().GetResult() : null;
                Assert.That(pending.IsCompleted, Is.True, "Cancellation must not wait for the stalled HTTP server.");
                Assert.That(pending.IsCanceled, Is.True, "The real UnityWebRequest must terminate as cancellation.");
                Assert.That(accepted.IsCompleted, Is.True, "The fixture must exercise an established local connection.");
            }
            finally
            {
                cancellation.Cancel();
                listener.Stop();
            }
        }
    }
}
