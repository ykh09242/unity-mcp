#if MCP_FOR_UNITY_PHYSICS && MCP_FOR_UNITY_PHYSICS2D
using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using Unity.Collections;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Physics3D = UnityEngine.Physics;
#if UNITY_6000_4_OR_NEWER
using ColliderId = UnityEngine.EntityId;
#else
using ColliderId = System.Int32;
#endif
#if UNITY_6000_3_OR_NEWER
using ContactId = UnityEngine.EntityId;
#else
using ContactId = System.Int32;
#endif

namespace MCPForUnity.Editor.Tools.Physics
{
    // Installed only by explicit observe_contacts=true. No simulation or contact-generation settings are changed.
    internal static class PhysicsPairContactObserver
    {
        internal static JObject Read(Collider first, Collider second, bool? observe, bool sameScene, bool trigger)
        {
            if (contactBinding.Handler == null)
                return Missing(contactBinding.Reason, false, false);
            Prune();
            var key = Key(first.gameObject.scene.GetPhysicsScene(), Identity(first), Identity(second));
            if (observe == false || !sameScene || trigger)
            {
                watches.Remove(key);
                UnsubscribeIfEmpty();
            }
            if (!sameScene || trigger)
                return Missing(!sameScene ? "different_physics_scenes" : "Physics.ContactEvent does not report triggers.", false);
            if (observe == true)
            {
                if (!watches.TryGetValue(key, out Watch watch))
                {
                    if (watches.Count >= MaximumPairs)
                        return Missing("observer_capacity_reached", false);
                    if (!TrySubscribe(out string reason))
                        return Missing(reason, false, false);
                    watches.Add(key, watch = new Watch { First = first, Second = second });
                }
                watch.Expires = EditorApplication.timeSinceStartup + LifetimeSeconds;
            }
            if (!watches.TryGetValue(key, out Watch active))
                return Missing("No active opt-in observation. This is not evidence of zero contacts.", false);
            var result = !active.Evidence.HasValue
                ? Missing("No collision event observed for this pair since opt-in; contact generation may be disabled.", true)
                : Describe(active.Evidence.Value);
            result["watching"] = true;
            result["expires_in_seconds"] = Math.Max(0, active.Expires - EditorApplication.timeSinceStartup);
            result["maximum_watched_pairs"] = MaximumPairs;
            result["current_state_proven"] = false;
            result["observer_supported"] = true;
            result["contact_generation_requirement"] =
                "Collider.providesContacts or an existing OnCollisionStay receiver is required; this tool enables neither.";
            return result;
        }

        private const int MaximumPairs = 32;
        private const int MaximumScannedPairsPerEvent = 4096;
        private const double LifetimeSeconds = 60;

        private sealed class Watch
        {
            internal Collider First;
            internal Collider Second;
            internal double Expires;
            internal ContactEvidence? Evidence;
        }

        private static readonly Dictionary<(PhysicsScene, ColliderId, ColliderId), Watch> watches =
            new Dictionary<(PhysicsScene, ColliderId, ColliderId), Watch>();

        private struct ContactEvidence
        {
            internal int Count;
            internal ColliderId First;
            internal ColliderId Second;
            internal bool Exit;
            internal bool Enter;
            internal double EditorTime;
            internal double FixedTime;
            internal long Sequence;
        }

        private static readonly List<(PhysicsScene, ColliderId, ColliderId)> expired = new(MaximumPairs);
        private static bool subscribed;
        private static long eventSequence;
        private static readonly EventInfo contactEvent = typeof(Physics3D).GetEvent("ContactEvent", BindingFlags.Public | BindingFlags.Static);
        private static readonly (Delegate Handler, string Reason) contactBinding = BindContactEvent(
            contactEvent,
            typeof(Physics3D).Assembly.GetType("UnityEngine.ContactPairHeader"),
            typeof(Physics3D).Assembly.GetType("UnityEngine.ContactPair")
        );

        private static (PhysicsScene, ColliderId, ColliderId) Key(PhysicsScene scene, ColliderId first, ColliderId second) =>
            first.CompareTo(second) < 0 ? (scene, first, second) : (scene, second, first);

        private static ColliderId Identity(Collider collider)
        {
#if UNITY_6000_4_OR_NEWER
            return collider.GetEntityId();
#else
            return collider.GetInstanceIDCompat();
#endif
        }

