using System;
using System.Diagnostics;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    public class PackageSourceSecurityTests
    {
        [TestCase("https://token@host/repo.git")]
        [TestCase("https://user:secret@host/repo.git")]
        [TestCase("ssh://user:secret@host/repo.git")]
        [TestCase("https://host/repo.git?token=secret")]
        [TestCase("https://host/repo.git?signature=secret")]
        [TestCase("user:secret@host:repo.git")]
        public void CredentialSourcesRejectBeforeNativePackageWork(string source)
        {
            // Given a credential-bearing source and an otherwise valid mutation.
            var request = new JObject { ["action"] = "add_package", ["package"] = source };
            // When native input admission runs, then no Client job is created.
            var response = JObject.FromObject(ManagePackages.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("must not contain credentials", response.Value<string>("error"));
            StringAssert.DoesNotContain(source, response.ToString());
        }

        [TestCase("ssh://git@host/repo.git")]
        [TestCase("git@host:repo.git#main")]
        [TestCase("https://host/repo.git?path=/Packages/tool#main")]
        public void CredentialFreeGitSourcesRetainSupportedSelectors(string source)
        {
            // Given legitimate SSH authentication and Git subdirectory/revision syntax.
            // When source admission parses it, then no credential rejection applies.
            Assert.IsFalse(PackageSourceSecurity.HasCredentials(source));
            Assert.AreEqual(source, PackageSourceSecurity.ForStorage(source));
        }

        [TestCase("https://user:secret@host/repo.git?path=secret#secret")]
        [TestCase("ssh://user:secret@host/repo.git?token=secret#secret")]
        [TestCase("git@host:repo.git?token=secret#secret")]
        public void LegacyDiagnosticsRedactCredentialsAndRetainUsefulHostPath(string source)
        {
            // Given arbitrary legacy/native error text, including nested response fields.
            var input = new { message = "Resolution failed: " + source, data = new { url = source } };
            // When projected for a read response.
            var response = PackageSourceSecurity.RedactResponse(input);
            // Then host/path and failure context remain useful, while all credential fields disappear.
            StringAssert.DoesNotContain("secret", response.ToString());
            StringAssert.Contains("host", response.ToString());
            StringAssert.Contains("repo.git", response.ToString());
            StringAssert.Contains("Resolution failed", response.ToString());
        }

        [Test]
        public void LargePlainDiagnosticsPreserveTextWithoutUnboundedPatternSearch()
        {
            string plain = new string('x', 140 * 1024);
            var timer = Stopwatch.StartNew();
            Assert.AreEqual(plain, PackageSourceSecurity.Redact(plain));
            Assert.IsFalse(PackageSourceSecurity.HasCredentials(plain));
            Assert.Less(timer.ElapsedMilliseconds, 5000, "Plain fields must not trigger quadratic URL/SCP matching.");
        }

        [TestCase(" https://user:secret@host/repo.git?token=secret")]
        [TestCase(" user:secret@host:repo.git#secret")]
        [TestCase(":// https://user:secret@host/repo.git")]
        [TestCase("@host user:secret@host:repo.git")]
        public void LongMalformedPrefixesNeverExposeCredentialSuffixes(string suffix)
        {
            string source = new string('x', 140 * 1024) + suffix;
            var timer = Stopwatch.StartNew();
            string redacted = PackageSourceSecurity.Redact(source);
            StringAssert.DoesNotContain("secret", redacted);
            Assert.Less(timer.ElapsedMilliseconds, 5000, "Malformed URL/SCP candidates need a finite redaction budget.");
        }

        [Test]
        public void LongAmbiguousScpCredentialAdmissionIsBoundedAndFailsClosed()
        {
            string source = new string(':', 140 * 1024) + "user:secret@host:repo.git";
            var timer = Stopwatch.StartNew();
            Assert.IsTrue(PackageSourceSecurity.HasCredentials(source));
            StringAssert.DoesNotContain("secret", PackageSourceSecurity.ForStorage(source));
            Assert.Less(timer.ElapsedMilliseconds, 5000);
        }

        [TestCase("Url")]
        [TestCase("ScpSource")]
        [TestCase("ScpCredentials")]
        [TestCase("GitPathQuery")]
        public void EveryPackagePatternHasAFiniteMatchBudget(string field)
        {
            var regex = (Regex)typeof(PackageSourceSecurity).GetField(field, BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Assert.Greater(regex.MatchTimeout, TimeSpan.Zero);
            Assert.LessOrEqual(regex.MatchTimeout, TimeSpan.FromMilliseconds(100));
        }

        [Test]
        public void JobSerializationRedactsLegacyPackageAndNativeError()
        {
            // Given a legacy job that predates input credential rejection.
            var job = new PackageJob { Package = "https://token@host/repo.git?token=secret", Error = "Failed: https://user:secret@host/repo.git#secret" };
            // When a read-scoped caller obtains its serializable status.
            string response = JObject.FromObject(PackageJobManager.ToSerializable(job)).ToString();
            // Then neither source nor error leaks credentials.
            StringAssert.DoesNotContain("secret", response);
            StringAssert.DoesNotContain("token@", response);
            StringAssert.Contains("host/repo.git", response);
        }
    }
}
