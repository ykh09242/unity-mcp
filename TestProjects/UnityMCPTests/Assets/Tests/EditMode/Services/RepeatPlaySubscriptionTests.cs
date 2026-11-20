using System;
using System.Collections;
using Newtonsoft.Json;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class RepeatPlaySubscriptionTests
    {
        private const string Snapshot = "MCP.RepeatPlaySubscriptionTests.OriginalOptions";
        private static readonly string Domain = Guid.NewGuid().ToString("N");
        private static event Action CoinCollected;
        private static bool unsubscribeOnStop;
        private static int balance;
        private static int callbacks;

        private static void CreditCoin()
        {
            callbacks++;
            balance += 10;
        }

        private static void OnTransition(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.EnteredPlayMode)
            {
                balance = 0;
                callbacks = 0;
                CoinCollected += CreditCoin;
            }
            if (unsubscribeOnStop && state == PlayModeStateChange.ExitingPlayMode)
                CoinCollected -= CreditCoin;
        }

        [UnityTest]
        public IEnumerator CollectingOnceAcrossTwoPlaySessionsExposesAndRepairsDuplicateCredit()
        {
            string domain = Domain;
            SessionState.SetInt(Snapshot, (int)EditorSettings.enterPlayModeOptions);
            SessionState.SetBool(Snapshot + ".Enabled", EditorSettings.enterPlayModeOptionsEnabled);
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorApplication.playModeStateChanged += OnTransition;
            foreach (bool repaired in new[] { false, true })
            {
                CoinCollected = null;
                unsubscribeOnStop = repaired;
                for (int session = 1; session <= 2; session++)
                {
                    yield return new EnterPlayMode(false);
                    Assert.IsTrue(Application.isPlaying);
                    Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
                    Assert.AreEqual(EnterPlayModeOptions.DisableDomainReload, EditorSettings.enterPlayModeOptions);
                    Assert.AreEqual(domain, Domain, "Both entries must retain the actual scripting domain.");
                    Assert.AreEqual(0, balance);
                    int listeners = CoinCollected?.GetInvocationList().Length ?? 0;
                    CoinCollected?.Invoke();
                    TestContext.WriteLine(
                        JsonConvert.SerializeObject(
                            new
                            {
                                domain,
                                session,
                                repaired,
                                listeners,
                                callbacks,
                                balance,
                            }
                        )
                    );
                    int expected = repaired ? 1 : session;
                    Assert.AreEqual(expected, callbacks, "One collected coin must reach each retained subscription.");
                    Assert.AreEqual(10 * expected, balance, "The wallet records every callback.");
                    yield return new ExitPlayMode();
                    Assert.IsFalse(Application.isPlaying);
                }
            }
        }

        [UnityTearDown]
        public IEnumerator RestorePlaySettings()
        {
            EditorApplication.playModeStateChanged -= OnTransition;
            CoinCollected = null;
            try
            {
                if (Application.isPlaying)
                    yield return new ExitPlayMode();
            }
            finally
            {
                int options = SessionState.GetInt(Snapshot, -1);
                if (options >= 0)
                {
                    EditorSettings.enterPlayModeOptions = (EnterPlayModeOptions)options;
                    EditorSettings.enterPlayModeOptionsEnabled = SessionState.GetBool(Snapshot + ".Enabled", false);
                    SessionState.EraseInt(Snapshot);
                    SessionState.EraseBool(Snapshot + ".Enabled");
                }
            }
        }
    }
}
