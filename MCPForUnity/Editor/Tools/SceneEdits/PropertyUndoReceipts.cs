using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.SceneEdits
{
    // Bound ownership to the Editor domain that actually performed the write.
    internal static class PropertyUndoReceipts
    {
        private static readonly LinkedList<(string id, Func<object> restore)> Recent = new();

        internal static string Retain(Func<object> restore)
        {
            string id = Guid.NewGuid().ToString("N");
            if (Recent.Count == 32)
                Recent.RemoveFirst();
            Recent.AddLast((id, restore));
            return id;
        }

        internal static object Restore(string id)
        {
            for (var node = Recent.First; node != null; node = node.Next)
            {
                if (node.Value.id != id)
                    continue;
                var restore = node.Value.restore;
                Recent.Remove(node);
                return restore();
            }
            return new ErrorResponse("Unknown or consumed property undo receipt in this Editor domain.");
        }

        internal enum Decision
        {
            Unchanged,
            Restore,
            Conflict,
        }

        internal static Decision Compare(JToken before, JToken applied, JToken current, JObject identity, JArray conflicts)
        {
            if (SameField(before, applied) || SameField(before, current))
                return Decision.Unchanged;
            if (SameField(applied, current))
                return Decision.Restore;
            identity["before"] = before;
            identity["applied"] = applied;
            identity["current"] = current;
            conflicts.Add(identity);
            return Decision.Conflict;
        }

        // Newtonsoft's numeric DeepEquals uses a tolerance. A nearby float can be a
        // distinct authored value; compare the finite native scalars from the field codec.
        internal static bool SameField(JToken left, JToken right) =>
            left is JArray a && right is JArray b
                ? a.Count == b.Count && a.Zip(b, SameField).All(equal => equal)
                : left is JValue x && right is JValue y && Equals(x.Value, y.Value);

        internal static object Report(int restored, JArray conflicts, Func<object> retryConflicts)
        {
            var data = new
            {
                restored,
                conflicts,
                remaining_receipt_id = conflicts.Count > 0 ? Retain(retryConflicts) : null,
            };
            return conflicts.Count == 0 ? new SuccessResponse("Property undo complete.", data) : new ErrorResponse("property_undo_conflict", data);
        }
    }
}
