using System;
using System.Collections;
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class NavigationBakeActualReloadTests
    {
        private const string Prefix = "MCPForUnity.Tests.NavigationBakeActualReload.";
        private static readonly string Nonce = Guid.NewGuid().ToString("N");

        private static object Call(string name, params object[] args) =>
            typeof(MCPForUnity.Editor.Tools.ManagePackages)
                .Assembly.GetType("MCPForUnity.Editor.Tools.Navigation.NavigationBakeCheckpoint")
                .GetMethod(name, BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, args);

        [
            UnityTest,
            Explicit("Owned disposable batch Editor only; exact UNITY_MCP_NAVIGATION_RELOAD_PROJECT required. Uses a controlled bake delegate, no native bake.")
        ]
        public IEnumerator DeclarativeReservationAcrossRepeatedActualReloadInvokesOnce()
        {
            Assert.IsTrue(Application.isBatchMode);
            Assert.AreEqual(
                Path.GetFullPath(Path.GetDirectoryName(Application.dataPath)),
                Environment.GetEnvironmentVariable("UNITY_MCP_NAVIGATION_RELOAD_PROJECT")
            );
            Assert.IsEmpty(SessionState.GetString(Prefix + "journal", ""), "Preserve unfinished probe metadata.");
            JObject job = new JObject
            {
                ["job_id"] = "owned-navigation-reload",
                ["state"] = "queued",
                ["native_invoked"] = false,
                ["deadline_utc"] = DateTime.UtcNow.AddMinutes(10).ToString("O"),
                ["checkpoint"] = new JObject
                {
                    ["version"] = 1,
                    ["target"] = "owned-stable-target",
                    ["scene_guid"] = "owned-scene",
                    ["scene_hash"] = "hash",
                    ["configuration_hash"] = "config",
                    ["backend"] = "controlled-delegate",
                },
            };
            SessionState.SetString(Prefix + "journal", new JArray(job).ToString());
            SessionState.SetInt(Prefix + "count", 0);
            try
            {
                // The runner restores the iterator position, but not its local loop counters.
                // Keep each domain boundary at a distinct top-level yield position.
                SessionState.SetString(Prefix + "nonce", Nonce);
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertQueuedAfterReload();

                SessionState.SetString(Prefix + "nonce", Nonce);
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertQueuedAfterReload();
                AdvanceQueuedJob();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                AssertCompletedWithoutReplay();
            }
            finally
            {
                SessionState.EraseString(Prefix + "journal");
                SessionState.EraseString(Prefix + "nonce");
                SessionState.EraseInt(Prefix + "count");
            }
        }

        private static void AssertQueuedAfterReload()
        {
            Assert.AreNotEqual(SessionState.GetString(Prefix + "nonce", ""), Nonce);
            JArray restored = (JArray)Call("Restore", SessionState.GetString(Prefix + "journal", ""));
            Assert.AreEqual("queued", (string)restored[0]["state"]);
            SessionState.SetString(Prefix + "journal", restored.ToString());
            Assert.AreEqual(0, SessionState.GetInt(Prefix + "count", 0));
        }

        private static void AdvanceQueuedJob()
        {
            // Recreate captured state in this domain; UTF restores no closure objects.
            var job = (JObject)((JArray)Call("Restore", SessionState.GetString(Prefix + "journal", "")))[0];
            Assert.IsTrue(
                (bool)Call(
                    "Advance",
                    job,
                    DateTime.UtcNow,
                    (Action)(() => { }),
                    (Action)(() => SessionState.SetInt(Prefix + "count", SessionState.GetInt(Prefix + "count", 0) + 1)),
                    (Func<string>)(() => null),
                    (Action)(() => SessionState.SetString(Prefix + "journal", new JArray(job).ToString()))
                )
            );
        }

        private static void AssertCompletedWithoutReplay()
        {
            var job = (JObject)((JArray)Call("Restore", SessionState.GetString(Prefix + "journal", "")))[0];
            Assert.AreEqual("owned-navigation-reload", (string)job["job_id"]);
            Assert.AreEqual("completed", (string)job["state"]);
            Assert.IsFalse(
                (bool)Call(
                    "Advance",
                    job,
                    DateTime.UtcNow,
                    (Action)(() => Assert.Fail("validate")),
                    (Action)(() => Assert.Fail("duplicate bake")),
                    (Func<string>)(() => null),
                    (Action)(() => { })
                )
            );
            Assert.AreEqual(1, SessionState.GetInt(Prefix + "count", 0));
        }
    }
}