        private static JValue NativeId(ColliderId id)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(EntityId.ToULong(id).ToString(System.Globalization.CultureInfo.InvariantCulture));
#else
            return new JValue(id);
#endif
        }

        private static JObject Missing(string reason, bool watching, bool supported = true)
        {
            var result = PhysicsPairDiagnostics.Unavailable(reason);
            result["watching"] = watching;
            result["observer_supported"] = supported;
            return result;
        }

        private static bool TrySubscribe(out string reason)
        {
            reason = null;
            if (subscribed)
                return true;
            try
            {
                contactEvent.AddEventHandler(null, contactBinding.Handler);
            }
            catch (Exception e)
            {
                reason = "Could not subscribe to Physics.ContactEvent: " + e.GetBaseException().Message;
                return false;
            }
            EditorApplication.update += Prune;
            EditorApplication.playModeStateChanged += OnPlayMode;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
            EditorSceneManager.sceneClosed += OnSceneClosed;
            subscribed = true;
            return true;
        }

        private static void UnsubscribeIfEmpty()
        {
            if (!subscribed || watches.Count != 0)
                return;
            contactEvent.RemoveEventHandler(null, contactBinding.Handler);
            EditorApplication.update -= Prune;
            EditorApplication.playModeStateChanged -= OnPlayMode;
            AssemblyReloadEvents.beforeAssemblyReload -= Clear;
            EditorApplication.quitting -= Clear;
            EditorSceneManager.sceneClosed -= OnSceneClosed;
            subscribed = false;
        }

        private static void OnPlayMode(PlayModeStateChange state) => Clear();

        private static void OnSceneClosed(Scene scene) => Clear();

        private static void Clear()
        {
            watches.Clear();
            UnsubscribeIfEmpty();
        }

        private static void Prune()
        {
            double now = EditorApplication.timeSinceStartup;
            try
            {
                foreach (var row in watches)
                    if (
                        row.Value.Expires <= now
                        || row.Value.First == null
                        || row.Value.Second == null
                        || row.Value.First.isTrigger
                        || row.Value.Second.isTrigger
                        || row.Value.First.gameObject.scene.GetPhysicsScene() != row.Key.Item1
                        || row.Value.Second.gameObject.scene.GetPhysicsScene() != row.Key.Item1
                    )
                        expired.Add(row.Key);
                foreach (var key in expired)
                    watches.Remove(key);
            }
            finally
            {
                expired.Clear();
            }
            UnsubscribeIfEmpty();
        }

        private static JObject Describe(ContactEvidence evidence) =>
            new JObject
            {
                ["status"] = "available",
                ["basis"] = "last_observed_collision_event",
                ["event_type"] =
                    evidence.Exit ? "exit"
                    : evidence.Enter ? "enter"
                    : "stay",
                ["contact_count"] = evidence.Count,
                ["native_first_collider_id"] = NativeId(evidence.First),
                ["native_second_collider_id"] = NativeId(evidence.Second),
#if UNITY_6000_4_OR_NEWER
                ["native_collider_id_kind"] = "entity_id_decimal_string",
#else
                ["native_collider_id_kind"] = "instance_id",
#endif
                ["observed_editor_time_seconds"] = evidence.EditorTime,
                ["engine_fixed_time_at_event"] = evidence.FixedTime,
                ["observed_event_sequence"] = evidence.Sequence,
                ["sequence_is_simulation_step"] = false,
                ["current_state_proven"] = false,
                ["scan_limit_per_event"] = MaximumScannedPairsPerEvent,
            };

