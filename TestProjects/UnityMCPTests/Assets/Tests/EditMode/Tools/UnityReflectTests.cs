using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    [TestFixture]
    public class UnityReflectTests
    {
        [OneTimeSetUp]
        public void LoadFixtureAssembly()
        {
            Assert.AreEqual("TestAsmdef", typeof(TestNamespace.CustomComponent).Assembly.GetName().Name);
        }

        private static JObject Invoke(string action, JObject extraParams = null)
        {
            var p = extraParams ?? new JObject();
            p["action"] = action;
            var result = UnityReflect.HandleCommand(p);
            return JObject.FromObject(result);
        }

        // ── get_type ────────────────────────────────────────────────

        [Test]
        public void GetType_Transform_ReturnsFound()
        {
            var jo = Invoke("get_type", new JObject { ["class_name"] = "UnityEngine.Transform" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["found"]);
            Assert.AreEqual("UnityEngine.Transform", (string)data["full_name"]);
        }

        [Test]
        public void GetType_ShortName_ResolvesNamespace()
        {
            var jo = Invoke("get_type", new JObject { ["class_name"] = "NavMeshAgent" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["found"]);
            Assert.AreEqual("UnityEngine.AI.NavMeshAgent", (string)data["full_name"]);
        }

        [Test]
        public void GetType_HasMembers()
        {
            var jo = Invoke("get_type", new JObject { ["class_name"] = "Camera" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["found"]);

            var members = data["members"];
            Assert.IsNotNull(members, "members should be present");

            var methods = (JArray)members["methods"];
            Assert.IsNotNull(methods, "methods array should be present");
            Assert.Greater(methods.Count, 0, "Camera should have methods");

            var properties = (JArray)members["properties"];
            Assert.IsNotNull(properties, "properties array should be present");
            Assert.Greater(properties.Count, 0, "Camera should have properties");
        }

        [Test]
        public void GetType_Ambiguous_ReturnsMultiple()
        {
            var jo = Invoke("get_type", new JObject { ["class_name"] = "Button" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["ambiguous"], "Button should be ambiguous (UnityEngine.UI.Button vs UnityEngine.UIElements.Button)");

            var matches = (JArray)data["matches"];
            Assert.IsNotNull(matches, "matches array should be present");
            Assert.Greater(matches.Count, 1, "Should have multiple matches");
        }

        [Test]
        public void GetType_NotFound_ReturnsFalse()
        {
            var jo = Invoke("get_type", new JObject { ["class_name"] = "TotallyFakeClass12345" });

            Assert.IsTrue((bool)jo["success"], "Should be a SuccessResponse even for not-found");
            var data = jo["data"];
            Assert.IsFalse((bool)data["found"]);
        }

        [Test]
        public void GetType_MissingClassName_ReturnsError()
        {
            var jo = Invoke("get_type", new JObject());

            Assert.IsFalse((bool)jo["success"]);
            Assert.IsNotNull(jo["error"], "Should have an error message");
        }

        // ── get_member ──────────────────────────────────────────────

        [Test]
        public void GetMember_Method_ReturnsSignature()
        {
            var jo = Invoke("get_member", new JObject { ["class_name"] = "Physics", ["member_name"] = "Raycast" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["found"]);
            Assert.AreEqual("method", (string)data["member_type"]);
            Assert.Greater((int)data["overload_count"], 0, "Raycast should have overloads");

            var overloads = (JArray)data["overloads"];
            Assert.IsNotNull(overloads);
            Assert.Greater(overloads.Count, 0);
        }

        [Test]
        public void GetMember_Property_ReturnsPropertyInfo()
        {
            var jo = Invoke("get_member", new JObject { ["class_name"] = "UnityEngine.Transform", ["member_name"] = "position" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["found"]);
            Assert.AreEqual("property", (string)data["member_type"]);
            Assert.IsNotNull(data["property_type"]);
        }

        [Test]
        public void GetMember_NotFound_ReturnsFalse()
        {
            var jo = Invoke("get_member", new JObject { ["class_name"] = "Physics", ["member_name"] = "TotallyFakeMethod" });

            Assert.IsTrue((bool)jo["success"], "Should be a SuccessResponse even for not-found member");
            var data = jo["data"];
            Assert.IsFalse((bool)data["found"]);
        }

        // ── search ──────────────────────────────────────────────────

        [Test]
        public void Search_NavMesh_FindsMultipleTypes()
        {
            var jo = Invoke("search", new JObject { ["query"] = "NavMesh", ["scope"] = "unity" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.Greater((int)data["count"], 1, "NavMesh should match multiple types");
        }

        [Test]
        public void Search_ExactMatch_RankedFirst()
        {
            var jo = Invoke("search", new JObject { ["query"] = "Camera", ["scope"] = "unity" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.Greater((int)data["count"], 0);

            var results = (JArray)data["results"];
            var firstFullName = (string)results[0]["full_name"];
            Assert.That(firstFullName, Does.EndWith(".Camera"), "First result should be an exact match ending with '.Camera'");
        }

        [Test]
        public void Search_NoResults_ReturnsZeroCount()
        {
            var jo = Invoke("search", new JObject { ["query"] = "ZzzNonexistentType999", ["scope"] = "unity" });

            Assert.IsTrue((bool)jo["success"], "Should be a SuccessResponse even with no results");
            var data = jo["data"];
            Assert.AreEqual(0, (int)data["count"]);
        }

        // ── generic types ───────────────────────────────────────────

        private static JObject Paging(int size = 25, string cursor = null, string kind = null, string assembly = null)
        {
            var request = new JObject
            {
                ["query"] = "Dev07Paging",
                ["scope"] = "all",
                ["page_size"] = size,
            };
            if (cursor != null)
                request["cursor"] = cursor;
            if (kind != null)
                request["type_kind"] = kind;
            if (assembly != null)
                request["assembly"] = assembly;
            return Invoke("search", request);
        }

        [Test]
        public void Search_DefaultPagePreservesLimitAndReportsTotal()
        {
            var jo = Invoke("search", new JObject { ["query"] = "Dev07Paging", ["scope"] = "all" });
            Assert.IsTrue((bool)jo["success"]);
            Assert.AreEqual(25, (int)jo["data"]["count"]);
            Assert.AreEqual(48, (int)jo["data"]["total_count"]);
            Assert.IsTrue((bool)jo["data"]["has_more"]);
            Assert.IsNotEmpty((string)jo["data"]["next_cursor"]);
        }

        [TestCase(1)]
        [TestCase(7)]
        [TestCase(25)]
        [TestCase(100)]
        public void Search_AllPagesHaveWholeUnionAndStableAssemblyTies(int size)
        {
            var rows = new List<JToken>();
            var pages = new JArray();
            string cursor = null;
            do
            {
                var page = Paging(size, cursor);
                Assert.IsTrue((bool)page["success"], page.ToString());
                pages.Add(page.DeepClone());
                var data = page["data"];
                Assert.AreEqual(48, (int)data["total_count"]);
                rows.AddRange(data["results"]);
                cursor = (string)data["next_cursor"];
                Assert.LessOrEqual(pages.Count, 49);
                Assert.AreEqual(cursor != null, (bool)data["has_more"]);
            } while (cursor != null);
            Assert.AreEqual(48, rows.Count);
            Assert.AreEqual(48, rows.Select(r => (string)r["assembly_full_name"] + "|" + (string)r["full_name"]).Distinct().Count());
            Assert.AreEqual("Dev07Paging", (string)rows[0]["name"]);
            Assert.AreEqual("XDev07PagingContains", (string)rows.Last()["name"]);
            var duplicate = rows.Where(r => (string)r["name"] == "Dev07PagingDuplicate").ToArray();
            Assert.AreEqual(2, duplicate.Length);
            Assert.Less(string.CompareOrdinal((string)duplicate[0]["assembly_full_name"], (string)duplicate[1]["assembly_full_name"]), 0);
            CollectionAssert.AreEqual(
                rows.Select(r => r.ToString(Formatting.None)),
                ((JArray)Paging(100)["data"]["results"]).Select(r => r.ToString(Formatting.None))
            );
            if (size == 7)
                TestContext.WriteLine("DEV07_NATIVE_PAGES=" + pages.ToString(Formatting.None));
        }

        [TestCase("class", 44)]
        [TestCase("interface", 1)]
        [TestCase("struct", 1)]
        [TestCase("enum", 1)]
        [TestCase("delegate", 1)]
        public void Search_KindFilterHasExplicitPartition(string kind, int count)
        {
            var page = Paging(100, kind: kind);
            Assert.IsTrue((bool)page["success"], page.ToString());
            Assert.AreEqual(count, (int)page["data"]["total_count"]);
            Assert.IsTrue(page["data"]["results"].All(r => (string)r["type_kind"] == kind));
        }

        [Test]
        public void Search_AssemblyFilterAndLegacyScopeIntersect()
        {
            var simple = Paging(100, assembly: "TestAsmdef");
            Assert.AreEqual(47, (int)simple["data"]["total_count"]);
            string full = typeof(TestNamespace.CustomComponent).Assembly.FullName;
            Assert.AreEqual(simple["data"]["results"].ToString(Formatting.None), Paging(100, assembly: full)["data"]["results"].ToString(Formatting.None));
            var scoped = Invoke(
                "search",
                new JObject
                {
                    ["query"] = "Dev07Paging",
                    ["scope"] = "project",
                    ["assembly"] = "TestAsmdef",
                }
            );
            Assert.AreEqual(0, (int)scoped["data"]["total_count"]);
        }

        [Test]
        public void Search_CursorReplayMismatchTamperAndStaleAreExplicit()
        {
            string cursor = (string)Paging(7)["data"]["next_cursor"];
            Assert.IsNotEmpty(cursor);
            var second = Paging(7, cursor);
            Assert.AreEqual(second["data"]["results"].ToString(Formatting.None), Paging(7, cursor)["data"]["results"].ToString(Formatting.None));
            Assert.AreEqual((string)second["data"]["next_cursor"], (string)Paging(7, cursor)["data"]["next_cursor"]);
            Assert.AreEqual("cursor_mismatch", (string)Paging(25, cursor)["code"]);
            Assert.AreEqual("invalid_cursor", (string)Paging(7, cursor.Substring(0, cursor.Length - 3) + "xyz")["code"]);
            typeof(UnityReflect).GetMethod("InvalidateCache", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            Assert.AreEqual("stale_cursor", (string)Paging(7, cursor)["code"]);
        }

        [TestCase("kelvin")]
        [TestCase("long_s")]
        [TestCase("case_only")]
        public void Search_CursorRequiresExactAssemblySpelling(string variant)
        {
            string assembly = typeof(TestNamespace.CustomComponent).Assembly.FullName;
            string changed =
                variant == "kelvin" ? assembly.Replace("KeyToken", "\u212AeyToken")
                : variant == "long_s" ? assembly.Replace("Asmdef", "A\u017Fmdef")
                : assembly.ToUpperInvariant();
            Assert.AreNotEqual(assembly, changed);
            var first = Paging(7, assembly: assembly);
            for (int attempt = 0; attempt < 3 && (string)first["code"] == "catalog_changed"; attempt++)
                first = Paging(7, assembly: assembly);
            Assert.IsTrue((bool)first["success"], first.ToString());
            string cursor = (string)first["data"]["next_cursor"];
            Assert.IsNotEmpty(cursor, first.ToString());
            var same = Paging(7, cursor, assembly: assembly);
            Assert.IsTrue((bool)same["success"], same.ToString());
            Assert.AreEqual(
                same["data"]["results"].ToString(Formatting.None),
                Paging(7, cursor, assembly: assembly)["data"]["results"].ToString(Formatting.None)
            );
            Assert.AreEqual("cursor_mismatch", (string)Paging(7, cursor, assembly: changed)["code"]);
        }

        [Test]
        public void Search_InitialAssemblyMatchingRemainsCaseInsensitive()
        {
            var original = Paging(100, assembly: "TestAsmdef");
            var differentCase = Paging(100, assembly: "TESTASMDEF");
            Assert.IsTrue((bool)original["success"]);
            Assert.IsTrue((bool)differentCase["success"]);
            Assert.AreEqual(47, (int)differentCase["data"]["total_count"]);
            Assert.AreEqual(original["data"]["results"].ToString(Formatting.None), differentCase["data"]["results"].ToString(Formatting.None));
        }

        [TestCase("page_size", "0")]
        [TestCase("page_size", "101")]
        [TestCase("page_size", "true")]
        [TestCase("page_size", "1.5")]
        [TestCase("page_size", "\"7\"")]
        [TestCase("query", "[]")]
        [TestCase("query", "\"\"")]
        [TestCase("assembly", "\"\"")]
        [TestCase("type_kind", "\"unknown\"")]
        [TestCase("cursor", "\"bad-token\"")]
        public void Search_InvalidPagingInputIsStructured(string field, string json)
        {
            var request = new JObject
            {
                ["query"] = "Dev07Paging",
                ["scope"] = "all",
                [field] = JToken.Parse(json),
            };
            Assert.IsFalse((bool)Invoke("search", request)["success"]);
        }

        [Test]
        public void Search_CursorExpiryUsesFirstIssuanceAndBoundedPageBytes()
        {
            var clock = typeof(UnityReflect).GetField("_searchClock", BindingFlags.NonPublic | BindingFlags.Static);
            var bytes = typeof(UnityReflect).GetField("_pageByteLimit", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.NotNull(clock);
            Assert.NotNull(bytes);
            var initialClock = clock.GetValue(null);
            var initialBytes = bytes.GetValue(null);
            try
            {
                var now = DateTimeOffset.UtcNow;
                clock.SetValue(null, (Func<DateTimeOffset>)(() => now));
                string cursor = (string)Paging(7)["data"]["next_cursor"];
                now = now.AddSeconds(299);
                var next = Paging(7, cursor);
                Assert.IsTrue((bool)next["success"]);
                now = now.AddSeconds(2);
                Assert.AreEqual("cursor_expired", (string)Paging(7, (string)next["data"]["next_cursor"])["code"]);
                bytes.SetValue(null, 8192);
                var page = Paging(100);
                Assert.IsTrue((bool)page["success"], page.ToString());
                Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(page.ToString(Formatting.None)), 8192);
                Assert.Greater((int)page["data"]["count"], 0);
                Assert.IsTrue((bool)page["data"]["has_more"]);
            }
            finally
            {
                clock.SetValue(null, initialClock);
                bytes.SetValue(null, initialBytes);
            }
        }

        [Test]
        public void Search_NewLoadedAssemblyStalesCursorAndReportsDynamicCoverage()
        {
            string cursor = (string)Paging(7)["data"]["next_cursor"];
            string name = "Dev07DynamicWitness" + Guid.NewGuid().ToString("N");
            var builder = AssemblyBuilder.DefineDynamicAssembly(new AssemblyName(name), AssemblyBuilderAccess.Run);
            builder.DefineDynamicModule(name).DefineType(name + ".Dev07PagingLate", TypeAttributes.Public).CreateType();
            Assert.AreEqual("stale_cursor", (string)Paging(7, cursor)["code"]);
            var page = Paging(7);
            Assert.IsTrue((bool)page["success"]);
            Assert.IsFalse((bool)page["data"]["complete"]);
            Assert.Greater((int)page["data"]["enumeration_warning_count"], 0);
            Assert.LessOrEqual(((JArray)page["data"]["enumeration_warnings"]).Count, 16);
        }

        [TestCase("_publicTypeLimit", "search_limit_exceeded")]
        [TestCase("_searchMatchLimit", "search_limit_exceeded")]
        [TestCase("_pageByteLimit", "result_too_large")]
        [TestCase("_workBudgetSeconds", "search_budget_exceeded")]
        public void Search_AdmissionRefusesRatherThanInventingPartialTotals(string fieldName, string code)
        {
            var field = typeof(UnityReflect).GetField(fieldName, BindingFlags.NonPublic | BindingFlags.Static);
            Assert.NotNull(field);
            object previous = field.GetValue(null);
            try
            {
                field.SetValue(null, field.FieldType == typeof(double) ? (object)(-1.0) : 1);
                typeof(UnityReflect).GetMethod("InvalidateCache", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
                var result = Paging(7);
                Assert.IsFalse((bool)result["success"]);
                Assert.AreEqual(code, (string)result["code"]);
                Assert.IsNull(result["data"]?["total_count"]);
            }
            finally
            {
                field.SetValue(null, previous);
                typeof(UnityReflect).GetMethod("InvalidateCache", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            }
        }

        [Test]
        public void GetType_GenericList_Resolves()
        {
            var jo = Invoke("get_type", new JObject { ["class_name"] = "List<T>" });

            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["found"], "List<T> should resolve via generic normalization");
        }

        [Test]
        public void GetType_GenericDictionary_Resolves()
        {
            var jo = Invoke("get_type", new JObject { ["class_name"] = "Dictionary<TKey, TValue>" });
            Assert.IsTrue((bool)jo["success"]);
            var data = jo["data"];
            Assert.IsTrue((bool)data["found"], "Dictionary<TKey, TValue> should resolve via generic normalization");
        }
    }
}

namespace MCPForUnityTests.Dev07PagingFixtures
{
    public class Dev07PagingDuplicate { }
}

namespace MCPForUnityTests.Editor.Tools
{
    // Kept outside UnityReflectTests so the normal fixture never reloads.
    [TestFixture]
    public class UnityReflectReloadProbeTests
    {
        private const string Prefix = "MCPForUnity.DEV07.ReflectionReload.";
        private const string Active = Prefix + "active";
        private const string Cursor = Prefix + "cursor";
        private const string Generation = Prefix + "generation";
        private const string BeforeDomain = Prefix + "domain";
        private const string Witness = Prefix + "witness";
        private static readonly string DomainNonce = Guid.NewGuid().ToString("N");

        [UnityEngine.TestTools.UnityTest]
        [Explicit("Only the serial native QA owner may run this in an opted-in disposable project.")]
        public System.Collections.IEnumerator ActualDomainReload_InvalidatesSearchCursor()
        {
            try
            {
                PrepareProbe();
                UnityEditor.EditorUtility.RequestScriptReload();
                yield return new UnityEngine.TestTools.WaitForDomainReload();
                VerifyProbe();
            }
            finally
            {
                CleanupProbe();
            }
        }

        private static JObject Search(string cursor = null)
        {
            // Resolve all types and static state afresh after the runner resumes.
            Assert.AreEqual("TestAsmdef", typeof(TestNamespace.CustomComponent).Assembly.GetName().Name);
            var request = new JObject
            {
                ["action"] = "search",
                ["query"] = "Dev07Paging",
                ["scope"] = "all",
                ["page_size"] = 7,
            };
            if (cursor != null)
                request["cursor"] = cursor;
            return JObject.FromObject(UnityReflect.HandleCommand(request));
        }

        private static void PrepareProbe()
        {
            string expected = Environment.GetEnvironmentVariable("DEV07_RELOAD_PROBE_PROJECT");
            string actual = System.IO.Directory.GetParent(UnityEngine.Application.dataPath).FullName;
            Assert.IsFalse(string.IsNullOrEmpty(expected), "Explicit probe requires DEV07_RELOAD_PROBE_PROJECT.");
            Assert.AreEqual(
                System.IO.Path.GetFullPath(expected).TrimEnd('/', '\\').ToLowerInvariant(),
                System.IO.Path.GetFullPath(actual).TrimEnd('/', '\\').ToLowerInvariant(),
                "Probe requires the exact disposable QA project path."
            );
            Assert.IsFalse(UnityEditor.SessionState.GetBool(Active, false), "A previous owned probe is still active.");
            var page = Search();
            for (int attempt = 0; attempt < 3 && (string)page["code"] == "catalog_changed"; attempt++)
                page = Search();
            Assert.IsTrue((bool)page["success"], page.ToString());
            Assert.IsNotEmpty((string)page["data"]["next_cursor"]);
            UnityEditor.SessionState.SetString(Cursor, (string)page["data"]["next_cursor"]);
            UnityEditor.SessionState.SetString(Generation, (string)page["data"]["catalog_generation"]);
            UnityEditor.SessionState.SetString(BeforeDomain, DomainNonce);
            UnityEditor.SessionState.SetString(Witness, "");
            UnityEditor.SessionState.SetBool(Active, true);
            UnityEditor.AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
        }

        private static void BeforeReload()
        {
            if (UnityEditor.SessionState.GetBool(Active, false))
                UnityEditor.SessionState.SetString(Witness, UnityEditor.SessionState.GetString(BeforeDomain, ""));
        }

        private static void VerifyProbe()
        {
            Assert.IsTrue(UnityEditor.SessionState.GetBool(Active, false));
            Assert.AreEqual(UnityEditor.SessionState.GetString(BeforeDomain, ""), UnityEditor.SessionState.GetString(Witness, ""));
            Assert.AreNotEqual(UnityEditor.SessionState.GetString(BeforeDomain, ""), DomainNonce, "A fresh domain must execute this verification.");
            var fresh = Search();
            for (int attempt = 0; attempt < 3 && (string)fresh["code"] == "catalog_changed"; attempt++)
                fresh = Search();
            Assert.IsTrue((bool)fresh["success"], fresh.ToString());
            Assert.AreNotEqual(UnityEditor.SessionState.GetString(Generation, ""), (string)fresh["data"]["catalog_generation"]);
            var stale = Search(UnityEditor.SessionState.GetString(Cursor, ""));
            Assert.IsFalse((bool)stale["success"], stale.ToString());
            Assert.AreEqual("stale_cursor", (string)stale["code"]);
            TestContext.WriteLine(
                "DEV07_ACTUAL_RELOAD="
                    + new JObject
                    {
                        ["fresh_domain"] = true,
                        ["before_reload_witness"] = true,
                        ["stale_cursor"] = true,
                        ["fresh_page"] = fresh["data"],
                    }.ToString(Formatting.None)
            );
        }

        private static void CleanupProbe()
        {
            UnityEditor.AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            foreach (string key in new[] { Cursor, Generation, BeforeDomain, Witness })
                UnityEditor.SessionState.EraseString(key);
            UnityEditor.SessionState.EraseBool(Active);
        }
    }
}
