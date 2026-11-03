#if MCP_INPUT_UGUI
using System;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class SnapshotSelectableCallbackProbe : Selectable
    {
        public int Calls;

        public override bool IsInteractable()
        {
            Calls++;
            return base.IsInteractable();
        }
    }

    public class UguiSnapshotCallbackTests
    {
        [Test]
        public void CaptureInvokesDerivedSelectableCallback()
        {
            var root = new GameObject("SnapshotCallback_" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Canvas));
            try
            {
                var child = new GameObject("Selectable", typeof(RectTransform));
                child.transform.SetParent(root.transform, false);
                var probe = child.AddComponent<SnapshotSelectableCallbackProbe>();
                probe.Calls = 0;

                var result = JObject.FromObject(
                    CaptureUISnapshot.HandleCommand(new JObject { ["framework"] = "ugui", ["target"] = root.GetInstanceIDCompat() })
                );

                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.Greater(probe.Calls, 0, "Snapshot must expose the project callback boundary.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
            }
        }
    }
}
#endif
