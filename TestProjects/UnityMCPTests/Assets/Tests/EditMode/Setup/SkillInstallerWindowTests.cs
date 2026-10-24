using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    public class SkillInstallerWindowTests
    {
        [Test]
        public void CancellationRetainsOwnershipUntilCompletionAndRejectsDuplicateApply()
        {
            using var state = new SkillInstallerOperation();
            var attempt = state.Begin("Applying safe changes");
            var token = attempt.Token;
            state.RequestCancellation();
            Assert.IsTrue(token.IsCancellationRequested);
            Assert.IsTrue(state.IsRunning);
            Assert.IsTrue(state.CancellationRequested);
            Assert.IsTrue(state.Owns(attempt));
            Assert.IsNull(state.Begin("Duplicate apply"));
            Assert.IsTrue(state.Complete(attempt));
            Assert.IsFalse(state.IsRunning);
            Assert.IsFalse(state.Complete(attempt));
            Assert.IsNotNull(state.Begin("Previewing changes"));
        }

        [Test]
        public void CloseInvalidatesLateCompletionAndLogsWithoutAffectingReplacement()
        {
            using var state = new SkillInstallerOperation();
            var closed = state.Begin("Previewing changes");
            var token = closed.Token;
            state.Dispose();
            Assert.IsTrue(token.IsCancellationRequested);
            Assert.IsFalse(state.Owns(closed));
            var replacement = state.Begin("Applying safe changes");
            Assert.IsFalse(state.Complete(closed));
            Assert.IsTrue(state.Owns(replacement));
            Assert.IsTrue(state.IsRunning);
        }

        [Test]
        public void LargePreviewKeepsEveryPathAcrossPagesAndFiltersWithoutRebuildingOnReads()
        {
            var paths = Enumerable.Range(0, 8192).Select(index => $"references/guide-{index:D5}.md").ToArray();
            var preview = Preview(paths);
            var review = new SkillInstallerReview();
            review.SetPreview(preview);
            Assert.AreEqual(8192, review.MatchCount);
            var seen = new List<string>();
            for (int page = 0; page < review.PageCount; page++)
            {
                review.SetPage(page);
                for (int row = 0; row < review.VisibleCount; row++)
                    seen.Add(review.GetVisiblePath(row));
            }
            CollectionAssert.AreEqual(paths, seen);
            review.SetFilter(SkillInstallerReview.Group.Added, "GUIDE-0819");
            Assert.AreEqual(2, review.MatchCount);
            Assert.AreEqual(0, review.Page);
            Assert.AreEqual(paths[8190], review.GetVisiblePath(0));
            Assert.AreEqual(paths[8191], review.GetVisiblePath(1));
            int builds = review.FilterRevision;
            review.SetPreview(preview);
            review.SetFilter(SkillInstallerReview.Group.Added, "GUIDE-0819");
            review.SetPage(0);
            Assert.AreEqual(2, review.VisibleCount);
            Assert.AreEqual(builds, review.FilterRevision, "Unchanged drawing inputs must reuse the filtered collection.");
            CollectionAssert.AreEqual(paths, preview.Added);
        }

        [Test]
        public void GroupsShowPreservedAndConflictingPathsAndEmptyFilterDoesNotLoseSelection()
        {
            var review = new SkillInstallerReview();
            review.SetPreview(Preview(new[] { "new.md" }, new[] { "updated.md" }, new[] { "removed.md" }, new[] { "local.md" }, new[] { "conflict.md" }));
            Assert.AreEqual(SkillInstallerReview.Group.Conflicts, review.SelectedGroup);
            foreach (SkillInstallerReview.Group group in Enum.GetValues(typeof(SkillInstallerReview.Group)))
            {
                review.SetFilter(group, ".md");
                Assert.AreEqual(1, review.MatchCount);
                Assert.IsNotEmpty(review.GetVisiblePath(0));
            }
            review.SetFilter(SkillInstallerReview.Group.Preserved, "missing");
            Assert.AreEqual(0, review.VisibleCount);
            Assert.AreEqual(1, review.PageCount);
            Assert.AreEqual(SkillInstallerReview.Group.Preserved, review.SelectedGroup);
            review.SetFilter(SkillInstallerReview.Group.Preserved, "");
            Assert.AreEqual("local.md", review.GetVisiblePath(0));
            review.SetPreview(null);
            Assert.AreEqual(0, review.MatchCount);
        }

        private static SkillSyncService.SyncPreview Preview(
            string[] added,
            string[] updated = null,
            string[] deleted = null,
            string[] preserved = null,
            string[] conflicts = null
        )
        {
            var plan = new SkillSyncService.SyncPlan();
            plan.Added.AddRange(added);
            plan.Updated.AddRange(updated ?? Array.Empty<string>());
            plan.Deleted.AddRange(deleted ?? Array.Empty<string>());
            plan.Preserved.AddRange(preserved ?? Array.Empty<string>());
            plan.Conflicts.AddRange(conflicts ?? Array.Empty<string>());
            return new SkillSyncService.SyncPreview(
                SkillSyncSource.Latest("https://github.com/ykh09242/unity-mcp", "beta"),
                "synthetic-install",
                new SkillSyncService.RemoteSnapshot(new string('a', 40), SkillSyncSource.SkillSubdirectory, new Dictionary<string, string>()),
                plan
            );
        }
    }
}
