using System;
using System.Text.RegularExpressions;
using UnityEngine;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>An explicit scene-scoped scenario identity that survives hierarchy renames and reparenting.</summary>
    [DisallowMultipleComponent]
    [AddComponentMenu("MCP for Unity/Play Scenario Target")]
    public sealed class PlayScenarioTarget : MonoBehaviour
    {
        private static readonly Regex IdentifierPattern = new Regex(@"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\z", RegexOptions.CultureInvariant);

        [SerializeField]
        [Tooltip("Unique within the active scene. Scenario IDs are exact and case-sensitive.")]
        private string _targetId = "";

        public string TargetId
        {
            get => _targetId;
            set
            {
                if (!string.IsNullOrEmpty(value) && !IsValidTargetId(value))
                    throw new ArgumentException("Target ID must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}.", nameof(value));
                _targetId = value ?? "";
            }
        }

        public static bool IsValidTargetId(string value) => value != null && value.Length <= 128 && IdentifierPattern.IsMatch(value);
    }
}
