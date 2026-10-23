using System;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ShaderReadPagingTests
    {
        private string directory;
        private string full;
        private string file;

        [SetUp]
        public void SetUp()
        {
            directory = "Assets/__McpShaderPaging_" + Guid.NewGuid().ToString("N");
            full = Path.Combine(Application.dataPath, directory.Substring(7));
            Directory.CreateDirectory(full);
            file = Path.Combine(full, "Fixture.shader");
        }

        [TearDown]
        public void TearDown()
        {
            Assert.That(Path.GetFullPath(full), Does.StartWith(Path.GetFullPath(Application.dataPath) + Path.DirectorySeparatorChar));
            File.Delete(file);
            Directory.Delete(full);
        }

        [TestCase("utf8")]
        [TestCase("utf8-bom")]
        [TestCase("utf16le")]
        [TestCase("utf16be")]
        [TestCase("utf32le")]
        [TestCase("utf32be")]
        public void PagesPreserveUnicodeBoundariesAndSecondBom(string kind)
        {
            Encoding encoding =
                kind == "utf8" ? new UTF8Encoding(false)
                : kind == "utf8-bom" ? new UTF8Encoding(true)
                : kind.StartsWith("utf16") ? (Encoding)new UnicodeEncoding(kind.EndsWith("be"), true)
                : new UTF32Encoding(kind.EndsWith("be"), true);
            string expected = (encoding.GetPreamble().Length > 0 ? "\uFEFF" : "") + "// 한글 😀\r\n";
            byte[] payload = encoding.GetBytes(expected);
            File.WriteAllBytes(file, encoding.GetPreamble().Concat(payload).ToArray());
            foreach (int limit in new[] { 4, 5, 7, 31 })
            {
                var assembled = new StringBuilder();
                long offset = 0;
                for (int page = 0; page < 100; page++)
                {
                    JObject data = Read(offset, limit);
                    string contents = (string)data["contents"];
                    Assert.That(data["hasMore"], Is.Not.Null);
                    Assert.That(data["encodedContents"]?.Type, Is.EqualTo(JTokenType.Null));
                    Assert.That(encoding.GetByteCount(contents), Is.LessThanOrEqualTo(limit));
                    assembled.Append(contents);
                    if (!(bool)data["hasMore"])
                    {
                        Assert.That(data["nextOffset"]?.Type, Is.EqualTo(JTokenType.Null));
                        break;
                    }
                    long next = (long)data["nextOffset"];
                    Assert.That(next, Is.GreaterThan(offset));
                    offset = next;
                }
                Assert.That(assembled.ToString(), Is.EqualTo(expected));
            }
        }

        [Test]
        public void DefaultReadIsBoundedAndCanContinueLargeSource()
        {
            string expected = new string('x', 140000);
            File.WriteAllText(file, expected, new UTF8Encoding(false));
            JObject first = Read();
            Assert.That(((string)first["contents"]).Length, Is.EqualTo(65536));
            Assert.That((long)first["sourceSizeBytes"], Is.EqualTo(expected.Length));
            var second = Read((long)first["nextOffset"], 1048576);
            Assert.That((string)first["contents"] + (string)second["contents"], Is.EqualTo(expected));
            Assert.That((bool)second["hasMore"], Is.False);
        }

        [Test]
        public void ExplicitBase64HasNoDuplicatedText()
        {
            const string expected = "// 한글 😀";
            File.WriteAllText(file, expected, new UTF8Encoding(false));
            var request = Request();
            request["readEncoding"] = "base64";
            var response = JObject.FromObject(ManageShader.HandleCommand(request));
            Assert.That((bool)response["success"], Is.True, response.ToString());
            JObject data = (JObject)response["data"];
            Assert.That(data["contents"]?.Type, Is.EqualTo(JTokenType.Null));
            Assert.That(Encoding.UTF8.GetString(Convert.FromBase64String((string)data["encodedContents"])), Is.EqualTo(expected));
        }

        [TestCase("readLimit", "3")]
        [TestCase("readLimit", "1048577")]
        [TestCase("readOffset", "-1")]
        [TestCase("readOffset", "1.5")]
        [TestCase("readOffset", "true")]
        [TestCase("readOffset", "null")]
        [TestCase("readOffset", "9223372036854775808")]
        [TestCase("readEncoding", "\"invalid\"")]
        public void InvalidReadOptionsRefuseWithoutChangingSource(string key, string json)
        {
            byte[] original = Encoding.UTF8.GetBytes("// original");
            File.WriteAllBytes(file, original);
            var request = Request();
            request[key] = JToken.Parse(json);
            var result = JObject.FromObject(ManageShader.HandleCommand(request));
            Assert.That((bool)result["success"], Is.False);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(file));
        }

        [Test]
        public void MidCodepointAndPastEndOffsetsRefuseAndEofIsEmpty()
        {
            File.WriteAllText(file, "😀x", new UTF8Encoding(false));
            foreach (long offset in new long[] { 1, 2, 3, 6, long.MaxValue })
            {
                var request = Request();
                request["readOffset"] = offset;
                var result = JObject.FromObject(ManageShader.HandleCommand(request));
                Assert.That((bool)result["success"], Is.False, "offset=" + offset);
            }
            JObject eof = Read(5);
            Assert.That((string)eof["contents"], Is.Empty);
            Assert.That((bool)eof["hasMore"], Is.False);
        }

        [Test]
        public void MalformedSuffixIsRefusedWhenItsPageIsRead()
        {
            byte[] bytes = new byte[] { 120, 120, 120, 120, 0xe2, 0x82 };
            File.WriteAllBytes(file, bytes);
            JObject first = Read(0, 4);
            Assert.That((string)first["contents"], Is.EqualTo("xxxx"));
            var request = Request();
            request["readOffset"] = (long)first["nextOffset"];
            var failed = JObject.FromObject(ManageShader.HandleCommand(request));
            Assert.That((bool)failed["success"], Is.False);
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(file));
        }

        private JObject Request() =>
            new JObject
            {
                ["action"] = "read",
                ["name"] = "Fixture",
                ["path"] = directory,
                ["readEncoding"] = "text",
            };

        private JObject Read(long offset = 0, int? limit = null)
        {
            JObject request = Request();
            request["readOffset"] = offset;
            if (limit.HasValue)
                request["readLimit"] = limit.Value;
            var response = JObject.FromObject(ManageShader.HandleCommand(request));
            Assert.That((bool)response["success"], Is.True, response.ToString());
            return (JObject)response["data"];
        }
    }
}
