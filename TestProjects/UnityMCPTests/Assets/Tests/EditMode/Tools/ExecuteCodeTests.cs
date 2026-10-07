using System;
using System.CodeDom.Compiler;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using Microsoft.CSharp;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class ExecuteCodeTests
    {
        [SetUp]
        public void SetUp()
        {
            ExecuteCode.HandleCommand(new JObject { ["action"] = "clear_history" });
        }

        // ──────────────────── Execute: success cases ────────────────────

        [TestCase("auto")]
        [TestCase("codedom")]
        public void Execute_ObjectAliasResolvesUnityEngineObject(string compiler)
        {
            // Given both System and UnityEngine are imported by the wrapper.
            // When the snippet uses their otherwise ambiguous Object name.
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "return typeof(Object).FullName;",
                        ["compiler"] = compiler,
                    }
                )
            );
            // Then the common Unity object spelling compiles on either backend.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("UnityEngine.Object", result["data"]["result"].Value<string>());
        }

        [TestCase("auto")]
        [TestCase("codedom")]
        public void Execute_DiagnosticsKeepSnippetLineNumbersAfterAlias(string compiler)
        {
            // Given an error on the second user line.
            // When the shared wrapper is compiled.
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "int valid = 1;\nreturn MissingAliasRegressionSymbol;",
                        ["compiler"] = compiler,
                    }
                )
            );
            // Then wrapper imports do not shift the reported snippet line.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Line 2:", result["data"]["errors"].ToString());
            StringAssert.Contains("method body", result["data"].Value<string>("hint"));
            if (result["data"].Value<string>("compiler") == "codedom")
            {
                StringAssert.Contains("compiler='roslyn'", result["data"].Value<string>("hint"));
                StringAssert.DoesNotContain("C# 6", result["data"].Value<string>("hint"));
            }
        }

        [Test]
        public void Execute_ReturnString_ReturnsSuccess()
        {
            var result = Execute("return \"hello\";");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("hello", result["data"]["result"].Value<string>());
        }

        [Test]
        public void Execute_ReturnInt_ReturnsSuccess()
        {
            var result = Execute("return 42;");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(42, result["data"]["result"].Value<int>());
        }

        [Test]
        public void Execute_ReturnNull_NoResultValue()
        {
            var result = Execute("int x = 1; return null;");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            // data may contain compiler info but should not have a "result" key
            var data = result["data"] as JObject;
            if (data != null)
                Assert.IsNull(data["result"], "Expected no 'result' key when code returns null");
        }

        [Test]
        public void Execute_VoidReturn_Succeeds()
        {
            var result = Execute("UnityEngine.Debug.Log(\"test\");");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNull(result["data"]["result"]);
        }

        [TestCase("auto")]
        [TestCase("codedom")]
        public void Execute_ConditionalReturn_AllowsFallthrough(string compiler)
        {
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "int x = 1; if (x == 2) return x; // final comment",
                        ["compiler"] = compiler,
                    }
                )
            );

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNull(result["data"]["result"]);
        }

        [Test]
        public void Execute_NestedUnityValue_PreservesStructureAndPrecision()
        {
            var result = Execute("return new { position = new Vector3(1.234567f, 2.345678f, 3.456789f), count = 42 };");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var value = result["data"]["result"];
            Assert.AreEqual(42, value["count"].Value<int>());
            Assert.AreEqual(1.234567f, value["position"]["x"].Value<float>());
            Assert.AreEqual(2.345678f, value["position"]["y"].Value<float>());
            Assert.AreEqual(3.456789f, value["position"]["z"].Value<float>());
        }

        [Test]
        public void Execute_MatrixResult_UsesRawElements()
        {
            var result = Execute("return new Matrix4x4();");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var value = result["data"]["result"] as JObject;
            Assert.IsNotNull(value, result.ToString());
            Assert.AreEqual(16, value.Count);
            Assert.AreEqual(0f, value["m00"].Value<float>());
            Assert.AreEqual(0f, value["m33"].Value<float>());
        }

        [Test]
        public void Execute_UnityAPI_CanAccessSceneManager()
        {
            var result = Execute("var scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene();\n" + "return scene.name;");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["result"]);
        }

        [Test]
        public void Execute_Generics_ListOfString()
        {
            var result = Execute("var list = new System.Collections.Generic.List<string>();\n" + "list.Add(\"a\"); list.Add(\"b\");\n" + "return list;");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var arr = result["data"]["result"] as JArray;
            Assert.IsNotNull(arr, "Expected array result");
            Assert.AreEqual(2, arr.Count);
        }

        [Test]
        public void Execute_LINQ_SelectWorks()
        {
            var result = Execute("var nums = new int[] { 1, 2, 3 };\n" + "return nums.Select(n => n * 2).ToList();");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var arr = result["data"]["result"] as JArray;
            Assert.IsNotNull(arr);
            Assert.AreEqual(3, arr.Count);
            Assert.AreEqual(2, arr[0].Value<int>());
            Assert.AreEqual(6, arr[2].Value<int>());
        }

        [Test]
        public void Execute_Dictionary_ReturnsStructured()
        {
            var result = Execute("var dict = new Dictionary<string, int> { { \"a\", 1 }, { \"b\", 2 } };\n" + "return dict;");

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["result"]);
        }

        // ──────────────────── Execute: error cases ────────────────────

        [Test]
        public void Execute_CompilationError_ReturnsErrors()
        {
            var result = Execute("int x = \"not an int\";");

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Compilation failed", result.Value<string>("error"));
            Assert.IsNotNull(result["data"]["errors"]);
        }

        [Test]
        public void Execute_RuntimeException_ReturnsError()
        {
            var result = Execute("throw new System.Exception(\"boom\");");

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("boom", result.Value<string>("error"));
        }

        [Test]
        public void Execute_MissingCode_ReturnsError()
        {
            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "execute" }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("code", result.Value<string>("error").ToLowerInvariant());
        }

        [Test]
        public void Execute_EmptyCode_ReturnsError()
        {
            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "execute", ["code"] = "   " }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [TestCase("unknown")]
        [TestCase("")]
        public void Execute_InvalidCompiler_RejectsBeforeExecuting(string compiler)
        {
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "return 1;",
                        ["compiler"] = compiler,
                    }
                )
            );

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("compiler", result.Value<string>("error"));
            Assert.AreEqual(0, GetHistoryTotal());
        }

        // ──────────────────── Safety checks ────────────────────

        [Test]
        public void Execute_SafetyChecks_BlocksFileDelete()
        {
            var result = Execute("System.IO.File.Delete(\"x\");");

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Blocked pattern", result.Value<string>("error"));
        }

        [Test]
        public void Execute_SafetyChecks_BlocksProcessStart()
        {
            var result = Execute("Process.Start(\"cmd\");");

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Blocked pattern", result.Value<string>("error"));
        }

        [Test]
        public void Execute_SafetyChecks_BlocksInfiniteLoop()
        {
            var result = Execute("while (true) { }");

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Blocked pattern", result.Value<string>("error"));
        }

        [Test]
        public void Execute_SafetyChecksDisabled_AllowsBlockedPattern()
        {
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "while (true) { break; }  return null;",
                        ["safety_checks"] = false,
                    }
                )
            );

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(0.5)]
        public void Execute_NumericSafetyFlag_RejectsBeforeExecuting(object safetyChecks)
        {
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "while (true) { break; } return 1;",
                        ["safety_checks"] = JToken.FromObject(safetyChecks),
                    }
                )
            );

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("safety_checks", result.Value<string>("error"));
            Assert.AreEqual(0, GetHistoryTotal());
        }

        // ──────────────────── History ────────────────────

        [Test]
        public void GetHistory_Empty_ReturnsZero()
        {
            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history" }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0, result["data"]["total"].Value<int>());
        }

        [Test]
        public void GetHistory_AfterExecution_RecordsEntry()
        {
            Execute("return 1;");

            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history" }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(1, result["data"]["total"].Value<int>());
            var entries = result["data"]["entries"] as JArray;
            Assert.IsNotNull(entries);
            Assert.AreEqual(1, entries.Count);
            Assert.IsTrue(entries[0]["success"].Value<bool>());
        }

        [Test]
        public void GetHistory_Limit_RespectsParameter()
        {
            Execute("return 1;");
            Execute("return 2;");
            Execute("return 3;");

            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history", ["limit"] = 2 }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(3, result["data"]["total"].Value<int>());
            var entries = result["data"]["entries"] as JArray;
            Assert.AreEqual(2, entries.Count);
        }

        [TestCase(0.5)]
        [TestCase(true)]
        public void GetHistory_NonIntegerLimit_ReturnsError(object limit)
        {
            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history", ["limit"] = JToken.FromObject(limit) }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("limit", result.Value<string>("error"));
        }

        [Test]
        public void ClearHistory_RemovesAll()
        {
            Execute("return 1;");
            Execute("return 2;");

            var clearResult = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "clear_history" }));
            Assert.IsTrue(clearResult.Value<bool>("success"), clearResult.ToString());

            var historyResult = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history" }));
            Assert.AreEqual(0, historyResult["data"]["total"].Value<int>());
        }

        // ──────────────────── Replay ────────────────────

        [Test]
        public void Replay_ValidIndex_ReExecutes()
        {
            Execute("return 42;");

            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "replay", ["index"] = 0 }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(42, result["data"]["result"].Value<int>());
        }

        [Test]
        public void Replay_InvalidIndex_ReturnsError()
        {
            Execute("return 1;");

            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "replay", ["index"] = 99 }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Invalid history index", result.Value<string>("error"));
        }

        [TestCase(-0.5)]
        [TestCase(0.5)]
        [TestCase(1.5)]
        [TestCase(true)]
        [TestCase(false)]
        public void Replay_NonIntegerIndex_DoesNotExecute(object index)
        {
            Execute("return 1;");
            Execute("return 2;");
            Execute("return 3;");

            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "replay", ["index"] = JToken.FromObject(index) }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("index", result.Value<string>("error"));
            Assert.AreEqual(3, GetHistoryTotal(), "Invalid replay must not execute or append history.");
        }

        [Test]
        public void Replay_EmptyHistory_ReturnsError()
        {
            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "replay", ["index"] = 0 }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        // ──────────────────── Action validation ────────────────────

        [Test]
        public void UnknownAction_ReturnsError()
        {
            var result = ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "invalid_action" }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Unknown action", result.Value<string>("error"));
        }

        [Test]
        public void NullParams_ReturnsError()
        {
            var result = ToJObject(ExecuteCode.HandleCommand(null));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        // ──────────────────── CodeDom backend ────────────────────

        [TestCase("", "\uFEFF", 0, 0, true)]
        [TestCase("", " \uFEFF\r\n", 0, 0, true)]
        [TestCase("", "", 0, 0, false)]
        [TestCase("", "Compiler failed", 0, 0, false)]
        [TestCase("CS0001", "\uFEFF", 0, 0, false)]
        [TestCase("", "\uFEFF", 1, 0, false)]
        [TestCase("", "\uFEFF", 0, 1, false)]
        public void FixtureCompiler_BomWorkaroundDoesNotHideRealDiagnostics(string number, string text, int line, int column, bool expected)
        {
            // Given either the observed Mono BOM artifact or a genuine diagnostic.
            var diagnostic = new CompilerError("", line, column, number, text);
            // When classifying the fixture compiler output.
            bool ignored = IsStandaloneBomDiagnostic(diagnostic);
            // Then only an unlocated BOM-only diagnostic is eligible for the workaround.
            Assert.AreEqual(expected, ignored);
        }

        // Regression for CoplayDev/unity-mcp#1144: large projects (~100+ asmdefs) blew past the
        // Windows 32 KB CreateProcess limit because every reference became an inline /r: flag.
        // The fix routes references through a @responsefile, so this just verifies that the
        // codedom path still compiles and runs end-to-end.
        [Test]
        public void Execute_CodedomBackend_CompilesAndRuns()
        {
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "return 1 + 1;",
                        ["compiler"] = "codedom",
                    }
                )
            );

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(2, result["data"]["result"].Value<int>());
            Assert.AreEqual("codedom", result["data"]["compiler"].Value<string>());
        }

        [Test]
        public void Execute_CodedomBackend_ResolvesUnityTypes()
        {
            var result = ToJObject(
                ExecuteCode.HandleCommand(
                    new JObject
                    {
                        ["action"] = "execute",
                        ["code"] = "return UnityEngine.Application.unityVersion;",
                        ["compiler"] = "codedom",
                    }
                )
            );

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(result["data"]["result"]);
        }

        [Test]
        public void FilterAssemblyPathsForCodeDom_WithNetstandard_PreservesSystemSecurity()
        {
            var tempRoot = CreateTempDirectory();
            try
            {
                var netstandardPath = CompileVersionedAssembly(tempRoot, "netstandard", "2.0.0.0");
                var securityFixturePath = CompileVersionedAssembly(tempRoot, "SystemSecurityFixture", "4.0.0.0");
                var systemSecurityPath = Path.Combine(Path.GetDirectoryName(securityFixturePath), "System.Security.dll");
                File.Copy(securityFixturePath, systemSecurityPath);

                var filtered = ExecuteCode.FilterAssemblyPathsForCodeDom(new[] { netstandardPath, systemSecurityPath });

                CollectionAssert.Contains(filtered, systemSecurityPath);
            }
            finally
            {
                Directory.Delete(tempRoot, true);
            }
        }

        [Test]
        public void FilterAssemblyPathsForCodeDom_DuplicateNames_PrefersReferencedVersion()
        {
            var tempRoot = CreateTempDirectory();
            try
            {
                var assemblyName = "McpCodeDomDuplicate" + Guid.NewGuid().ToString("N");
                var referencedPath = CompileVersionedAssembly(tempRoot, assemblyName, "1.0.0.0");
                var newerPath = CompileVersionedAssembly(tempRoot, assemblyName, "2.0.0.0");
                LoadAssemblyReferencing(referencedPath);

                var filtered = ExecuteCode.FilterAssemblyPathsForCodeDom(new[] { newerPath, referencedPath });

                Assert.AreEqual(1, filtered.Length);
                Assert.AreEqual(referencedPath, filtered[0]);
            }
            finally
            {
                Directory.Delete(tempRoot, true);
            }
        }

        [Test]
        public void FilterAssemblyPathsForCodeDom_CachedAssemblyPaths_ReusesResultUntilDomainReload()
        {
            var tempRoot = CreateTempDirectory();
            var cachedAssemblyPathsField = typeof(ExecuteCode).GetField("_cachedAssemblyPaths", BindingFlags.NonPublic | BindingFlags.Static);
            var cachedCodeDomAssemblyPathsField = typeof(ExecuteCode).GetField("_cachedCodeDomAssemblyPaths", BindingFlags.NonPublic | BindingFlags.Static);
            var onDomainReload = typeof(ExecuteCode).GetMethod("OnDomainReload", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(cachedAssemblyPathsField);
            Assert.IsNotNull(cachedCodeDomAssemblyPathsField);
            Assert.IsNotNull(onDomainReload);

            try
            {
                onDomainReload.Invoke(null, null);
                var assemblyName = "McpCodeDomCache" + Guid.NewGuid().ToString("N");
                var olderPath = CompileVersionedAssembly(tempRoot, assemblyName, "1.0.0.0");
                var newerPath = CompileVersionedAssembly(tempRoot, assemblyName, "2.0.0.0");
                var cachedAssemblyPaths = new[] { olderPath, newerPath };
                cachedAssemblyPathsField.SetValue(null, cachedAssemblyPaths);

                var first = ExecuteCode.FilterAssemblyPathsForCodeDom(cachedAssemblyPaths);
                Assert.AreEqual(1, first.Length);

                File.WriteAllText(olderPath, "invalidated");
                File.WriteAllText(newerPath, "invalidated");
                var second = ExecuteCode.FilterAssemblyPathsForCodeDom(cachedAssemblyPaths);
                Assert.AreSame(first, second);

                onDomainReload.Invoke(null, null);
                cachedAssemblyPathsField.SetValue(null, cachedAssemblyPaths);
                var afterReload = ExecuteCode.FilterAssemblyPathsForCodeDom(cachedAssemblyPaths);
                Assert.AreNotSame(first, afterReload);
                Assert.AreEqual(2, afterReload.Length);
            }
            finally
            {
                onDomainReload.Invoke(null, null);
                Directory.Delete(tempRoot, true);
            }
        }

        // ──────────────────── Helpers ────────────────────

        private static int GetHistoryTotal()
        {
            return ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "get_history" }))["data"]["total"].Value<int>();
        }

        private static string CreateTempDirectory()
        {
            var path = Path.Combine(Path.GetTempPath(), "UnityMCPTests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(path);
            return path;
        }

        private static string CompileVersionedAssembly(string tempRoot, string assemblyName, string version)
        {
            var outputDirectory = Path.Combine(tempRoot, version);
            Directory.CreateDirectory(outputDirectory);
            var outputPath = Path.Combine(outputDirectory, assemblyName + ".dll");
            var source = "using System.Reflection;\n" + "[assembly: AssemblyVersion(\"" + version + "\")]\n" + "public sealed class VersionMarker { }";

            using (var provider = new CSharpCodeProvider())
            {
                var parameters = new CompilerParameters
                {
                    GenerateExecutable = false,
                    GenerateInMemory = false,
                    OutputAssembly = outputPath,
                };
                var results = provider.CompileAssemblyFromSource(parameters, source);
                AssertCompilerSuccess(results, outputPath);
                var identity = AssemblyName.GetAssemblyName(outputPath);
                Assert.AreEqual(assemblyName, identity.Name);
                Assert.AreEqual(new Version(version), identity.Version);
            }

            return outputPath;
        }

        private static void LoadAssemblyReferencing(string referencedAssemblyPath)
        {
            // Mono CodeDom can report the compiler's standalone BOM as an error and
            // then refuse CompiledAssembly even after successfully writing a DLL.
            // Keep the fixture on disk and verify the result before loading its bytes.
            string outputPath = Path.Combine(Path.GetDirectoryName(referencedAssemblyPath), "ReferenceHolder-" + Guid.NewGuid().ToString("N") + ".dll");
            using (var provider = new CSharpCodeProvider())
            {
                var parameters = new CompilerParameters
                {
                    GenerateExecutable = false,
                    GenerateInMemory = false,
                    OutputAssembly = outputPath,
                };
                parameters.ReferencedAssemblies.Add(referencedAssemblyPath);

                var results = provider.CompileAssemblyFromSource(
                    parameters,
                    "public static class ReferenceHolder { " + "public static System.Type Get() { return typeof(VersionMarker); } }"
                );
                AssertCompilerSuccess(results, outputPath);
                var assembly = Assembly.Load(File.ReadAllBytes(outputPath));
                var expected = AssemblyName.GetAssemblyName(referencedAssemblyPath);
                Assert.IsTrue(
                    assembly.GetReferencedAssemblies().Any(reference => reference.Name == expected.Name && reference.Version.Equals(expected.Version)),
                    "The fixture must load a genuine reference to the requested assembly version."
                );
            }
        }

        private static void AssertCompilerSuccess(CompilerResults results, string outputPath)
        {
            var errors = results
                .Errors.Cast<CompilerError>()
                .Where(error => !error.IsWarning)
                .Where(error => !IsStandaloneBomDiagnostic(error))
                .Select(error => error.ToString())
                .ToArray();
            Assert.AreEqual(0, results.NativeCompilerReturnValue, "Fixture compiler failed: " + string.Join("\n", errors));
            CollectionAssert.IsEmpty(errors);
            Assert.IsTrue(File.Exists(outputPath), "Fixture compiler produced no DLL.");
            Assert.DoesNotThrow(() => AssemblyName.GetAssemblyName(outputPath), "Fixture compiler output must be a readable managed assembly.");
        }

        private static bool IsStandaloneBomDiagnostic(CompilerError error)
        {
            // Do not hide ordinary empty errors: this workaround is only for the
            // observed, unlocated BOM emitted by Unity's installed Mono compiler.
            string text = error.ErrorText ?? "";
            return string.IsNullOrEmpty(error.ErrorNumber)
                && error.Line == 0
                && error.Column == 0
                && text.IndexOf('\uFEFF') >= 0
                && text.Trim('\uFEFF', ' ', '\t', '\r', '\n').Length == 0;
        }

        private static JObject Execute(string code)
        {
            return ToJObject(ExecuteCode.HandleCommand(new JObject { ["action"] = "execute", ["code"] = code }));
        }

        // ──────────────────── Compiled-assembly cache (issue #1351) ────────────────────

        [Test]
        public void Execute_SameSnippetTwice_DoesNotLoadASecondAssembly()
        {
            // Every compile Assembly.Load()s a fresh "MCPDynamic" image that Mono cannot
            // unload, so an uncached recompile leaks one assembly per call.
            const string snippet = "return 41 + 1;";

            // Warm the cache: this call is expected to add exactly one assembly.
            var first = Execute(snippet);
            Assert.IsTrue(first.Value<bool>("success"), first.ToString());

            int loadedBefore = AppDomain.CurrentDomain.GetAssemblies().Length;

            var second = Execute(snippet);
            Assert.IsTrue(second.Value<bool>("success"), second.ToString());
            Assert.AreEqual(42, second["data"]["result"].Value<int>());

            int loadedAfter = AppDomain.CurrentDomain.GetAssemblies().Length;
            Assert.AreEqual(loadedBefore, loadedAfter, "Re-executing an identical snippet loaded another assembly; the compile cache did not hit.");
        }

        [Test]
        public void Execute_DifferentSnippets_StillCompileIndependently()
        {
            var a = Execute("return 1;");
            var b = Execute("return 2;");

            Assert.IsTrue(a.Value<bool>("success"), a.ToString());
            Assert.IsTrue(b.Value<bool>("success"), b.ToString());
            Assert.AreEqual(1, a["data"]["result"].Value<int>());
            Assert.AreEqual(2, b["data"]["result"].Value<int>());
        }
    }
}
