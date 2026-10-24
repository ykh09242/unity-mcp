using System;
using System.Collections.Generic;

namespace MCPForUnity.Editor.Setup
{
    /// <summary>Local, paged view of the immutable preview; rebuilding happens only when its inputs change.</summary>
    internal sealed class SkillInstallerReview
    {
        internal enum Group
        {
            Added,
            Updated,
            Deleted,
            Preserved,
            Conflicts,
        }

        private const int PageSize = 50;
        private SkillSyncService.SyncPreview _preview;
        private IReadOnlyList<string> _matches = Array.Empty<string>();
        public string[] GroupLabels { get; private set; } = new[] { "Added (0)", "Updated (0)", "Deleted (0)", "Preserved (0)", "Conflicts (0)" };
        public Group SelectedGroup { get; private set; }
        public string Query { get; private set; } = string.Empty;
        public int Page { get; private set; }
        public int FilterRevision { get; private set; }
        public int MatchCount => _matches.Count;
        public int PageCount => Math.Max(1, (MatchCount + PageSize - 1) / PageSize);
        public int VisibleCount => Math.Min(PageSize, MatchCount - Page * PageSize);

        public void SetPreview(SkillSyncService.SyncPreview preview)
        {
            if (ReferenceEquals(_preview, preview))
                return;
            _preview = preview;
            GroupLabels = new string[5];
            SelectedGroup = Group.Added;
            for (int index = 0; index < GroupLabels.Length; index++)
            {
                var group = (Group)index;
                int count = Paths(group).Count;
                GroupLabels[index] = $"{group} ({count})";
                if (count > 0 && Paths(SelectedGroup).Count == 0)
                    SelectedGroup = group;
            }
            if (Paths(Group.Conflicts).Count > 0)
                SelectedGroup = Group.Conflicts;
            Query = string.Empty;
            Rebuild();
        }

        public void SetFilter(Group group, string query)
        {
            query ??= string.Empty;
            if (SelectedGroup == group && Query == query)
                return;
            SelectedGroup = group;
            Query = query;
            Rebuild();
        }

        public void SetPage(int page) => Page = Math.Max(0, Math.Min(page, PageCount - 1));

        public string GetVisiblePath(int row)
        {
            if (row < 0 || row >= VisibleCount)
                throw new ArgumentOutOfRangeException(nameof(row));
            return _matches[Page * PageSize + row];
        }

        private void Rebuild()
        {
            var paths = Paths(SelectedGroup);
            if (Query.Length == 0)
                _matches = paths;
            else
            {
                var matches = new List<string>();
                foreach (string path in paths)
                    if (path.IndexOf(Query, StringComparison.OrdinalIgnoreCase) >= 0)
                        matches.Add(path);
                _matches = matches;
            }
            Page = 0;
            FilterRevision++;
        }

        private IReadOnlyList<string> Paths(Group group)
        {
            if (_preview == null || !_preview.Success)
                return Array.Empty<string>();
            return group switch
            {
                Group.Added => _preview.Added,
                Group.Updated => _preview.Updated,
                Group.Deleted => _preview.Deleted,
                Group.Preserved => _preview.Preserved,
                Group.Conflicts => _preview.Conflicts,
                _ => Array.Empty<string>(),
            };
        }
    }
}
