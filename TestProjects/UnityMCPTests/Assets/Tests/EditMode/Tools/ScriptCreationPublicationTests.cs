using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
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
    }
}