        internal static (Delegate Handler, string Reason) BindContactEvent(EventInfo source, Type header, Type pair)
        {
            if (source?.GetAddMethod()?.IsStatic != true || source.GetRemoveMethod()?.IsStatic != true)
                return (null, "Physics.ContactEvent does not expose public static subscription and removal.");
            if (header?.IsValueType != true || pair?.IsValueType != true)
                return (null, "Physics.ContactEvent does not expose the required contact header and pair types.");
            try
            {
                var reader = typeof(ContactReader<,>).MakeGenericType(header, pair);
                var handler = reader.GetMethod(nameof(ContactReader<int, int>.OnContactEvent));
                var invoke = source.EventHandlerType.GetMethod("Invoke");
                var parameters = invoke.GetParameters();
                if (
                    invoke.ReturnType != typeof(void)
                    || parameters.Length != 2
                    || parameters[0].ParameterType != typeof(PhysicsScene)
                    || parameters[1].ParameterType != handler.GetParameters()[1].ParameterType
                )
                    return (null, "Physics.ContactEvent has an incompatible scene or read-only header array signature.");
                var getPair = header.GetMethod("GetContactPair", BindingFlags.Instance | BindingFlags.Public, null, new[] { typeof(int) }, null);
                if (getPair?.ReturnType != pair.MakeByRefType())
                    return (null, "The contact header does not expose GetContactPair(int) returning the native pair by reference.");
                return (Delegate.CreateDelegate(source.EventHandlerType, Activator.CreateInstance(reader, getPair), handler), null);
            }
            catch (Exception e)
            {
                return (null, "Could not bind the Physics.ContactEvent reader: " + e.GetBaseException().Message);
            }
        }

        // Bind once. The native arrays and ref-returned pairs are valid only during this callback.
        private sealed class ContactReader<THeader, TPair>
            where THeader : unmanaged
            where TPair : unmanaged
        {
            private delegate TValue Getter<TStruct, TValue>(ref TStruct value);
            private delegate ref readonly TPair GetPair(ref THeader header, int index);

            private readonly GetPair getPair;
            private readonly Getter<THeader, int> pairCount = BindGetter<THeader, int>("pairCount", "PairCount");
            private readonly Getter<TPair, int> contactCount = BindGetter<TPair, int>("contactCount", "ContactCount");
            private readonly Getter<TPair, bool> isExit = BindGetter<TPair, bool>("isCollisionExit", "IsCollisionExit");
            private readonly Getter<TPair, bool> isEnter = BindGetter<TPair, bool>("isCollisionEnter", "IsCollisionEnter");
#if UNITY_6000_3_OR_NEWER
            private readonly Getter<TPair, ContactId> firstId = BindGetter<TPair, ContactId>("colliderEntityId");
            private readonly Getter<TPair, ContactId> secondId = BindGetter<TPair, ContactId>("otherColliderEntityId");
#else
            private readonly Getter<TPair, ContactId> firstId = BindGetter<TPair, ContactId>("colliderInstanceID", "ColliderInstanceID");
            private readonly Getter<TPair, ContactId> secondId = BindGetter<TPair, ContactId>("otherColliderInstanceID", "OtherColliderInstanceID");
#endif

            public ContactReader(MethodInfo getPairMethod) => getPair = (GetPair)getPairMethod.CreateDelegate(typeof(GetPair));

            private static Getter<TStruct, TValue> BindGetter<TStruct, TValue>(params string[] names)
            {
                foreach (string name in names)
                {
                    var getter = typeof(TStruct).GetProperty(name, BindingFlags.Public | BindingFlags.Instance)?.GetGetMethod();
                    if (getter?.ReturnType == typeof(TValue) && getter.GetParameters().Length == 0)
                        return (Getter<TStruct, TValue>)getter.CreateDelegate(typeof(Getter<TStruct, TValue>));
                }
                throw new MissingMemberException(typeof(TStruct).FullName, string.Join("/", names));
            }

            public void OnContactEvent(PhysicsScene scene, NativeArray<THeader>.ReadOnly headers)
            {
                int scanned = 0;
                long sequence = ++eventSequence;
                for (int i = 0; i < headers.Length; i++)
                {
                    THeader header = headers[i];
                    int pairs = pairCount(ref header);
                    for (int j = 0; j < pairs; j++)
                    {
                        if (++scanned > MaximumScannedPairsPerEvent)
                            return;
                        TPair pair = getPair(ref header, j);
                        ColliderId first = firstId(ref pair);
                        ColliderId second = secondId(ref pair);
                        if (watches.TryGetValue(Key(scene, first, second), out Watch watch) && EditorApplication.timeSinceStartup < watch.Expires)
                        {
                            watch.Evidence = new ContactEvidence
                            {
                                Count = contactCount(ref pair),
                                First = first,
                                Second = second,
                                Exit = isExit(ref pair),
                                Enter = isEnter(ref pair),
                                EditorTime = EditorApplication.timeSinceStartup,
                                FixedTime = Time.fixedTimeAsDouble,
                                Sequence = sequence,
                            };
                        }
                    }
                }
            }
        }
    }
}

#endif
