using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Resources
{
    [Parallelizable(ParallelScope.None)]
    public class GameObjectHandleResourceTests : ObjectHandleFixture
    {
        [Test]
        public void HandleResourceReadsExactGameObjectMetadata()
        {
            string handle = Token(Capture(Target));
            JObject response = Call("MCPForUnity.Editor.Resources.Scene.GameObjectHandleResource", new JObject { ["handle"] = handle });
            JObject data = Success(response);
            Assert.AreEqual(Target.GetInstanceIDCompat(), data.SelectToken("identity.instanceID").Value<int>());
            Assert.AreEqual(Target.GetInstanceIDCompat(), data.SelectToken("gameObject.instanceID").Value<int>());
            Assert.AreEqual("IdentityTarget", data.SelectToken("gameObject.name").Value<string>());
            Assert.IsFalse(OwnedScene.isDirty);
        }

        [Test]
        public void ComponentHandleCannotBeReadAsAGameObject()
        {
            string handle = Token(Capture(Second));
            Error(Call("MCPForUnity.Editor.Resources.Scene.GameObjectHandleResource", new JObject { ["handle"] = handle }), "object_kind_mismatch");
        }

        [TestCase(true, true)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(false, false)]
        public async Task ActualDispatcherRespectsInjectedResourceAndParentPolicy(bool parent, bool alias)
        {
            string handle = Token(Capture(Target));
            int id = Target.GetInstanceIDCompat();
            Resources.Enabled = parent;
            Resources.AliasEnabled = alias;
            CommandRegistry.Initialize();
            var operations = new List<string>();
            using (Observe(operations.Add))
            {
                var request = new JObject
                {
                    ["type"] = "get_gameobject_by_handle_v2",
                    ["params"] = new JObject { ["handle"] = handle },
                };
                JObject envelope = JObject.Parse(await TransportCommandDispatcher.ExecuteCommandJsonAsync(request.ToString(), CancellationToken.None));
                if (!alias)
                {
                    Assert.AreEqual("error", envelope.Value<string>("status"));
                    StringAssert.Contains("disabled", envelope.ToString());
                }
                else if (!parent)
                    Error((JObject)envelope["result"], "resource_disabled");
                else
                {
                    JObject data = Success((JObject)envelope["result"]);
                    Assert.AreEqual(id, data.SelectToken("gameObject.instanceID").Value<int>());
                    Assert.AreEqual(id, data.SelectToken("identity.instanceID").Value<int>());
                }
            }
            if (!parent || !alias)
                Assert.AreEqual(0, operations.Count);
            else
                Assert.IsFalse(operations.Contains("before:instance_lookup"), "Resolved reference must be passed directly into metadata serialization.");
        }

        [Test]
        public void DisabledParentRefusesBeforeParsingEvenWithAliasEnabled()
        {
            Resources.Enabled = false;
            int operations = 0;
            using (Observe(_ => operations++))
                Error(Call("MCPForUnity.Editor.Resources.Scene.GameObjectHandleResource", new JObject { ["handle"] = "invalid" }), "resource_disabled");
            Assert.AreEqual(0, operations);
        }

        [TestCase("before:resource_metadata")]
        [TestCase("after:resource_metadata")]
        public void MetadataReadRefusesDestroyedOriginalReference(string boundary)
        {
            string handle = Token(Capture(Target));
            using (
                Observe(operation =>
                {
                    if (operation == boundary)
                        UnityEngine.Object.DestroyImmediate(Target);
                })
            )
                Error(Call("MCPForUnity.Editor.Resources.Scene.GameObjectHandleResource", new JObject { ["handle"] = handle }), "stale_handle");
        }

        [TestCase("before:resource_metadata")]
        [TestCase("after:resource_metadata")]
        public void MetadataReadRefusesLifecycleEpochChange(string boundary)
        {
            string handle = Token(Capture(Target));
            using (
                Observe(operation =>
                {
                    if (operation == boundary)
                    {
                        System.Type helper = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Helpers.ObjectHandles");
                        helper
                            .GetMethod("OnPlayModeStateChanged", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)
                            .Invoke(null, new object[] { UnityEditor.PlayModeStateChange.ExitingEditMode });
                    }
                })
            )
                Error(Call("MCPForUnity.Editor.Resources.Scene.GameObjectHandleResource", new JObject { ["handle"] = handle }), "stale_handle");
        }

        [TestCase("before:resource_metadata")]
        [TestCase("after:resource_metadata")]
        public void MetadataReadPropagatesCancellation(string boundary)
        {
            string handle = Token(Capture(Target));
            System.Type handler = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Resources.Scene.GameObjectHandleResource");
            var method = handler.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            using var cancellation = new CancellationTokenSource();
            using (
                Observe(operation =>
                {
                    if (operation == boundary)
                        cancellation.Cancel();
                })
            )
            {
                var exception = Assert.Throws<System.Reflection.TargetInvocationException>(() =>
                    method.Invoke(
                        null,
                        new object[]
                        {
                            new JObject { ["handle"] = handle },
                            cancellation.Token,
                        }
                    )
                );
                Assert.IsInstanceOf<System.OperationCanceledException>(exception.InnerException);
            }
        }
    }
}
