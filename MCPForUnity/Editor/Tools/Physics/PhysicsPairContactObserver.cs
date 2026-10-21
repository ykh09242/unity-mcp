using System;
using System.Collections.Generic;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
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
#if UNITY_6000_0_OR_NEWER || UNITY_2022_3
using Unity.Collections;
#endif

namespace MCPForUnity.Editor.Tools.Physics
{
    // Installed only by explicit observe_contacts=true. No simulation or contact-generation settings are changed.
    internal static class PhysicsPairContactObserver
    {
        internal static JObject Read(Collider first, Collider second, bool? observe, bool sameScene, bool trigger)
        {
#if UNITY_6000_0_OR_NEWER || UNITY_2022_3
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
                    watches.Add(key, watch = new Watch { First = first, Second = second });
                }
                watch.Expires = EditorApplication.timeSinceStartup + LifetimeSeconds;
                Subscribe();
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
#else
            var result = PhysicsPairDiagnostics.Unavailable(
                "The passive ContactEvent backend is verified for Unity2022.3 and Unity6; unavailable on this editor version."
            );
            result["watching"] = false;
            result["observer_supported"] = false;
            return result;
#endif
        }

#if UNITY_6000_0_OR_NEWER || UNITY_2022_3
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

        private static JObject Missing(string reason, bool watching)
        {
            var result = PhysicsPairDiagnostics.Unavailable(reason);
            result["watching"] = watching;
            result["observer_supported"] = true;
            return result;
        }

        private static void Subscribe()
        {
            if (subscribed)
                return;
            Physics3D.ContactEvent += OnContactEvent;
            EditorApplication.update += Prune;
            EditorApplication.playModeStateChanged += OnPlayMode;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
            EditorSceneManager.sceneClosed += OnSceneClosed;
            subscribed = true;
        }

        private static void UnsubscribeIfEmpty()
        {
            if (!subscribed || watches.Count != 0)
                return;
            Physics3D.ContactEvent -= OnContactEvent;
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

        private static void OnContactEvent(PhysicsScene scene, NativeArray<ContactPairHeader>.ReadOnly headers)
        {
            int scanned = 0;
            long sequence = ++eventSequence;
            for (int i = 0; i < headers.Length; i++)
            {
                ContactPairHeader header = headers[i];
#if UNITY_6000_0_OR_NEWER
                int pairCount = header.pairCount;
#else
                int pairCount = header.PairCount;
#endif
                for (int j = 0; j < pairCount; j++)
                {
                    if (++scanned > MaximumScannedPairsPerEvent)
                        return;
                    ref readonly ContactPair pair = ref header.GetContactPair(j);
#if UNITY_6000_0_OR_NEWER
#if UNITY_6000_3_OR_NEWER
                    // Unity 6000.3 EntityId is an Int32 wrapper; the implicit conversion is lossless. 6000.4+ retains the full EntityId alias.
                    ColliderId first = pair.colliderEntityId;
                    ColliderId second = pair.otherColliderEntityId;
#else
                    int first = pair.colliderInstanceID;
                    int second = pair.otherColliderInstanceID;
#endif
                    int count = pair.contactCount;
                    bool exit = pair.isCollisionExit;
                    bool enter = pair.isCollisionEnter;
#else
                    int first = pair.ColliderInstanceID;
                    int second = pair.OtherColliderInstanceID;
                    int count = pair.ContactCount;
                    bool exit = pair.IsCollisionExit;
                    bool enter = pair.IsCollisionEnter;
#endif
                    if (watches.TryGetValue(Key(scene, first, second), out Watch watch) && EditorApplication.timeSinceStartup < watch.Expires)
                    {
                        watch.Evidence = new ContactEvidence
                        {
                            Count = count,
                            First = first,
                            Second = second,
                            Exit = exit,
                            Enter = enter,
                            EditorTime = EditorApplication.timeSinceStartup,
                            FixedTime = Time.fixedTimeAsDouble,
                            Sequence = sequence,
                        };
                    }
                }
            }
        }
#endif
    }
}
