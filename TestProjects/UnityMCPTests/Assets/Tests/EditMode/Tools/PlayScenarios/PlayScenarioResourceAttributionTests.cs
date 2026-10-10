using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using MCPForUnity.Runtime;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools.PlayScenarios
{
    public class PlayScenarioResourceAttributionTests
    {
        private static readonly MethodInfo Reset = typeof(PlayScenarioResourceTracker).GetMethod(
            "ResetOnPlayEntry",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private readonly List<ScriptableObject> _objects = new List<ScriptableObject>();
        private readonly List<IDisposable> _tokens = new List<IDisposable>();

        [SetUp]
        public void SetUp() => Reset.Invoke(null, null);

        [TearDown]
        public void TearDown()
        {
            foreach (IDisposable token in _tokens)
                token.Dispose();
            foreach (ScriptableObject resource in _objects)
                if (resource != null)
                    UnityEngine.Object.DestroyImmediate(resource);
            _tokens.Clear();
            _objects.Clear();
            Reset.Invoke(null, null);
        }

        [Test]
        public void LegacyCallsCaptureCompilerCallsiteAndMatchingResourceKinds()
        {
            var clone = NewClone("legacy clone");
            PlayScenarioResourceTracker.RegisterScriptableObject(clone);
            _tokens.Add(PlayScenarioResourceTracker.RegisterSubscription());
            _tokens.Add(PlayScenarioResourceTracker.RegisterHandle());

            PlayScenarioRegisteredResources snapshot = PlayScenarioResourceTracker.Capture();

            Assert.That(snapshot.ResourceDetails.Select(info => info.Kind), Is.EquivalentTo(new[] { "scriptable_object", "subscription", "handle" }));
            foreach (PlayScenarioRegisteredResourceInfo info in snapshot.ResourceDetails)
            {
                Assert.That(info.SourceFile, Is.EqualTo("PlayScenarioResourceAttributionTests.cs"));
                Assert.That(info.SourceMember, Is.EqualTo(nameof(LegacyCallsCaptureCompilerCallsiteAndMatchingResourceKinds)));
                Assert.That(info.SourceLine, Is.GreaterThan(0));
                Assert.That(info.Owner, Is.Null);
            }
            Assert.That(
                snapshot.ResourceDetails.Select(info => info.Id),
                Is.EquivalentTo(snapshot.ScriptableObjectIds.Concat(snapshot.SubscriptionIds).Concat(snapshot.HandleIds))
            );
        }

        [Test]
        public void ExplicitCallerMetadataStripsPathsAndSanitizesControls()
        {
            _tokens.Add(
                PlayScenarioResourceTracker.RegisterHandle(
                    " owner\r\n\t\u202e label ",
                    "C:\\private\\directory\\Resource.cs",
                    " Acquire\n\u2028\u2029Handle ",
                    -12
                )
            );
            _tokens.Add(PlayScenarioResourceTracker.RegisterSubscription(sourceFile: "/private/directory/Event.cs", sourceMember: "Listen", sourceLine: 23));

            PlayScenarioRegisteredResourceInfo[] details = PlayScenarioResourceTracker.Capture().ResourceDetails;

            PlayScenarioRegisteredResourceInfo handle = details.Single(info => info.Kind == "handle");
            Assert.That(handle.Owner, Is.EqualTo("owner     label"));
            Assert.That(handle.SourceFile, Is.EqualTo("Resource.cs"));
            Assert.That(handle.SourceMember, Is.EqualTo("Acquire   Handle"));
            Assert.That(handle.SourceLine, Is.Zero);
            Assert.That(details.Single(info => info.Kind == "subscription").SourceFile, Is.EqualTo("Event.cs"));
            Assert.That(details.Single(info => info.Kind == "subscription").SourceLine, Is.EqualTo(23));
        }

        [Test]
        public void MetadataBoundsApplyBeforeStorageWithoutSplittingSurrogates()
        {
            string longLabel = new string('a', 127) + "\ud83d\ude00" + new string('z', 300);

            var info = new PlayScenarioRegisteredResourceInfo(
                1,
                "scriptable_object",
                longLabel,
                new string('t', 300),
                new string('n', 200),
                "/private/" + new string('f', 200),
                new string('m', 200)
            );

            Assert.That(info.Owner, Is.EqualTo(new string('a', 127)));
            Assert.That(info.TypeName.Length, Is.EqualTo(256));
            Assert.That(info.ResourceName.Length, Is.EqualTo(128));
            Assert.That(info.SourceFile.Length, Is.EqualTo(128));
            Assert.That(info.SourceMember.Length, Is.EqualTo(128));
            Assert.That(info.SourceFile, Does.Not.Contain("/"));
        }

        [Test]
        public void ScriptableObjectDescriptionsRemainAtTheirFirstRegistrationValues()
        {
            var clone = NewClone("initial name");
            PlayScenarioResourceTracker.RegisterScriptableObject(
                clone,
                "first owner",
                sourceFile: "/original/Clone.cs",
                sourceMember: "Create",
                sourceLine: 17
            );
            PlayScenarioRegisteredResourceInfo initial = PlayScenarioResourceTracker.Capture().ResourceDetails.Single();
            clone.name = "renamed";

            PlayScenarioResourceTracker.RegisterScriptableObject(clone, "second owner", sourceFile: "/later/Other.cs", sourceMember: "Reuse", sourceLine: 99);

            PlayScenarioRegisteredResourceInfo current = PlayScenarioResourceTracker.Capture().ResourceDetails.Single();
            Assert.That(current.Id, Is.EqualTo(initial.Id));
            Assert.That(current.Owner, Is.EqualTo("first owner"));
            Assert.That(current.TypeName, Is.EqualTo(typeof(PlayScenarioAttributionClone).FullName));
            Assert.That(current.ResourceName, Is.EqualTo("initial name"));
            Assert.That(current.SourceFile, Is.EqualTo("Clone.cs"));
            Assert.That(current.SourceMember, Is.EqualTo("Create"));
            Assert.That(current.SourceLine, Is.EqualTo(17));
        }

        [Test]
        public void DestroyedAndDisposedRegistrationsPruneTheirMetadataTogether()
        {
            var clone = NewClone("to destroy");
            PlayScenarioResourceTracker.RegisterScriptableObject(clone, "clone owner");
            IDisposable handle = PlayScenarioResourceTracker.RegisterHandle("handle owner");
            IDisposable subscription = PlayScenarioResourceTracker.RegisterSubscription("kept owner");
            _tokens.Add(handle);
            _tokens.Add(subscription);
            PlayScenarioRegisteredResources previous = PlayScenarioResourceTracker.Capture();
            UnityEngine.Object.DestroyImmediate(clone);
            handle.Dispose();

            PlayScenarioRegisteredResources current = PlayScenarioResourceTracker.Capture();

            Assert.That(current.ScriptableObjectIds, Is.Empty);
            Assert.That(current.HandleIds, Is.Empty);
            Assert.That(current.ResourceDetails.Single().Kind, Is.EqualTo("subscription"));
            Assert.That(current.ResourceDetails.Single().Owner, Is.EqualTo("kept owner"));
            Assert.That(current.ResourceDetails.Single().Id, Is.EqualTo(current.SubscriptionIds.Single()));
            Assert.That(previous.ResourceDetails.Length, Is.EqualTo(3));
            Assert.That(previous.ResourceDetails.Single(info => info.Kind == "scriptable_object").ResourceName, Is.EqualTo("to destroy"));
        }

        [Test]
        public void SnapshotArrayMutationCannotRewriteStoredAttribution()
        {
            _tokens.Add(PlayScenarioResourceTracker.RegisterHandle("registered owner"));
            PlayScenarioRegisteredResources snapshot = PlayScenarioResourceTracker.Capture();
            PlayScenarioRegisteredResourceInfo[] firstRead = snapshot.ResourceDetails;
            firstRead[0] = new PlayScenarioRegisteredResourceInfo(-1, "handle", "rewritten");
            var provided = new[] { new PlayScenarioRegisteredResourceInfo(10, "handle", "provided owner") };
            var constructed = new PlayScenarioRegisteredResources { ResourceDetails = provided };

            provided[0] = null;

            Assert.That(snapshot.ResourceDetails.Single().Owner, Is.EqualTo("registered owner"));
            Assert.That(PlayScenarioResourceTracker.Capture().ResourceDetails.Single().Owner, Is.EqualTo("registered owner"));
            Assert.That(constructed.ResourceDetails.Single().Owner, Is.EqualTo("provided owner"));
            Assert.That(typeof(PlayScenarioRegisteredResourceInfo).GetProperties().All(property => !property.CanWrite), Is.True);
        }

        [Test]
        public void SnapshotMetadataArrayCannotExceedRegistrationCapacity()
        {
            var provided = new PlayScenarioRegisteredResourceInfo[PlayScenarioResourceTracker.RegistrationLimit + 1];
            for (int index = 0; index < provided.Length; index++)
                provided[index] = new PlayScenarioRegisteredResourceInfo(index + 1, "handle");

            var snapshot = new PlayScenarioRegisteredResources { ResourceDetails = provided };

            Assert.That(snapshot.ResourceDetails.Length, Is.EqualTo(PlayScenarioResourceTracker.RegistrationLimit));
            snapshot.ResourceDetails = null;
            Assert.That(snapshot.ResourceDetails, Is.Empty);
        }

        [Test]
        public void MetadataDoesNotRetainRegistrationTokensOrResourceWrappers()
        {
            WeakReference tokenReference = RegisterUnretainedHandle();
            var clone = NewClone("weak clone");
            PlayScenarioResourceTracker.RegisterScriptableObject(clone, "weak owner");
            var registrations = (IDictionary)
                typeof(PlayScenarioResourceTracker).GetField("ScriptableObjects", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            object registration = registrations.Values.Cast<object>().Single();
            WeakReference resourceReference = (WeakReference)
                registration.GetType().GetField("Reference", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(registration);
            resourceReference.Target = null;
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();

            PlayScenarioRegisteredResources snapshot = PlayScenarioResourceTracker.Capture();

            Assert.That(tokenReference.IsAlive, Is.False);
            Assert.That(snapshot.HandleCount, Is.EqualTo(1));
            Assert.That(snapshot.ScriptableObjectCount, Is.EqualTo(1), "Collected wrappers must not hide live native resources.");
            Assert.That(snapshot.ResourceDetails.Single(info => info.Kind == "scriptable_object").ResourceName, Is.EqualTo("weak clone"));
            Assert.That(
                typeof(PlayScenarioRegisteredResourceInfo)
                    .GetProperties()
                    .All(property => property.PropertyType == typeof(string) || property.PropertyType == typeof(long) || property.PropertyType == typeof(int)),
                Is.True
            );
        }

        [Test]
        public void CapacityFailureKeepsBoundedMetadataAndStickyFailureAfterRelease()
        {
            for (int index = 0; index < PlayScenarioResourceTracker.RegistrationLimit; index++)
                _tokens.Add(PlayScenarioResourceTracker.RegisterHandle("owner " + index));
            Assert.Throws<InvalidOperationException>(() => PlayScenarioResourceTracker.RegisterSubscription("overflow"));
            PlayScenarioRegisteredResources full = PlayScenarioResourceTracker.Capture();
            _tokens[0].Dispose();
            _tokens.Add(PlayScenarioResourceTracker.RegisterSubscription("replacement"));

            PlayScenarioRegisteredResources current = PlayScenarioResourceTracker.Capture();

            Assert.That(full.ResourceDetails.Length, Is.EqualTo(PlayScenarioResourceTracker.RegistrationLimit));
            Assert.That(current.ResourceDetails.Length, Is.EqualTo(PlayScenarioResourceTracker.RegistrationLimit));
            Assert.That(current.ResourceDetails.Any(info => info.Owner == "overflow"), Is.False);
            Assert.That(current.ResourceDetails.Any(info => info.Id == full.ResourceDetails.Single(detail => detail.Owner == "owner 0").Id), Is.False);
            Assert.That(current.ResourceDetails.Single(info => info.Kind == "subscription").Owner, Is.EqualTo("replacement"));
            Assert.That(current.RegistrationFailureCount, Is.EqualTo(1));
        }

        [Test]
        public void ReclaimingDeadClonesAtCapacityAlsoReclaimsTheirDescriptions()
        {
            for (int index = 0; index < PlayScenarioResourceTracker.RegistrationLimit; index++)
            {
                var clone = NewClone("dead clone " + index);
                PlayScenarioResourceTracker.RegisterScriptableObject(clone, "dead owner");
                UnityEngine.Object.DestroyImmediate(clone);
            }

            _tokens.Add(PlayScenarioResourceTracker.RegisterHandle("live owner"));

            PlayScenarioRegisteredResources snapshot = PlayScenarioResourceTracker.Capture();
            Assert.That(snapshot.ResourceDetails.Single().Owner, Is.EqualTo("live owner"));
            Assert.That(snapshot.ScriptableObjectIds, Is.Empty);
            Assert.That(snapshot.RegistrationFailureCount, Is.Zero);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static WeakReference RegisterUnretainedHandle() => new WeakReference(PlayScenarioResourceTracker.RegisterHandle("unretained token"));

        private PlayScenarioAttributionClone NewClone(string resourceName)
        {
            var clone = ScriptableObject.CreateInstance<PlayScenarioAttributionClone>();
            clone.name = resourceName;
            _objects.Add(clone);
            return clone;
        }
    }

    public sealed class PlayScenarioAttributionClone : ScriptableObject { }
}
