using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen.Http;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class UnityWebRequestTransportLifetimeTests
    {
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
