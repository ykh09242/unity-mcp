using System;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen.Http;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class UnityWebRequestTransportTests
    {
        [TestCase("http://127.0.0.1/private")]
        [TestCase("https://assets.meshy.ai:8443/model.glb")]
        [TestCase("https://attacker.example/model.glb")]
        public void ArtifactRequest_UsesRestrictedDownloader(string url)
        {
            var spec = new HttpRequestSpec { Method = "GET", Url = url, DownloadProvider = "meshy" };
            // This fails before UnityWebRequest is constructed or any network request is sent.
            Assert.ThrowsAsync<InvalidOperationException>(() => new UnityWebRequestTransport().SendAsync(spec, CancellationToken.None));
        }
    }
}
