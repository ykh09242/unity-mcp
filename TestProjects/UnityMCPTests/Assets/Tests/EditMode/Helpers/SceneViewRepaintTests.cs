using System;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Capabilities;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    public class SceneViewRepaintTests
    {
        private SceneView view;
        private object observation;
        private double now;
        private Type type;
        private const BindingFlags Hidden = BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Instance;

        [SetUp]
        public void SetUp()
        {
            now = 100;
            view = ScriptableObject.CreateInstance<SceneView>();
            type = typeof(ManageScene).Assembly.GetType("MCPForUnity.Editor.Helpers.SceneViewRepaintWait");
        }

        [TearDown]
        public void TearDown()
        {
            (observation as IDisposable)?.Dispose();
            if (view != null)
                UnityEngine.Object.DestroyImmediate(view);
        }

        private Task Begin(double seconds = 300, CancellationToken token = default)
        {
            Assert.IsNotNull(type, "A real repaint must be observed instead of sleeping on the Editor thread.");
            observation = Activator.CreateInstance(type, Hidden, null, new object[] { view, seconds, token, (Func<double>)(() => now) }, null);
            return (Task)type.GetProperty("Task", Hidden).GetValue(observation);
        }

        private void Invoke(string name) => type.GetMethod(name, Hidden).Invoke(observation, null);

        private void Repaint(SceneView target, EventType eventType) =>
            type.GetMethod("ObserveEvent", Hidden).Invoke(observation, new object[] { target, eventType });

        private int UpdateOwners()
        {
            if (observation == null)
                return 0;
            int count = 0;
            foreach (var callback in EditorApplication.update?.GetInvocationList() ?? Array.Empty<Delegate>())
                if (ReferenceEquals(callback.Target, observation))
                    count++;
            return count;
        }

        [Test]
        public void LegacyCameraCapture_PreservesImmediateValidationResponse()
        {
            object result = MCPForUnity.Editor.Tools.Cameras.ManageCamera.HandleCommand(
                new JObject { ["action"] = "screenshot", ["capture_source"] = "invalid-source" }
            );
            Assert.IsInstanceOf<MCPForUnity.Editor.Helpers.ErrorResponse>(result);
        }

        [Test]
        public void MatchingRepaint_CompletesOnlyAfterTheDrawingCallbackReturns()
        {
            Task task = Begin();
            Assert.AreEqual(1, UpdateOwners());
            now += 90;
            Invoke("Tick");
            Assert.IsFalse(task.IsCompleted, "A long delay alone cannot prove a rendered frame.");
            using var unrelated = new OwnedView();
            Repaint(unrelated.View, EventType.Repaint);
            Invoke("AfterRepaint");
            Assert.IsFalse(task.IsCompleted);
            Repaint(view, EventType.Layout);
            Invoke("AfterRepaint");
            Assert.IsFalse(task.IsCompleted);
            Repaint(view, EventType.Repaint);
            Assert.IsFalse(task.IsCompleted, "The OnGUI callback is still drawing this frame.");
            Invoke("AfterRepaint");
            Assert.AreEqual(TaskStatus.RanToCompletion, task.Status);
            Assert.AreEqual(0, UpdateOwners());
        }

        [Test]
        public void MatchingRepaint_CompletesOnTheNextUpdateEvenWhenInspectorsDeferDelayCalls()
        {
            Task task = Begin();
            Repaint(view, EventType.Repaint);
            Assert.IsFalse(task.IsCompleted);
            Invoke("Tick");
            Assert.AreEqual(TaskStatus.RanToCompletion, task.Status);
            Assert.AreEqual(0, UpdateOwners());
            Invoke("AfterRepaint");
            Assert.AreEqual(TaskStatus.RanToCompletion, task.Status);
        }

        private sealed class OwnedView : IDisposable
        {
            internal SceneView View = ScriptableObject.CreateInstance<SceneView>();

            public void Dispose() => UnityEngine.Object.DestroyImmediate(View);
        }

        [TestCase(300)]
        [TestCase(86400)]
        public void DeadlineIsFinite_WithoutAnEarlyMachineSpeedCap(double budget)
        {
            Task task = Begin(budget);
            now += budget - 0.01;
            Invoke("Tick");
            Assert.IsFalse(task.IsCompleted);
            now += 0.01;
            Invoke("Tick");
            Assert.IsTrue(task.IsCompleted, "The observation must settle synchronously at its terminal boundary.");
            Assert.Throws<TimeoutException>(() => task.GetAwaiter().GetResult());
            Repaint(view, EventType.Repaint);
            Invoke("AfterRepaint");
            Assert.IsTrue(task.IsFaulted);
            Assert.AreEqual(0, UpdateOwners());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancellationBeforeOrAfterRepaint_CannotFinishAQueuedCapture(bool afterRepaint)
        {
            using var cts = new CancellationTokenSource();
            Task task = Begin(token: cts.Token);
            if (afterRepaint)
                Repaint(view, EventType.Repaint);
            cts.Cancel();
            Invoke(afterRepaint ? "AfterRepaint" : "Tick");
            Assert.IsTrue(task.IsCanceled);
            Assert.AreEqual(0, UpdateOwners());
        }

        [Test]
        public void LateRepaintAfterDeadline_CannotPublishSuccess()
        {
            Task task = Begin();
            Repaint(view, EventType.Repaint);
            now += 301;
            Invoke("AfterRepaint");
            Assert.IsTrue(task.IsCompleted, "The observation must settle synchronously at its terminal boundary.");
            Assert.Throws<TimeoutException>(() => task.GetAwaiter().GetResult());
            Assert.AreEqual(0, UpdateOwners());
        }

        [Test]
        public void ClosedWindow_ReleasesObservation()
        {
            Task task = Begin();
            UnityEngine.Object.DestroyImmediate(view);
            Invoke("Tick");
            Assert.IsTrue(task.IsCompleted, "Closing the observed window must settle the wait.");
            Assert.Throws<InvalidOperationException>(() => task.GetAwaiter().GetResult());
            Assert.AreEqual(0, UpdateOwners());
        }

        [Test]
        public void ReloadOrExit_CancelsAndDetachesQueuedCallbacks()
        {
            Task task = Begin();
            Repaint(view, EventType.Repaint);
            ((IDisposable)observation).Dispose();
            Invoke("AfterRepaint");
            Assert.IsTrue(task.IsCanceled);
            Assert.AreEqual(0, UpdateOwners());
        }

        [TestCase(0)]
        [TestCase(-1)]
        [TestCase(double.NaN)]
        [TestCase(double.PositiveInfinity)]
        [TestCase(86401)]
        public void InvalidBudget_IsRejectedBeforeAttaching(double seconds)
        {
            Assert.IsNotNull(type);
            var error = Assert.Throws<TargetInvocationException>(() => Begin(seconds));
            Assert.IsInstanceOf<ArgumentOutOfRangeException>(error.InnerException);
            Assert.AreEqual(0, UpdateOwners());
        }

        [Test]
        public void CapabilityCarriesCancellationBeforeAnySceneOrFileSideEffect()
        {
            var method = typeof(CaptureCamera).GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            Assert.IsNotNull(method);
            using var cts = new CancellationTokenSource();
            cts.Cancel();
            Assert.Catch<OperationCanceledException>(() =>
            {
                try
                {
                    var task =
                        (Task<object>)
                            method.Invoke(
                                null,
                                new object[]
                                {
                                    new JObject { ["action"] = "screenshot", ["capture_source"] = "scene_view" },
                                    cts.Token,
                                }
                            );
                    Assert.IsTrue(task.IsCompleted, "Already canceled admission must settle immediately.");
                    task.GetAwaiter().GetResult();
                }
                catch (TargetInvocationException e)
                {
                    throw e.InnerException;
                }
            });
        }
    }
}
