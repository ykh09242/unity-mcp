using System;
using System.Collections;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.AssetGen.Http;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class UnityWebRequestTransportLifetimeTests
    {
        private sealed class CountingDownloadHandler : UnityEngine.Networking.DownloadHandlerScript
        {
            internal int Disposals;
            internal int DisposalThread;

            public override void Dispose()
            {
                Interlocked.Increment(ref Disposals);
                DisposalThread = Thread.CurrentThread.ManagedThreadId;
                base.Dispose();
            }
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator WorkerCancellationReleasesNativeOwnershipOnceOnEditorThread()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            int editorThread = Thread.CurrentThread.ManagedThreadId;
            using var cancellation = new CancellationTokenSource();
            var download = new CountingDownloadHandler();
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var native = new UnityEngine.Networking.UnityWebRequest($"http://127.0.0.1:{port}/held", "GET") { downloadHandler = download };
            using var owner = new EditorWebRequest(native);
            TcpClient client = null;
            int interruptions = 0;
            int completions = 0;
            Exception interruption = null;
            try
            {
                var accepted = listener.AcceptTcpClientAsync();
                owner.Send(
                    cancellation.Token,
                    () => completions++,
                    error =>
                    {
                        interruption = error;
                        interruptions++;
                    }
                );
                double deadline = UnityEditor.EditorApplication.timeSinceStartup + 8;
                while (!accepted.IsCompleted && UnityEditor.EditorApplication.timeSinceStartup < deadline)
                    yield return null;
                Assert.That(accepted.IsCompleted, Is.True);
                client = accepted.GetAwaiter().GetResult();
                var canceled = Task.Run(() =>
                {
                    cancellation.Cancel();
                    return Thread.CurrentThread.ManagedThreadId;
                });
                while ((!canceled.IsCompleted || !owner.IsDisposed) && UnityEditor.EditorApplication.timeSinceStartup < deadline)
                    yield return null;
                Assert.That(canceled.IsCompleted, Is.True);
                Assert.That(canceled.GetAwaiter().GetResult(), Is.Not.EqualTo(editorThread));
                Assert.That(owner.IsDisposed, Is.True);
                owner.Dispose();
                cancellation.Cancel();
                yield return null;
                Assert.That(download.Disposals, Is.EqualTo(1));
                Assert.That(download.DisposalThread, Is.EqualTo(editorThread));
                Assert.That(interruptions, Is.EqualTo(1));
                Assert.That(completions, Is.Zero);
                Assert.That(((OperationCanceledException)interruption).CancellationToken, Is.EqualTo(cancellation.Token));
            }
            finally
            {
                cancellation.Cancel();
                client?.Dispose();
                listener.Stop();
            }
        }

        [Test]
        public void CancellationDuringRegistrationReleasesUnsentRequestOnce()
        {
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            var download = new CountingDownloadHandler();
            var native = new UnityEngine.Networking.UnityWebRequest("http://127.0.0.1:1/never-sent", "GET") { downloadHandler = download };
            using var owner = new EditorWebRequest(native);
            int interruptions = 0;
            owner.Send(cancellation.Token, () => Assert.Fail("Canceled request must not complete."), _ => interruptions++);
            owner.Dispose();
            Assert.That(download.Disposals, Is.EqualTo(1));
            Assert.That(interruptions, Is.EqualTo(1));
            Assert.Throws<OperationCanceledException>(() => _ = owner.Request);
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator ActiveCancellationPreservesCallerToken()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            using var cancellation = new CancellationTokenSource();
            TcpClient client = null;
            try
            {
                var accepted = listener.AcceptTcpClientAsync();
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                var pending = new UnityWebRequestTransport().SendAsync(new HttpRequestSpec { Url = $"http://127.0.0.1:{port}/held" }, cancellation.Token);
                double deadline = UnityEditor.EditorApplication.timeSinceStartup + 8;
                while (!accepted.IsCompleted && !pending.IsCompleted && UnityEditor.EditorApplication.timeSinceStartup < deadline)
                    yield return null;
                Assert.That(accepted.IsCompleted, Is.True, "The test must establish a real local request.");
                client = accepted.GetAwaiter().GetResult();
                var cancel = Task.Run(() => cancellation.Cancel());
                while ((!cancel.IsCompleted || !pending.IsCompleted) && UnityEditor.EditorApplication.timeSinceStartup < deadline)
                    yield return null;
                Assert.That(cancel.IsCompleted, Is.True, "Worker cancellation must not deadlock with main-thread cleanup.");
                Assert.That(pending.IsCanceled, Is.True);
                var error = Assert.Throws<TaskCanceledException>(() => pending.GetAwaiter().GetResult());
                Assert.That(error.CancellationToken, Is.EqualTo(cancellation.Token));
            }
            finally
            {
                cancellation.Cancel();
                client?.Dispose();
                listener.Stop();
            }
        }

        [UnityEngine.TestTools.UnityTest]
        public System.Collections.IEnumerator CompletedNativeDownloadKeepsTheTransferredBytesAfterCleanup()
        {
            string path = System.IO.Path.GetFullPath(
                System.IO.Path.Combine(UnityEngine.Application.dataPath, "../Temp/McpResponse_" + Guid.NewGuid().ToString("N") + ".txt")
            );
            byte[] expected = System.Text.Encoding.UTF8.GetBytes("native response 한글");
            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path));
            System.IO.File.WriteAllBytes(path, expected);
            using var cancellation = new CancellationTokenSource();
            try
            {
                var task = new UnityWebRequestTransport().SendAsync(new HttpRequestSpec { Url = new Uri(path).AbsoluteUri }, cancellation.Token);
                double deadline = UnityEditor.EditorApplication.timeSinceStartup + 10;
                while (!task.IsCompleted && UnityEditor.EditorApplication.timeSinceStartup < deadline)
                    yield return null;
                Assert.IsTrue(task.IsCompleted, "Owned local download did not complete.");
                HttpResult result = task.GetAwaiter().GetResult();
                cancellation.Cancel();
                yield return null;
                Assert.That(task.Status, Is.EqualTo(TaskStatus.RanToCompletion), "Late cancellation cannot replace a completed response.");
                Assert.IsTrue(result.IsSuccess);
                Assert.AreEqual("native response 한글", result.Text);
                CollectionAssert.AreEqual(expected, result.Body);
            }
            finally
            {
                cancellation.Cancel();
                System.IO.File.Delete(path);
            }
        }

        [Test]
        public void AlreadyCanceledRequest_ReturnsCanceledTaskWithoutNativeSetup()
        {
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            // Invalid URL/header values would fail request setup if the native path were touched.
            var spec = new HttpRequestSpec
            {
                Url = null,
                Headers = new Dictionary<string, string> { [""] = "" },
            };
            Task<HttpResult> task = null;
            Assert.DoesNotThrow(() => task = new UnityWebRequestTransport().SendAsync(spec, cancellation.Token));
            Assert.That(task.IsCanceled, Is.True);
            var error = Assert.Throws<TaskCanceledException>(() => task.GetAwaiter().GetResult());
            Assert.That(error.CancellationToken, Is.EqualTo(cancellation.Token));
        }

        [Test]
        public void NullRequest_RemainsArgumentErrorEvenWhenCanceled()
        {
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            Assert.Throws<ArgumentNullException>(() => new UnityWebRequestTransport().SendAsync(null, cancellation.Token));
        }
    }
}
