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
        public IEnumerator AbortReleasesNativeOwnershipBeforeFinallyDisposesAgain()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            TcpClient client = null;
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            using var transfer = new RoslynInstaller.UnityTransfer($"http://127.0.0.1:{port}/held");
            try
            {
                var accepted = listener.AcceptTcpClientAsync();
                transfer.Start();
                var elapsed = Stopwatch.StartNew();
                while (!accepted.IsCompleted && elapsed.Elapsed < TimeSpan.FromSeconds(5))
                    yield return null;
                Assert.That(accepted.IsCompleted, Is.True);
                client = accepted.GetAwaiter().GetResult();
                transfer.Abort();
                Assert.Throws<ObjectDisposedException>(
                    () => _ = transfer.DownloadedBytes,
                    "Abort must finish ownership cleanup before the caller's finally continues."
                );
                Assert.That(transfer.IsDone, Is.True);
                Assert.DoesNotThrow(() => transfer.Dispose());
            }
            finally
            {
                client?.Dispose();
                listener.Stop();
            }
        }

        [Test]
        public void DisposedTransferReportsDoneWithoutNativeAccess()
        {
            var transfer = new RoslynInstaller.UnityTransfer("http://127.0.0.1:1/never-sent");
            transfer.Dispose();
            Assert.DoesNotThrow(() => transfer.Dispose());
            Assert.That(transfer.IsDone, Is.True, "Finally cleanup must not query freed native state.");
        }

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
