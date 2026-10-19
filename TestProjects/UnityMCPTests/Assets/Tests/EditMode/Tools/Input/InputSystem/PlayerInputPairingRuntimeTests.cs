#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System.Collections;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Users;
using UnityEngine.TestTools;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public class PlayerInputPairingRuntimeTests
    {
        private static PlayerInput Player(string name, InputActionAsset asset, Gamepad original)
        {
            var go = new GameObject(name);
            go.SetActive(false);
            var player = go.AddComponent<PlayerInput>();
            player.neverAutoSwitchControlSchemes = true;
            player.actions = asset;
            player.defaultActionMap = "Gameplay";
            go.SetActive(true);
            Assert.IsTrue(player.user.valid, "The real PlayerInput lifecycle must create its InputUser.");
            player.user.UnpairDevices();
            InputUser.PerformPairingWithDevice(original, player.user);
            return player;
        }

        [UnityTest]
        public IEnumerator ExplicitPairingRoutesOnlySelectedPlayerAndRestoresPriorPairingOnPause()
        {
            Assert.IsNotNull(
                typeof(RuntimeInputSystemSimulationBackend).GetMethod("PairPlayer"),
                "Explicit reversible PlayerInput pairing must be implemented."
            );
            Assert.IsNotNull(typeof(RuntimeInputSystemSimulationBackend).GetMethod("DiagnosePlayer"), "PlayerInput diagnostics must be implemented.");
            yield return new EnterPlayMode();
            var input = new InputTestFixture();
            input.Setup();
            var backend = new InputSystemSimulationBackend();
            PlayerInput first = null;
            PlayerInput second = null;
            InputActionAsset asset = null;
            try
            {
                MethodInfo pair = typeof(RuntimeInputSystemSimulationBackend).GetMethod("PairPlayer");
                Assert.IsNotNull(pair, "Explicit reversible PlayerInput pairing must be implemented.");
                MethodInfo diagnose = typeof(RuntimeInputSystemSimulationBackend).GetMethod("DiagnosePlayer");
                Assert.IsNotNull(diagnose, "PlayerInput diagnostics must be implemented.");
                InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
                asset = ScriptableObject.CreateInstance<InputActionAsset>();
                asset.AddActionMap("Gameplay").AddAction("Move", InputActionType.Value, "<Gamepad>/leftStick");
                Gamepad one = InputApi.AddDevice<Gamepad>("OriginalOne");
                Gamepad two = InputApi.AddDevice<Gamepad>("OriginalTwo");
                first = Player("OwnedPlayerOne", asset, one);
                second = Player("OwnedPlayerTwo", asset, two);
                var beforeOne = first.devices.Select(d => d.deviceId).ToArray();
                var beforeTwo = second.devices.Select(d => d.deviceId).ToArray();
                string scheme = first.currentControlScheme;
                JObject diagnostic = JObject.FromObject(diagnose.Invoke(backend, new object[] { first.gameObject }));
                Assert.AreEqual(first.playerIndex, (int)diagnostic["player_index"]);
                CollectionAssert.AreEquivalent(beforeOne, first.devices.Select(d => d.deviceId));
                pair.Invoke(backend, new object[] { first.gameObject, "gamepad" });
                Gamepad owned = Gamepad.current;
                Assert.Contains(owned, first.devices.ToArray());
                Assert.IsFalse(second.devices.Contains(owned));
                backend.Apply(
                    InputSimulationRequest.Parse(JObject.Parse("{\"action\":\"gamepad\",\"control\":\"leftStick\",\"value\":[0.75,0.25],\"frames\":600}"))
                );
                InputApi.Update();
                Assert.Greater(first.actions["Gameplay/Move"].ReadValue<Vector2>().magnitude, 0.5f);
                Assert.AreEqual(Vector2.zero, second.actions["Gameplay/Move"].ReadValue<Vector2>());
                EditorApplication.isPaused = true;
                yield return null;
                Assert.IsFalse(owned.added, "Real pause callback must remove the paired owned device.");
                CollectionAssert.AreEquivalent(beforeOne, first.devices.Select(d => d.deviceId));
                CollectionAssert.AreEquivalent(beforeTwo, second.devices.Select(d => d.deviceId));
                Assert.AreEqual(scheme, first.currentControlScheme);
                Assert.IsTrue(one.added && two.added);
            }
            finally
            {
                backend.ReleaseAll();
                EditorApplication.isPaused = false;
                if (first != null)
                    Object.DestroyImmediate(first.gameObject);
                if (second != null)
                    Object.DestroyImmediate(second.gameObject);
                if (asset != null)
                    Object.DestroyImmediate(asset);
                input.TearDown();
            }
            yield return new ExitPlayMode();
        }

        [UnityTearDown]
        public IEnumerator RestoreEditMode()
        {
            EditorApplication.isPaused = false;
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }
    }
}
#endif
