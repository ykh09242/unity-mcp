using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ScriptCreationPublicationTests
    {
        [Test]
        public void ImportFailure_AfterCreateReportsSavedSourceAndDoesNotReplayCreation()
        {
            string folder = "Assets/ScriptPublication_" + Guid.NewGuid().ToString("N");
            string path = folder + "/Published.cs";
            const string contents = "public class Published {}\n";
            var boundary = typeof(ManageScript).GetField("_createScriptImportForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(boundary, "The import boundary must be injectable without replacing file publication.");
            int imports = 0;
            try
            {
                boundary.SetValue(
                    null,
                    (Action<string>)(
                        importPath =>
                        {
                            imports++;
                            Assert.AreEqual(path, importPath);
                            Assert.AreEqual(contents, File.ReadAllText(path), "The real atomic creation must precede import.");
                            throw new IOException(new string('x', 2000));
                        }
                    )
                );
                var response = Create(path, contents);
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.IsTrue(response["data"]?.Value<bool>("source_saved") == true, response.ToString());
                Assert.AreEqual(path, response["data"].Value<string>("path"));
                Assert.AreEqual("mcpforunity://path/" + path, response["data"].Value<string>("uri"));
                Assert.AreEqual("import_and_compile", response["data"].Value<string>("failed_stage"));
                Assert.AreEqual("unknown", response["data"].Value<string>("refresh_confirmation"));
                Assert.LessOrEqual(response["data"].Value<string>("refresh_error").Length, 512);
                Assert.LessOrEqual(response.ToString().Length, 2048);
                StringAssert.Contains("Inspect", response["data"].Value<string>("recovery"));
                StringAssert.Contains("Do not replay", response["data"].Value<string>("recovery"));
                Assert.AreEqual(contents, File.ReadAllText(path));
                var repeated = Create(path, "public class Published { public int Changed; }\n");
                Assert.IsFalse(repeated.Value<bool>("success"));
                Assert.IsNull(repeated["data"]?["source_saved"], "An already-existing target does not confirm publication by this new request.");
                Assert.AreEqual(1, imports, "A refused duplicate must not reach import or rewrite source.");
                Assert.AreEqual(contents, File.ReadAllText(path));
            }
            finally
            {
                boundary.SetValue(null, null);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        [Test]
        public void InvalidContents_BeforeCreateDoesNotClaimPublicationOrImport()
        {
            string folder = "Assets/ScriptPublication_" + Guid.NewGuid().ToString("N");
            string path = folder + "/Invalid.cs";
            var response = Create(path, "public class Invalid {");
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(response["data"]?["source_saved"]);
            Assert.IsFalse(File.Exists(path));
            Assert.IsFalse(AssetDatabase.IsValidFolder(folder));
        }

        private static JObject Create(string path, string contents)
        {
            var method = typeof(ManageScript).GetMethod("CreateScript", BindingFlags.Static | BindingFlags.NonPublic);
            return JObject.FromObject(
                method.Invoke(null, new object[] { Path.GetFullPath(path), path, Path.GetFileNameWithoutExtension(path), contents, null, null })
            );
        }

        [TestCase(true)]
        [TestCase(false)]
        public void TextEditRefreshFailure_RetainsPublishedBytesAndAllowsRefreshOnlyRecovery(bool immediate)
        {
            string folder = "Assets/TextEditPublication_" + Guid.NewGuid().ToString("N");
            string path = folder + "/Published.cs";
            const string original = "public class Published {}\n";
            const string candidate = "// Published edit\npublic class Published {}\n";
            const BindingFlags hidden = BindingFlags.Static | BindingFlags.NonPublic;
            var refresh = typeof(ManageScript).GetField("_textEditRefreshForTests", hidden);
            var write = typeof(ManageScript).GetField("_scriptWriteBeforeCommitForTests", hidden);
            Assert.IsNotNull(refresh, "The post-publication refresh must be injectable without replacing file IO.");
            object priorRefresh = refresh.GetValue(null),
                priorWrite = write.GetValue(null);
            var scheduler = typeof(ManageScript).Assembly.GetType("MCPForUnity.Editor.Tools.RefreshDebounce", true);
            var paths = (HashSet<string>)scheduler.GetField("_paths", hidden).GetValue(null);
            var priorPaths = new List<string>(paths);
            var schedulerFields = new[] { "_compilePending", "_scheduled", "_lastRequest" };
            var priorScheduler = new Dictionary<FieldInfo, object>();
            foreach (string name in schedulerFields)
            {
                var field = scheduler.GetField(name, hidden);
                priorScheduler.Add(field, field.GetValue(null));
            }
            var callbacks = EditorApplication.delayCall;
            int writes = 0,
                refreshes = 0;
            try
            {
                Directory.CreateDirectory(folder);
                File.WriteAllText(path, original, new UTF8Encoding(false));
                write.SetValue(null, (Action<string>)(_ => writes++));
                refresh.SetValue(
                    null,
                    (Action<string, bool>)(
                        (refreshPath, requestedImmediate) =>
                        {
                            refreshes++;
                            Assert.AreEqual(path, refreshPath);
                            Assert.AreEqual(immediate, requestedImmediate);
                            CollectionAssert.AreEqual(Encoding.UTF8.GetBytes(candidate), File.ReadAllBytes(path));
                            throw new IOException(new string('x', 2000));
                        }
                    )
                );
                var method = typeof(ManageScript).GetMethod("ApplyTextEdits", hidden);
                var edits = new JArray(
                    new JObject
                    {
                        ["startLine"] = 1,
                        ["startCol"] = 1,
                        ["endLine"] = 1,
                        ["endCol"] = 1,
                        ["newText"] = "// Published edit\n",
                    }
                );
                object[] arguments =
                {
                    Path.GetFullPath(path),
                    path,
                    "Published",
                    edits,
                    Sha(original),
                    immediate ? "immediate" : "deferred",
                    "syntax",
                    false,
                    null,
                    false,
                };
                var response = JObject.FromObject(method.Invoke(null, arguments));
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                var data = response["data"];
                Assert.IsTrue(data.Value<bool>("source_saved"), response.ToString());
                Assert.IsTrue(data.Value<bool>("applied"));
                Assert.AreEqual(Sha(candidate), data.Value<string>("sha256"));
                Assert.AreEqual(immediate ? "import_and_compile" : "schedule_refresh", data.Value<string>("failed_stage"));
                Assert.AreEqual("failed", data.Value<string>("refresh_status"));
                Assert.AreEqual("unknown", data.Value<string>("refresh_confirmation"));
                Assert.AreEqual(JTokenType.Null, data["scheduledRefresh"].Type);
                Assert.LessOrEqual(data.Value<string>("refresh_error").Length, 512);
                StringAssert.Contains("refresh", data.Value<string>("recovery"));
                StringAssert.Contains("Do not replay", data.Value<string>("recovery"));
                var replay = JObject.FromObject(method.Invoke(null, arguments));
                Assert.AreEqual("stale_file", replay["data"].Value<string>("status"));
                Assert.AreEqual(1, writes);
                Assert.AreEqual(1, refreshes);
                // Exercise the existing refresh-only scheduler, with native calls controlled.
                paths.Clear();
                scheduler.GetField("_compilePending", hidden).SetValue(null, false);
                scheduler.GetField("_scheduled", hidden).SetValue(null, false);
                int compilations = 0;
                using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    typeof(ManageScript)
                        .Assembly.GetType("MCPForUnity.Editor.Tools.ManageScriptRefreshHelpers", true)
                        .GetMethod("ScheduleScriptRefresh")
                        .Invoke(null, new object[] { path });
                    scheduler
                        .GetMethod("ProcessPending", hidden)
                        .Invoke(
                            null,
                            new object[]
                            {
                                (Action<string>)(
                                    importPath =>
                                    {
                                        Assert.AreEqual(path, importPath);
                                        refreshes++;
                                    }
                                ),
                                (Action)(() => compilations++),
                                (Action<string>)(message => Assert.Fail(message)),
                            }
                        );
                }
                Assert.AreEqual(2, refreshes, "Refresh-only recovery must import the saved source once.");
                Assert.AreEqual(1, compilations);
                Assert.AreEqual(1, writes, "Refresh-only recovery must not publish the edit again.");
                CollectionAssert.AreEqual(Encoding.UTF8.GetBytes(candidate), File.ReadAllBytes(path));
            }
            finally
            {
                refresh.SetValue(null, priorRefresh);
                write.SetValue(null, priorWrite);
                paths.Clear();
                paths.UnionWith(priorPaths);
                foreach (var item in priorScheduler)
                    item.Key.SetValue(null, item.Value);
                EditorApplication.delayCall = callbacks;
                AssetDatabase.DeleteAsset(folder);
            }
        }

        private static string Sha(string text)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
        }
    }
}
