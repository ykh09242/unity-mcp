using System;
using System.IO;
using System.Net;
using System.Net.Security;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen.Http;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetDownloadTests
    {
        private const string MeshyUrl = "https://assets.meshy.ai/model.glb?Signature=test%2Fvalue";
        private static readonly IPAddress PublicIp = IPAddress.Parse("93.184.215.14");

        [TestCase("tripo", "https://tripo-data.rg1.data.tripo3d.com/model.glb?Signature=test")]
        [TestCase("meshy", MeshyUrl)]
        [TestCase("fal", "https://fal.media/a.png")]
        [TestCase("fal", "https://v3.fal.media/a.wav")]
        [TestCase("sketchfab", "https://sketchfab-prod-media.s3.amazonaws.com/archive.zip?X-Amz-Signature=test")]
        public void DocumentedArtifactHosts_Accepted(string provider, string url)
        {
            Assert.That(AssetDownloadPolicy.RequireAllowedUrl(provider, url).AbsoluteUri, Is.EqualTo(url));
        }

        [TestCase("http://127.0.0.1/private")]
        [TestCase("https://127.1/private")]
        [TestCase("https://2130706433/private")]
        [TestCase("https://0x7f000001/private")]
        [TestCase("https://169.254.169.254/metadata")]
        [TestCase("https://[::1]/private")]
        [TestCase("https://[::ffff:127.0.0.1]/private")]
        [TestCase("https://10.0.0.1/private")]
        [TestCase("https://localhost/private")]
        [TestCase("http://assets.meshy.ai/model.glb")]
        [TestCase("https://assets.meshy.ai:8443/model.glb")]
        [TestCase("https://user:password@assets.meshy.ai/model.glb")]
        [TestCase("https://assets.meshy.ai@localhost/model.glb")]
        [TestCase("https://assets.meshy.ai.evil.example/model.glb")]
        [TestCase("https://evilassets.meshy.ai/model.glb")]
        [TestCase("https://assets.meshy.ai./model.glb")]
        [TestCase("https://assets.meshy.ai/model.glb#fragment")]
        [TestCase("file:///etc/passwd")]
        [TestCase("/model.glb")]
        public void UnsafeUrl_RejectedBeforeDnsOrConnection(string url)
        {
            int dnsCalls = 0;
            int sends = 0;
            var downloader = new AssetDownloadTransport(
                _ => { dnsCalls++; return Task.FromResult(new[] { PublicIp }); },
                (_, __, ___) => { sends++; return Task.FromResult(Ok()); });
            Assert.ThrowsAsync<InvalidOperationException>(() => downloader.DownloadAsync("meshy", url, CancellationToken.None));
            Assert.That(dnsCalls, Is.Zero);
            Assert.That(sends, Is.Zero);
        }

        [TestCase("fal", "https://evilfal.media/a.png")]
        [TestCase("fal", "https://fal.media.evil.example/a.png")]
        [TestCase("sketchfab", "https://attacker.s3.amazonaws.com/model.zip")]
        [TestCase("tripo", "https://api.tripo3d.ai/private")]
        [TestCase("meshy", "https://fal.media/a.png")]
        [TestCase("openrouter", "https://arbitrary.example/a.png")]
        [TestCase("unknown", MeshyUrl)]
        public void HostAllowlist_IsProviderSpecific(string provider, string url)
        {
            Assert.Throws<InvalidOperationException>(() => AssetDownloadPolicy.RequireAllowedUrl(provider, url));
        }

        [TestCase("0.0.0.0")]
        [TestCase("10.1.2.3")]
        [TestCase("100.64.0.1")]
        [TestCase("100.127.255.254")]
        [TestCase("127.0.0.1")]
        [TestCase("169.254.169.254")]
        [TestCase("172.16.0.1")]
        [TestCase("172.31.255.254")]
        [TestCase("192.168.1.1")]
        [TestCase("192.0.0.1")]
        [TestCase("192.0.2.1")]
        [TestCase("192.88.99.1")]
        [TestCase("198.18.0.1")]
        [TestCase("198.19.255.254")]
        [TestCase("198.51.100.1")]
        [TestCase("203.0.113.1")]
        [TestCase("224.0.0.1")]
        [TestCase("240.0.0.1")]
        [TestCase("255.255.255.255")]
        [TestCase("168.63.129.16")]
        [TestCase("::")]
        [TestCase("::1")]
        [TestCase("::ffff:127.0.0.1")]
        [TestCase("::ffff:10.0.0.1")]
        [TestCase("::192.168.0.1")]
        [TestCase("fe80::1")]
        [TestCase("fec0::1")]
        [TestCase("fc00::1")]
        [TestCase("fd00::1")]
        [TestCase("ff02::1")]
        [TestCase("64:ff9b::a00:1")]
        [TestCase("64:ff9b:1::1")]
        [TestCase("100::1")]
        [TestCase("2001::1")]
        [TestCase("2001:2::1")]
        [TestCase("2001:db8::1")]
        [TestCase("2002:7f00:1::1")]
        [TestCase("3ffe::1")]
        [TestCase("3fff::1")]
        [TestCase("2606:4700::1%3")]
        public void NonPublicDnsAnswer_BlocksEntireDownload(string address)
        {
            int sends = 0;
            var downloader = new AssetDownloadTransport(
                _ => Task.FromResult(new[] { PublicIp, IPAddress.Parse(address) }),
                (_, __, ___) => { sends++; return Task.FromResult(Ok()); });
            Assert.ThrowsAsync<InvalidOperationException>(() => downloader.DownloadAsync("meshy", MeshyUrl, CancellationToken.None));
            Assert.That(sends, Is.Zero, "A mixed answer must fail before any connection.");
        }

        [TestCase("1.1.1.1")]
        [TestCase("8.8.8.8")]
        [TestCase("93.184.215.14")]
        [TestCase("::ffff:8.8.8.8")]
        [TestCase("2606:4700:4700::1111")]
        [TestCase("2001:4860:4860::8888")]
        public void PublicAddress_Accepted(string address)
        {
            Assert.IsTrue(AssetDownloadPolicy.IsPublicAddress(IPAddress.Parse(address)));
        }

        [Test]
        public void EmptyDnsAnswer_FailsClosed()
        {
            Assert.Throws<IOException>(() => AssetDownloadPolicy.RequirePublicAddresses(Array.Empty<IPAddress>()));
        }

        [Test]
        public void PinnedIpv6Request_PreservesTlsHostAndIsolatesConnections()
        {
            var address = IPAddress.Parse("2606:4700:4700::1111");
            var first = AssetDownloadTransport.CreatePinnedRequest(new Uri(MeshyUrl), address);
            var second = AssetDownloadTransport.CreatePinnedRequest(new Uri(MeshyUrl), address);
            Assert.That(IPAddress.Parse(first.RequestUri.DnsSafeHost), Is.EqualTo(address));
            Assert.That(first.Host, Is.EqualTo("assets.meshy.ai"));
            Assert.IsNotEmpty(first.ConnectionGroupName);
            Assert.That(first.ConnectionGroupName, Is.Not.EqualTo(second.ConnectionGroupName));
            first.Abort();
            second.Abort();
        }

        [Test]
        public async Task ConnectionRetry_UsesOnlyAlreadyValidatedAddresses()
        {
            var second = IPAddress.Parse("1.1.1.1");
            int lookups = 0;
            int sends = 0;
            var downloader = new AssetDownloadTransport(
                _ => { lookups++; return Task.FromResult(new[] { PublicIp, second }); },
                (_, address, __) =>
                {
                    if (++sends == 1) throw new WebException("connection refused", WebExceptionStatus.ConnectFailure);
                    Assert.That(address, Is.EqualTo(second));
                    return Task.FromResult(Ok());
                });
            Assert.IsTrue((await downloader.DownloadAsync("meshy", MeshyUrl, CancellationToken.None)).IsSuccess);
            Assert.That(lookups, Is.EqualTo(1));
            Assert.That(sends, Is.EqualTo(2));
        }

        [TestCase("http://127.0.0.1/private")]
        [TestCase("https://169.254.169.254/metadata")]
        [TestCase("https://[::1]/private")]
        [TestCase("https://attacker.example/file")]
        [TestCase("https://assets.meshy.ai:8443/file")]
        [TestCase("https://user@assets.meshy.ai/file")]
        public void PublicRedirectToUnsafeUrl_NeverContactsTarget(string location)
        {
            int sends = 0;
            int resolutions = 0;
            var downloader = new AssetDownloadTransport(
                _ => { resolutions++; return Task.FromResult(new[] { PublicIp }); },
                (_, __, ___) => { sends++; return Task.FromResult(Redirect(location)); });
            Assert.ThrowsAsync<InvalidOperationException>(() => downloader.DownloadAsync("meshy", MeshyUrl, CancellationToken.None));
            Assert.That(sends, Is.EqualTo(1));
            Assert.That(resolutions, Is.EqualTo(1));
        }

        [Test]
        public void SameHostRedirect_RevalidatesDnsAndRejectsRebinding()
        {
            int lookups = 0;
            int sends = 0;
            var downloader = new AssetDownloadTransport(
                _ => Task.FromResult(new[] { ++lookups == 1 ? PublicIp : IPAddress.Loopback }),
                (_, address, __) => { sends++; Assert.That(address, Is.EqualTo(PublicIp)); return Task.FromResult(Redirect("/next")); });
            Assert.ThrowsAsync<InvalidOperationException>(() => downloader.DownloadAsync("meshy", MeshyUrl, CancellationToken.None));
            Assert.That(lookups, Is.EqualTo(2));
            Assert.That(sends, Is.EqualTo(1));
        }

        [Test]
        public async Task SignedUrl_UsesValidatedSnapshotAndPreservesHostAndQuery()
        {
            int lookups = 0;
            var downloader = new AssetDownloadTransport(
                _ => Task.FromResult(new[] { ++lookups == 1 ? PublicIp : IPAddress.Loopback }),
                (uri, address, _) =>
                {
                    var request = AssetDownloadTransport.CreatePinnedRequest(uri, address);
                    Assert.That(request.RequestUri.Host, Is.EqualTo(PublicIp.ToString()));
                    Assert.That(request.ServicePoint.Address.Host, Is.EqualTo(PublicIp.ToString()));
                    Assert.That(request.RequestUri.PathAndQuery, Is.EqualTo(new Uri(MeshyUrl).PathAndQuery));
                    Assert.That(request.Host, Is.EqualTo("assets.meshy.ai"));
                    Assert.IsFalse(request.AllowAutoRedirect);
                    Assert.IsNull(request.Proxy);
                    Assert.IsNull(request.Credentials);
                    Assert.IsNull(request.CookieContainer);
                    Assert.IsNull(request.Headers["Authorization"]);
                    Assert.IsFalse(request.KeepAlive);
                    Assert.IsFalse(request.ServerCertificateValidationCallback(null, null, null, SslPolicyErrors.RemoteCertificateNameMismatch));
                    Assert.IsFalse(request.ServerCertificateValidationCallback(null, null, null, SslPolicyErrors.RemoteCertificateChainErrors));
                    request.Abort();
                    return Task.FromResult(Ok());
                });
            HttpResult result = await downloader.DownloadAsync("meshy", MeshyUrl, CancellationToken.None);
            Assert.IsTrue(result.IsSuccess);
            CollectionAssert.AreEqual(new byte[] { 1, 2, 3 }, result.Body);
            Assert.That(lookups, Is.EqualTo(1));
        }

        [TestCase(301)]
        [TestCase(302)]
        [TestCase(303)]
        [TestCase(307)]
        [TestCase(308)]
        public async Task ValidRelativeRedirect_IsFollowedWithFreshValidation(int status)
        {
            int sends = 0;
            int lookups = 0;
            var downloader = new AssetDownloadTransport(
                _ => { lookups++; return Task.FromResult(new[] { PublicIp }); },
                (uri, _, __) =>
                {
                    if (++sends == 1) return Task.FromResult(new HttpResult { Status = status, RedirectLocation = "/final?Signature=next%2Fvalue" });
                    Assert.That(uri.AbsoluteUri, Is.EqualTo("https://assets.meshy.ai/final?Signature=next%2Fvalue"));
                    return Task.FromResult(Ok());
                });
            Assert.IsTrue((await downloader.DownloadAsync("meshy", MeshyUrl, CancellationToken.None)).IsSuccess);
            Assert.That(lookups, Is.EqualTo(2));
        }

        [Test]
        public void RedirectLoop_IsBounded()
        {
            int sends = 0;
            var downloader = new AssetDownloadTransport(
                _ => Task.FromResult(new[] { PublicIp }),
                (_, __, ___) => { sends++; return Task.FromResult(Redirect("/loop")); });
            Assert.ThrowsAsync<InvalidOperationException>(() => downloader.DownloadAsync("meshy", MeshyUrl, CancellationToken.None));
            Assert.That(sends, Is.EqualTo(AssetDownloadTransport.MaxRedirects + 1));
        }

        [Test]
        public void CanceledDownload_DoesNotResolveOrConnect()
        {
            using var cts = new CancellationTokenSource();
            cts.Cancel();
            var downloader = new AssetDownloadTransport(_ => throw new AssertionException("Must not resolve"));
            Assert.ThrowsAsync(Is.InstanceOf<OperationCanceledException>(), () => downloader.DownloadAsync("meshy", MeshyUrl, cts.Token));
        }

        [Test]
        public void CancellationWhileResolving_CompletesWithoutConnecting()
        {
            using var cts = new CancellationTokenSource();
            var pending = new TaskCompletionSource<IPAddress[]>();
            var downloader = new AssetDownloadTransport(_ => pending.Task);
            Task<HttpResult> download = downloader.DownloadAsync("meshy", MeshyUrl, cts.Token);
            cts.Cancel();
            Assert.ThrowsAsync(Is.InstanceOf<OperationCanceledException>(), async () => await download);
        }

        [Test]
        public void RejectedUrl_DoesNotLeakSignedQuery()
        {
            var error = Assert.Throws<InvalidOperationException>(() => AssetDownloadPolicy.RequireAllowedUrl("meshy", "https://evil.example/a?Signature=secret-value"));
            StringAssert.DoesNotContain("secret-value", error.Message);
        }

        private static HttpResult Ok() => new HttpResult { Status = 200, IsSuccess = true, Body = new byte[] { 1, 2, 3 } };
        private static HttpResult Redirect(string location) => new HttpResult { Status = 302, RedirectLocation = location };
    }
}
