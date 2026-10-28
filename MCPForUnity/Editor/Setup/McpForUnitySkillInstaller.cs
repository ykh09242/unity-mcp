using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Constants;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Setup
{
    public class McpForUnitySkillInstaller : EditorWindow
    {
        private const string RepoUrlKey = "UnityMcpSkillSync.RepoUrl.ykh09242";
        private const string BranchKey = "UnityMcpSkillSync.Branch";
        private const string CliKey = "UnityMcpSkillSync.Cli";
        private const string InstallDirKey = "UnityMcpSkillSync.InstallDir";
        private const string CodexCli = "codex";
        private const string ClaudeCli = "claude";
        private static readonly string[] BranchOptions = { "beta", "main" };
        private static readonly string[] SourceOptions = { "Installed package", "Latest from branch" };
        private static readonly string[] CliOptions = { CodexCli, ClaudeCli };

        private string _repoUrl;
        private string _targetBranch;
        private string _cliType;
        private string _installDir;
        private bool _latest;
        private SkillSyncSource _packageSource;
        private string _packageError;
        private Vector2 _scroll;
        private Vector2 _formScroll;
        private bool _followOutput = true;
        private bool _sourceDetails;
        private bool _showOutput;
        private string _status;
        private readonly SkillInstallerOperation _operation = new();
        private readonly SkillInstallerReview _review = new();
        private SkillSyncService.SyncPreview _preview;
        private readonly BoundedEditorLog _log = new();
        private long _displayedLogRevision;
        private double _nextRepaintAt;

        [MenuItem(ProductInfo.MenuRoot + "/Skill Installer", priority = 4)]
        public static void OpenWindow()
        {
            GetWindow<McpForUnitySkillInstaller>("Unity MCP Skill Install(Sync)");
        }

        private void OnEnable()
        {
            var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            _repoUrl = EditorPrefs.GetString(RepoUrlKey, "https://github.com/ykh09242/unity-mcp");
            _targetBranch = EditorPrefs.GetString(BranchKey, "beta");
            if (!BranchOptions.Contains(_targetBranch))
            {
                _targetBranch = "beta";
            }
            _cliType = EditorPrefs.GetString(CliKey, CodexCli);
            if (!CliOptions.Contains(_cliType))
            {
                _cliType = CodexCli;
            }
            _installDir = EditorPrefs.GetString(InstallDirKey, GetDefaultInstallDir(userHome, _cliType));
            _latest = false;
            RefreshPackageSource();
            EditorApplication.update += OnEditorUpdate;
        }

        private void OnDisable()
        {
            EditorApplication.update -= OnEditorUpdate;
            _operation.Dispose();
            EditorPrefs.SetString(RepoUrlKey, _repoUrl);
            EditorPrefs.SetString(BranchKey, _targetBranch);
            EditorPrefs.SetString(CliKey, _cliType);
            EditorPrefs.SetString(InstallDirKey, _installDir);
        }

        private void OnGUI()
        {
            FlushPendingLogs();
            _formScroll = EditorGUILayout.BeginScrollView(_formScroll);
            EditorGUILayout.LabelField("Preview changes, then apply. Local edits and additions are preserved.", EditorStyles.wordWrappedLabel);
            EditorGUI.BeginChangeCheck();
            using (new EditorGUI.DisabledScope(_operation.IsRunning))
            {
                _latest = EditorGUILayout.Popup("Skill source", _latest ? 1 : 0, SourceOptions) == 1;
                if (!_latest)
                {
                    if (_packageSource == null)
                        EditorGUILayout.HelpBox(_packageError ?? "Installed package source unavailable.", MessageType.Error);
                }
                if (_latest)
                {
                    _repoUrl = EditorGUILayout.TextField("Repo URL", _repoUrl);
                    var branchIndex = Array.IndexOf(BranchOptions, _targetBranch);
                    if (branchIndex < 0)
                    {
                        branchIndex = 0;
                    }

                    var selectedBranchIndex = EditorGUILayout.Popup("Branch", branchIndex, BranchOptions);
                    _targetBranch = BranchOptions[selectedBranchIndex];
                }

                var cliIndex = Array.IndexOf(CliOptions, _cliType);
                if (cliIndex < 0)
                {
                    cliIndex = 0;
                }

                var selectedCliIndex = EditorGUILayout.Popup("CLI", cliIndex, CliOptions);
                if (selectedCliIndex != cliIndex)
                {
                    var previousCli = _cliType;
                    _cliType = CliOptions[selectedCliIndex];
                    TryApplyCliDefaultInstallPath(previousCli, _cliType);
                }

                _installDir = EditorGUILayout.TextField("Destination", _installDir);
            }
            if (EditorGUI.EndChangeCheck())
            {
                SetPreview(null);
                _status = null;
            }

            _sourceDetails = EditorGUILayout.Foldout(_sourceDetails, "Source details", true);
            if (_sourceDetails)
                EditorGUILayout.LabelField(
                    _preview != null ? _preview.SourceDescription + "\nResolved commit: " + _preview.CommitSha
                        : _latest ? "Resolves the branch at preview time. Compatibility with this package is not verified."
                        : (_packageSource?.Description ?? _packageError) + "\nInstalled files may differ from this source.",
                    EditorStyles.wordWrappedLabel
                );

            EditorGUILayout.Space(4f);
            EditorGUILayout.BeginHorizontal();
            using (new EditorGUI.DisabledScope(_operation.IsRunning))
            {
                if (GUILayout.Button(_latest ? $"Preview latest ({_targetBranch})" : "Preview package skills", GUILayout.Height(32f)))
                    RunPreview();
            }

            using (new EditorGUI.DisabledScope(_operation.IsRunning || _preview == null))
                if (GUILayout.Button("Apply safe changes", GUILayout.Height(32f)))
                    RunApply();

            EditorGUILayout.EndHorizontal();
            if (_operation.IsRunning)
            {
                EditorGUILayout.BeginHorizontal();
                EditorGUILayout.LabelField(_operation.Phase + "…", EditorStyles.boldLabel);
                using (new EditorGUI.DisabledScope(_operation.CancellationRequested))
                    if (GUILayout.Button(_operation.CancellationRequested ? "Cancellation requested" : "Request cancellation", GUILayout.Width(165f)))
                        _operation.RequestCancellation();
                EditorGUILayout.EndHorizontal();
                if (_operation.CancellationRequested)
                    EditorGUILayout.HelpBox(
                        "Cancellation requested. Waiting for the current attempt to finish; changes may already have been applied.",
                        MessageType.Info
                    );
            }
            else if (!string.IsNullOrEmpty(_status))
                EditorGUILayout.LabelField(_status, EditorStyles.wordWrappedLabel);
            if (_preview != null)
            {
                EditorGUILayout.HelpBox(
                    $"Preview: +{_preview.Added.Count} ~{_preview.Updated.Count} -{_preview.Deleted.Count}. "
                        + $"Local preserved: {_preview.Preserved.Count}. Conflicts retained: {_preview.Conflicts.Count}.",
                    _preview.Conflicts.Count > 0 ? MessageType.Warning : MessageType.Info
                );
                DrawReview();
            }

            EditorGUILayout.Space(8f);
            _showOutput = EditorGUILayout.Foldout(_showOutput, "Output", true);
            if (_showOutput)
            {
                string output = _log.Snapshot();
                if (string.IsNullOrEmpty(output))
                    EditorGUILayout.LabelField("No output yet. Preview changes to begin.", EditorStyles.wordWrappedLabel);
                else
                {
                    EditorGUILayout.BeginHorizontal();
                    bool follow = GUILayout.Toggle(_followOutput, "Follow output", GUILayout.Width(105f));
                    if (follow && !_followOutput)
                        _scroll.y = float.MaxValue;
                    _followOutput = follow;
                    if (GUILayout.Button("Clear", GUILayout.Width(55f)))
                        _log.Clear();
                    EditorGUILayout.EndHorizontal();
                    _scroll = EditorGUILayout.BeginScrollView(_scroll, GUILayout.Height(150f));
                    EditorGUILayout.TextArea(output, EditorStyles.textArea);
                    EditorGUILayout.EndScrollView();
                }
            }
            EditorGUILayout.EndScrollView();
        }

        private void DrawReview()
        {
            EditorGUILayout.LabelField("Review paths", EditorStyles.boldLabel);
            var group = (SkillInstallerReview.Group)EditorGUILayout.Popup("Change group", (int)_review.SelectedGroup, _review.GroupLabels);
            string query = EditorGUILayout.TextField("Filter paths", _review.Query);
            _review.SetFilter(group, query);
            EditorGUILayout.LabelField($"{_review.MatchCount} matching paths", EditorStyles.miniLabel);
            if (_review.PageCount > 1)
            {
                EditorGUILayout.BeginHorizontal();
                using (new EditorGUI.DisabledScope(_review.Page == 0))
                    if (GUILayout.Button("Previous"))
                        _review.SetPage(_review.Page - 1);
                GUILayout.Label($"Page {_review.Page + 1} of {_review.PageCount}");
                using (new EditorGUI.DisabledScope(_review.Page == _review.PageCount - 1))
                    if (GUILayout.Button("Next"))
                        _review.SetPage(_review.Page + 1);
                EditorGUILayout.EndHorizontal();
            }
            for (int index = 0; index < _review.VisibleCount; index++)
                EditorGUILayout.SelectableLabel(_review.GetVisiblePath(index), GUILayout.Height(EditorGUIUtility.singleLineHeight));
            if (_review.MatchCount == 0)
                EditorGUILayout.LabelField("No paths match this group and filter.", EditorStyles.wordWrappedLabel);
        }

        private void SetPreview(SkillSyncService.SyncPreview preview)
        {
            _preview = preview;
            _review.SetPreview(preview);
        }

        private void OnEditorUpdate()
        {
            var changed = FlushPendingLogs();
            if (changed || (_operation.IsRunning && EditorApplication.timeSinceStartup >= _nextRepaintAt))
            {
                _nextRepaintAt = EditorApplication.timeSinceStartup + 0.25;
                Repaint();
            }
        }

        private void RunPreview()
        {
            if (_operation.IsRunning)
                return;
            SkillSyncSource source;
            try
            {
                source = _latest ? SkillSyncSource.Latest(_repoUrl, _targetBranch) : SkillSyncSource.CaptureInstalled();
                if (!_latest)
                    _packageSource = source;
            }
            catch (Exception error)
            {
                SetPreview(null);
                _packageError = error.Message;
                _status = "Preview failed: " + error.Message;
                AppendLineImmediate("[ERROR] " + error.Message);
                return;
            }
            SetPreview(null);
            _status = null;
            var operation = _operation.Begin("Previewing changes");
            SkillSyncService.PreviewSourceAsync(
                source,
                _installDir,
                line =>
                {
                    if (_operation.Owns(operation))
                        _log.Append(line);
                },
                preview =>
                {
                    if (this == null || !_operation.Complete(operation))
                        return;
                    SetPreview(preview.Success ? preview : null);
                    _status = preview.Success ? "Preview ready. Review paths before applying." : "Preview failed: " + preview.Error;
                    if (!preview.Success)
                        AppendLineImmediate("[ERROR] " + preview.Error);
                    Repaint();
                },
                operation.Token
            );
        }

        private void RefreshPackageSource()
        {
            try
            {
                _packageSource = SkillSyncSource.CaptureInstalled();
                _packageError = null;
            }
            catch (Exception error)
            {
                _packageSource = null;
                _packageError = error.Message;
            }
        }

        private void RunApply()
        {
            if (_operation.IsRunning || _preview == null)
                return;
            var preview = _preview;
            SetPreview(null);
            _status = null;
            var operation = _operation.Begin("Applying safe changes");
            SkillSyncService.ApplyAsync(
                preview,
                line =>
                {
                    if (_operation.Owns(operation))
                        _log.Append(line);
                },
                result =>
                {
                    if (this == null || !_operation.Complete(operation))
                        return;
                    _status = SkillSyncService.DescribeResult(result);
                    AppendLineImmediate(SkillSyncService.DescribeResult(result, includeSourceDetails: true));
                    Repaint();
                },
                operation.Token
            );
        }

        private void TryApplyCliDefaultInstallPath(string previousCli, string currentCli)
        {
            var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            var previousDefaultInstall = GetDefaultInstallDir(userHome, previousCli);
            var currentDefaultInstall = GetDefaultInstallDir(userHome, currentCli);

            if (string.IsNullOrWhiteSpace(_installDir) || PathsEqual(_installDir, previousDefaultInstall))
            {
                _installDir = currentDefaultInstall;
            }
        }

        private static string GetDefaultInstallDir(string userHome, string cliType)
        {
            var baseDir = IsClaudeCli(cliType) ? ".claude" : ".codex";
            return Path.Combine(userHome, baseDir, "skills/unity-mcp-skill");
        }

        private static bool IsClaudeCli(string cliType)
        {
            return string.Equals(cliType, ClaudeCli, StringComparison.Ordinal);
        }

        private static bool PathsEqual(string left, string right)
        {
            if (string.IsNullOrWhiteSpace(left) || string.IsNullOrWhiteSpace(right))
            {
                return false;
            }

            try
            {
                return string.Equals(SkillSyncService.ExpandPath(left), SkillSyncService.ExpandPath(right), StringComparison.Ordinal);
            }
            catch
            {
                return false;
            }
        }

        private void AppendLineImmediate(string line)
        {
            _log.Append(line);
            FlushPendingLogs();
            Repaint();
        }

        private bool FlushPendingLogs()
        {
            long revision = _log.Revision;
            if (revision == _displayedLogRevision)
                return false;
            _displayedLogRevision = revision;
            if (_followOutput)
                _scroll.y = float.MaxValue;
            return true;
        }
    }
}
