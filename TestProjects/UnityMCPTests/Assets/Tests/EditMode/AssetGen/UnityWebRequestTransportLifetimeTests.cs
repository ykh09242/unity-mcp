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
