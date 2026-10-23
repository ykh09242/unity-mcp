using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.ReferenceGraph;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class DiagnosticBundleTests
    {
        private static JObject Sample() =>
            JObject.Parse(
                @"{
          'versions':{'unity':'6000.0.69f1','package':'11.16.0-beta.7'},
          'connection':{'selected_transport':'http','selected_scope':'remote','existing_client_connected':true,'endpoint':'https://sk-synthetic-secret.invalid/?key=sk-synthetic-secret'},
          'capabilities':{'tools':{'known':100000,'enabled':80000,'application':{'stage':'applied','desired_revision':7,'confirmed_revision':7,'message':'sk-synthetic-secret','epoch':'sk-synthetic-secret'}},'resources':{'known':50000,'enabled':40000,'configuration':{'key':'sk-synthetic-secret'}}},
          'graph':{'revision':8,'ready':1000000,'pending':45,'errors':1,'native_current':false,'coverage':'partial_static','worker_error':'sk-synthetic-secret','providers':{'external:sk-synthetic-secret':{'state':'failed','error':'sk-synthetic-secret','root':'C:/sk-synthetic-secret','last_verified_at':1792627200}}},
          'operation':{'owner':'build','job_id':'11111111111111111111111111111111','state':'failed','active':false,'can_cancel':false,'evidence_available':true,'phase':'sk-synthetic-secret','detail':'sk-synthetic-secret','result':{'key':'sk-synthetic-secret'}},
          'api_key':'sk-synthetic-secret'
        }"
            );

        private static JObject Build(JObject input, int categories)
        {
            var type = typeof(MCPForUnityEditorWindow).Assembly.GetType("MCPForUnity.Editor.Windows.McpDiagnosticBundle");
            Assert.IsNotNull(type, "Explicit allowlisted diagnostic export must be available.");
            return (JObject)
                type.GetMethod("Build", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { input, categories, DateTimeOffset.UtcNow });
        }

        [Test]
        public void SelectedCategoriesExportAllowlistedSummariesWithoutRawSecrets()
        {
            var all = Build(Sample(), 31);
            StringAssert.DoesNotContain("sk-synthetic-secret", all.ToString());
            Assert.AreEqual(100000, all["capabilities"]["tools"].Value<int>("known"));
            Assert.AreEqual(1000000, all["graph"].Value<int>("ready"));
            Assert.AreEqual(1, all["graph"].Value<int>("provider_count"));
            Assert.AreEqual("applied", all["capabilities"]["tools"]["application"].Value<string>("stage"));
            Assert.AreEqual("unavailable", all["capabilities"]["resources"].Value<string>("server_confirmation"));
            Assert.AreEqual("build", all["operation"].Value<string>("owner"));
            var versionOnly = Build(Sample(), 1);
            Assert.IsNotNull(versionOnly["versions"]);
            Assert.IsNull(versionOnly["connection"]);
            Assert.IsNull(versionOnly["graph"]);
            Assert.IsNull(versionOnly["operation"]);
            Assert.IsNull(Build(Sample(), 0)["versions"]);
        }

        [Test]
        public void MalformedAndUnavailableObservationsCannotLeakThroughTypedFields()
        {
            var data = Sample();
            data["versions"]["unity"] = "6000.0.69f1-sk-synthetic-secret";
            data["versions"]["package"] = new JObject { ["key"] = "sk-synthetic-secret" };
            data["connection"]["selected_transport"] = "sk-synthetic-secret";
            data["connection"]["existing_client_connected"] = new JObject { ["key"] = "sk-synthetic-secret" };
            data["graph"]["revision"] = new JObject { ["key"] = "sk-synthetic-secret" };
            data["operation"]["owner"] = "sk-synthetic-secret";
            data["operation"]["state"] = "sk-synthetic-secret";
            data["operation"]["job_id"] = "sk-synthetic-secret";
            var result = Build(data, 31);
            StringAssert.DoesNotContain("sk-synthetic-secret", result.ToString());
            Assert.AreEqual("unknown", result["versions"].Value<string>("unity"));
            Assert.AreEqual("unknown", result["connection"].Value<string>("selected_transport"));
            Assert.AreEqual("unknown", result["operation"].Value<string>("owner"));
            var unavailable = Build(new JObject(), 31);
            Assert.IsFalse(unavailable["graph"].Value<bool>("available"));
            Assert.IsFalse(unavailable["operation"].Value<bool>("available"));
        }

        [Test]
        public void MalformedContainersAndLargeNumbersRemainUnavailable()
        {
            var data = JObject.Parse(
                "{'versions':['sk-synthetic-secret'],'connection':'sk-synthetic-secret','capabilities':1,'graph':{'revision':999999999999999999999999999999999999},'operation':[]}"
            );
            var result = Build(data, 31);
            Assert.AreEqual("unknown", result["versions"].Value<string>("unity"));
            Assert.IsFalse(result["capabilities"]["tools"].Value<bool>("available"));
            Assert.AreEqual(JTokenType.Null, result["graph"]["revision"].Type);
            StringAssert.DoesNotContain("sk-synthetic-secret", result.ToString());
            var valid = Sample();
            valid["graph"]["providers"]["external:sk-synthetic-secret"]["last_verified_at"] = 1792627200.75;
            Assert.AreEqual(1792627200L, Build(valid, 8)["graph"].Value<long>("oldest_verification_unix_seconds"));
            valid["versions"]["unity"] = "6000.0.69f1\n";
            valid["versions"]["package"] = "11.16.0-beta.7\n";
            Assert.AreEqual("unknown", Build(valid, 1)["versions"].Value<string>("unity"));
            Assert.AreEqual("unknown", Build(valid, 1)["versions"].Value<string>("package"));
        }

        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic;
        private static Type WindowType => typeof(MCPForUnityEditorWindow).Assembly.GetType("MCPForUnity.Editor.Windows.McpDiagnosticWindow");

        private static EditorWindow Create(Func<int, JObject> source)
        {
            Assert.IsNotNull(WindowType);
            var window = (EditorWindow)ScriptableObject.CreateInstance(WindowType);
            WindowType.GetMethod("Configure", Instance).Invoke(window, new object[] { source });
            window.ShowUtility();
            WindowType.GetMethod("CreateGUI", Instance).Invoke(window, null);
            return window;
        }

        private static void Set(EditorWindow window, string name, object value) => WindowType.GetField(name, Instance).SetValue(window, value);

        private static void Click(EditorWindow window, string name)
        {
            var button = window.rootVisualElement.Q<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledSelf, name);
            typeof(Clickable).GetMethod("Invoke", Instance).Invoke(button.clickable, new object[] { null });
        }

        [Test]
        public void PreviewIsExplicitAndCopySaveExportExactlyTheReviewedSnapshot()
        {
            int captures = 0,
                selected = 0,
                saves = 0;
            var input = Sample();
            var window = Create(mask =>
            {
                captures++;
                selected = mask;
                return input;
            });
            try
            {
                var root = window.rootVisualElement;
                Assert.Zero(captures);
                Assert.IsFalse(root.Q<Button>("diagnostics-copy").enabledSelf);
                Assert.IsFalse(root.Q<Button>("diagnostics-save").enabledSelf);
                Click(window, "diagnostics-generate");
                Assert.AreEqual(1, captures);
                Assert.AreEqual(31, selected);
                string preview = root.Q<TextField>("diagnostics-preview").value;
                StringAssert.DoesNotContain("sk-synthetic-secret", preview);
                input["graph"]["ready"] = 2000000;
                string copied = null,
                    savedPath = null,
                    savedText = null;
                Set(window, "CopyText", (Action<string>)(text => copied = text));
                Set(
                    window,
                    "SaveText",
                    (Action<string, string>)(
                        (path, text) =>
                        {
                            saves++;
                            savedPath = path;
                            savedText = text;
                        }
                    )
                );
                Click(window, "diagnostics-copy");
                Assert.AreEqual(preview, copied);
                Set(window, "ChooseSavePath", (Func<string>)(() => ""));
                Click(window, "diagnostics-save");
                Assert.Zero(saves);
                Set(window, "ChooseSavePath", (Func<string>)(() => "synthetic-selected-path.json"));
                Click(window, "diagnostics-save");
                Assert.AreEqual(1, saves);
                Assert.AreEqual("synthetic-selected-path.json", savedPath);
                Assert.AreEqual(preview, savedText);
                Assert.AreEqual(1, captures);
                root.Q<Toggle>("diagnostics-category-8").value = false;
                Assert.IsEmpty(root.Q<TextField>("diagnostics-preview").value);
                Assert.IsFalse(root.Q<Button>("diagnostics-copy").enabledSelf);
                Assert.IsFalse(root.Q<Button>("diagnostics-save").enabledSelf);
                Click(window, "diagnostics-generate");
                Assert.AreEqual(23, selected);
                Assert.IsNull(JObject.Parse(root.Q<TextField>("diagnostics-preview").value)["graph"]);
                Assert.AreEqual(2, captures);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        [Test]
        public void CaptureExportFailuresAndReloadDoNotExposeExceptionPayloads()
        {
            var window = Create(_ => throw new InvalidOperationException("sk-synthetic-secret"));
            try
            {
                Click(window, "diagnostics-generate");
                Assert.IsFalse(window.rootVisualElement.Q<Button>("diagnostics-copy").enabledSelf);
                StringAssert.DoesNotContain("sk-synthetic-secret", window.rootVisualElement.Q<Label>("diagnostics-status").text);
                WindowType.GetMethod("Configure", Instance).Invoke(window, new object[] { (Func<int, JObject>)(_ => Sample()) });
                Click(window, "diagnostics-generate");
                Set(window, "CopyText", (Action<string>)(_ => throw new IOException("sk-synthetic-secret")));
                Click(window, "diagnostics-copy");
                StringAssert.Contains("Could not copy", window.rootVisualElement.Q<Label>("diagnostics-status").text);
                Set(window, "ChooseSavePath", (Func<string>)(() => "synthetic-selected-path.json"));
                Set(window, "SaveText", (Action<string, string>)((_, __) => throw new IOException("sk-synthetic-secret")));
                Click(window, "diagnostics-save");
                StringAssert.Contains("Could not save", window.rootVisualElement.Q<Label>("diagnostics-status").text);
                StringAssert.DoesNotContain("sk-synthetic-secret", window.rootVisualElement.Q<Label>("diagnostics-status").text);
                WindowType.GetMethod("Configure", Instance).Invoke(window, new object[] { (Func<int, JObject>)(_ => null) });
                Click(window, "diagnostics-generate");
                StringAssert.Contains("unavailable", window.rootVisualElement.Q<Label>("diagnostics-status").text);
                WindowType.GetMethod("OnDisable", Instance).Invoke(window, null);
                WindowType.GetMethod("CreateGUI", Instance).Invoke(window, null);
                Assert.IsEmpty(window.rootVisualElement.Q<TextField>("diagnostics-preview").value);
                Assert.IsFalse(window.rootVisualElement.Q<Button>("diagnostics-generate").enabledSelf);
                StringAssert.Contains("Reopen", window.rootVisualElement.Q<Label>("diagnostics-status").text);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }

        private sealed class Host : EditorWindow { }

        [Test]
        public async Task ReferenceDiagnosticsNeverCrossIdentityOrRefreshTheGraph()
        {
            string identity = "fixture-account-a";
            int requests = 0;
            var host = ScriptableObject.CreateInstance<Host>();
            try
            {
                using var section = new McpReferenceGraphSection(
                    host.rootVisualElement,
                    (_, __, ___) =>
                    {
                        requests++;
                        return Task.FromResult(new JObject { ["graph_status"] = Sample()["graph"] });
                    },
                    () => identity
                );
                section.SetVisible(true);
                var capture = typeof(McpReferenceGraphSection).GetMethod("CaptureDiagnostics", Instance);
                Assert.IsNull(capture.Invoke(section, null));
                await section.Run("overview", new JObject());
                int before = requests;
                var summary = (JObject)capture.Invoke(section, null);
                Assert.IsNotNull(summary);
                StringAssert.DoesNotContain("sk-synthetic-secret", summary.ToString());
                identity = "fixture-account-b";
                Assert.IsNull(capture.Invoke(section, null));
                Assert.AreEqual(before, requests);
                section.Dispose();
                Assert.IsNull(capture.Invoke(section, null));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(host);
            }
        }

        [Test]
        public void ProducerReadsOnlyChosenExistingObservationsWithoutInitializingTransport()
        {
            var owner = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
            try
            {
                var field = typeof(MCPServiceLocator).GetField("_transportManager", BindingFlags.Static | BindingFlags.NonPublic);
                var existing = field.GetValue(null);
                var capture = typeof(MCPForUnityEditorWindow).GetMethod("CaptureDiagnosticInput", Instance);
                Assert.IsNull(capture.Invoke(owner, new object[] { 31 }));
                typeof(MCPForUnityEditorWindow).GetField("guiCreated", Instance).SetValue(owner, true);
                Assert.Zero(((JObject)capture.Invoke(owner, new object[] { 0 })).Count);
                var data = (JObject)capture.Invoke(owner, new object[] { 31 });
                Assert.IsNotNull(data["versions"]);
                Assert.AreEqual(JTokenType.Null, data["graph"].Type);
                Assert.IsFalse(Build(data, 4)["capabilities"]["tools"].Value<bool>("available"));
                Assert.IsFalse(Build(data, 4)["capabilities"]["resources"].Value<bool>("available"));
                Assert.AreSame(existing, field.GetValue(null));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(owner);
            }
        }

        [UnityTest, Explicit("Actual diagnostic preview in narrow/wide native light/dark themes.")]
        public IEnumerator ActualDiagnosticPreviewLayout()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var skin = typeof(EditorGUIUtility).GetProperty("skinIndex", BindingFlags.Static | BindingFlags.NonPublic);
            var original = skin.GetValue(null);
            EditorWindow window = null;
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/diagnostics-" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            try
            {
                foreach (int theme in new[] { 1, 0 })
                {
                    skin.SetValue(null, theme);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    Assert.AreEqual(theme == 1, EditorGUIUtility.isProSkin);
                    window = Create(_ => Sample());
                    window.titleContent = new GUIContent("Diagnostic bundle");
                    window.minSize = new Vector2(360, 320);
                    window.ShowUtility();
                    foreach (var size in new[] { new Vector2(360, 320), new Vector2(500, 600), new Vector2(900, 700) })
                    {
                        window.position = new Rect(30, 30, size.x, size.y);
                        Click(window, "diagnostics-generate");
                        var body = window.rootVisualElement.Q<ScrollView>("diagnostics-body");
                        body.scrollOffset = Vector2.zero;
                        for (int i = 0; i < 12; i++)
                        {
                            window.Repaint();
                            yield return null;
                        }
                        foreach (string name in new[] { "diagnostics-generate", "diagnostics-copy", "diagnostics-save" })
                        {
                            var button = window.rootVisualElement.Q<Button>(name);
                            Assert.GreaterOrEqual(button.worldBound.height, 28);
                            Assert.LessOrEqual(button.worldBound.xMax, window.rootVisualElement.worldBound.xMax);
                        }
                        Assert.LessOrEqual(body.horizontalScroller.highValue, 1);
                        string path = Path.Combine(directory, "diagnostics-" + size.x + "-" + size.y + "-" + theme + ".png");
                        typeof(OperationActivityTests)
                            .GetMethod("Capture", BindingFlags.Static | BindingFlags.NonPublic)
                            .Invoke(null, new object[] { window, path });
                        TestContext.Out.WriteLine("DIAGNOSTICS_CAPTURE " + path);
                        if (size.x == 360)
                        {
                            body.ScrollTo(window.rootVisualElement.Q<TextField>("diagnostics-preview"));
                            for (int i = 0; i < 12; i++)
                            {
                                window.Repaint();
                                yield return null;
                            }
                            path = Path.Combine(directory, "diagnostics-preview-360-320-" + theme + ".png");
                            typeof(OperationActivityTests)
                                .GetMethod("Capture", BindingFlags.Static | BindingFlags.NonPublic)
                                .Invoke(null, new object[] { window, path });
                            TestContext.Out.WriteLine("DIAGNOSTICS_CAPTURE " + path);
                        }
                    }
                    window.Close();
                    window = null;
                }
            }
            finally
            {
                if (window != null)
                    window.Close();
                skin.SetValue(null, original);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }
    }
}
