namespace MCPForUnity.Editor.Tools
{
    // Editor-thread cache for the project-wide GUID census. Continuations use the original
    // membership and checkpoint; each asset description is still observed when its page is read.
    internal sealed class ReferenceGraphInventories
    {
        internal sealed class Version
        {
            internal readonly string[] Items;
            internal readonly string Token;
            internal readonly string Epoch;
            internal readonly long Revision;
            internal double LastUsed;

            internal Version(string[] items, string token, string epoch, long revision, double now)
            {
                Items = items;
                Token = token;
                Epoch = epoch;
                Revision = revision;
                LastUsed = now;
            }
        }

        // Cap the incremental cost of a second version. The current census has no size limit.
        internal const int MaximumPreviousGuids = 65536;
        private readonly int maximumPreviousGuids;
        internal Version Current { get; private set; }
        internal Version Previous { get; private set; }

        internal ReferenceGraphInventories(int maximumPreviousGuids = MaximumPreviousGuids) => this.maximumPreviousGuids = maximumPreviousGuids;

        internal void ReleaseExpired(string epoch, double now)
        {
            if (Current != null && (Current.Epoch != epoch || now - Current.LastUsed > 600))
                Current = null;
            if (Previous != null && (Previous.Epoch != epoch || now - Previous.LastUsed > 600))
                Previous = null;
        }

        internal void Publish(string[] items, string token, string epoch, long revision, double now)
        {
            ReleaseExpired(epoch, now);
            // Shared async waiters and synchronous readers can finish the same revision.
            if (Current == null || Current.Revision != revision)
            {
                if (Current != null)
                    Previous = maximumPreviousGuids > 0 && Current.Items.Length <= maximumPreviousGuids ? Current : null;
                Current = new Version(items, token, epoch, revision, now);
            }
            Current.LastUsed = now;
        }

        internal Version Find(string token, string epoch, double now)
        {
            ReleaseExpired(epoch, now);
            Version match =
                Current?.Token == token ? Current
                : Previous?.Token == token ? Previous
                : null;
            if (match != null)
                match.LastUsed = now;
            return match;
        }
    }
}
