#if MCP_INPUT_SYSTEM
using System;
using System.Linq;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Users;

namespace MCPForUnity.Editor.Tools.Input
{
    public partial class RuntimeInputSystemSimulationBackend
    {
        private PlayerInput _pairedPlayer;
        private InputUser _pairedUser;
        private bool _restoreAutoSwitch;

        private static PlayerInput RequirePlayer(GameObject target)
        {
            if (target == null || !target.activeInHierarchy || !target.scene.IsValid() || !target.scene.isLoaded)
                throw new ArgumentException("PlayerInput target must be an active scene GameObject.");
            PlayerInput player = target.GetComponent<PlayerInput>();
            if (player == null)
                throw new ArgumentException("target requires a PlayerInput component.");
            return player;
        }

        public object DiagnosePlayer(GameObject target)
        {
            PlayerInput player = RequirePlayer(target);
            bool active = player.isActiveAndEnabled && player.inputIsActive && player.user.valid;
            // PlayerInput.actions can lazily clone assets and install hooks. Observe only the
            // already-associated InputUser collection; diagnostics never call that lazy getter.
            IInputActionCollection actions = active ? player.user.actions : null;
            int resolved =
                actions == null
                    ? 0
                    : actions.Where(action => action.enabled).SelectMany(action => action.controls).Count(control => control.device == _gamepad);
            return new
            {
                player_index = player.playerIndex,
                enabled = player.isActiveAndEnabled,
                input_active = player.inputIsActive,
                user_valid = player.user.valid,
                control_scheme = player.currentControlScheme,
                action_map = player.currentActionMap?.name,
                paired_device_ids = player.devices.Select(device => device.deviceId).ToArray(),
                owned_gamepad_id = _gamepad?.deviceId,
                owned_gamepad_paired = _gamepad != null && player.devices.Contains(_gamepad),
                resolved_owned_controls = resolved,
                action_inspection_available = actions != null,
                delivery_eligible = player.isActiveAndEnabled && player.inputIsActive && player.user.valid && resolved > 0,
                pairing_lease_selected = _pairedPlayer == player,
                note = "Eligibility observes enabled action control resolution for this player only; callbacks, gameplay handling and successful delivery are not guaranteed.",
            };
        }

        public object PairPlayer(GameObject target, string device)
        {
            if (!Available)
                throw new ArgumentException(UnavailableReason);
            if (device != "gamepad")
                throw new ArgumentException("Only the MCP-owned gamepad can be explicitly paired.");
            PlayerInput player = RequirePlayer(target);
            if (!player.isActiveAndEnabled || !player.user.valid || player.actions == null || !player.inputIsActive)
                throw new ArgumentException("Pairing requires an enabled PlayerInput with active actions and a valid InputUser.");
            if (_subscribed && Clock >= _deadline)
                ReleaseAll();
            if (_pairedPlayer != null && _pairedPlayer != player)
                throw new ArgumentException("Another PlayerInput owns this pairing lease; release_player first.");
            if (_pairedPlayer == player && _gamepad != null && _gamepad.added && _pairedUser.valid && player.user.id == _pairedUser.id)
                return DiagnosePlayer(target);
            if (_pairedPlayer == player)
                ReleaseAll();
            if (_gamepad != null && _gamepad.added && _gamepadReleases.Count > 0)
                throw new ArgumentException("Release held gamepad controls before creating a PlayerInput pairing lease.");
            EnsureLease();
            _pairedPlayer = player;
            _pairedUser = player.user;
            _restoreAutoSwitch = !player.neverAutoSwitchControlSchemes;
            // This affects only the selected PlayerInput; its control scheme and existing devices stay intact.
            player.neverAutoSwitchControlSchemes = true;
            try
            {
                Gamepad owned = EnsureGamepad();
                InputUser.PerformPairingWithDevice(owned, _pairedUser, InputUserPairingOptions.ForceNoPlatformUserAccountSelection);
                if (_pairedPlayer != player || _gamepad != owned || !owned.added)
                    throw new InvalidOperationException("PlayerInput pairing was interrupted; retry after cleanup.");
                return DiagnosePlayer(target);
            }
            catch
            {
                ReleaseAll();
                throw;
            }
        }

        public object ReleasePlayer(GameObject target)
        {
            PlayerInput player = RequirePlayer(target);
            if (_subscribed && Clock >= _deadline)
                ReleaseAll();
            if (_pairedPlayer != null && _pairedPlayer != player)
                throw new ArgumentException("target does not own the active pairing lease.");
            ReleaseAll();
            return DiagnosePlayer(target);
        }

        private void RestorePlayerPairing()
        {
            PlayerInput player = _pairedPlayer;
            InputUser user = _pairedUser;
            bool restore = _restoreAutoSwitch;
            _pairedPlayer = null;
            _pairedUser = default;
            _restoreAutoSwitch = false;
            if (user.valid && _gamepad != null && user.pairedDevices.Contains(_gamepad))
                user.UnpairDevice(_gamepad);
            if (restore && player != null && player.neverAutoSwitchControlSchemes)
                player.neverAutoSwitchControlSchemes = false;
        }
    }
}
#endif
