using System;
using System.Collections.Generic;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// Works out the priority order to send after a reorder.
    ///
    /// Shared by both hosts rather than written twice: getting this wrong is
    /// invisible in the UI -- the rows look right and the queue simply runs in
    /// the wrong order later.
    /// </summary>
    public static class QueueOrdering
    {
        /// <summary>
        /// One order spanning both tabs: everything queued, then everything
        /// awaiting review.
        ///
        /// The backend assigns priorities descending from the length of the
        /// list it is given, so sending a single tab's rows renumbers them into
        /// the same space the other tab occupies. Reordering the review list on
        /// its own would silently shuffle it in among already-approved work,
        /// and approving would start to mean "jump the queue". Sending both
        /// keeps the two bands separate.
        ///
        /// A video can appear in both snapshots -- approved on another host
        /// since the last refresh -- and is placed in the queued band, because
        /// that is where it now actually is. Listing it twice would give it two
        /// priorities and the later write would silently undo part of the order
        /// just set.
        /// </summary>
        /// <param name="queued">The run queue, in its current order.</param>
        /// <param name="awaitingReview">The review list, in its current order.</param>
        /// <param name="reordered">The rows of the tab being reordered, in their new order.</param>
        /// <param name="reorderedIsReview">Which tab those rows belong to.</param>
        public static List<Guid> GlobalOrder(
            IEnumerable<QueueEntry> queued,
            IEnumerable<QueueEntry> awaitingReview,
            IReadOnlyList<Guid> reordered,
            bool reorderedIsReview)
        {
            var ids = new List<Guid>();
            var seen = new HashSet<Guid>();

            void AppendTab(IEnumerable<QueueEntry> entries, bool isReorderedTab)
            {
                if (isReorderedTab)
                {
                    foreach (var id in reordered)
                    {
                        if (seen.Add(id)) ids.Add(id);
                    }
                    return;
                }

                foreach (var entry in entries)
                {
                    if (seen.Add(entry.VideoId)) ids.Add(entry.VideoId);
                }
            }

            AppendTab(queued, !reorderedIsReview);
            AppendTab(awaitingReview, reorderedIsReview);

            return ids;
        }

        /// <summary>
        /// Move the ticked videos one place up (-1) or down (+1) within a list.
        ///
        /// Walks from the edge the rows are moving toward, so a block of
        /// adjacent selections shifts together instead of collapsing, and a
        /// block already at the edge stays put.
        /// </summary>
        public static List<Guid> Move(IReadOnlyList<Guid> order, ISet<Guid> selected, int direction)
        {
            var moved = new List<Guid>(order);
            var indexes = new List<int>();
            for (var i = 0; i < moved.Count; i++)
            {
                if (selected.Contains(moved[i])) indexes.Add(i);
            }

            if (direction < 0)
            {
                foreach (var index in indexes)
                {
                    if (index == 0) break;
                    (moved[index - 1], moved[index]) = (moved[index], moved[index - 1]);
                }
            }
            else
            {
                indexes.Reverse();
                foreach (var index in indexes)
                {
                    if (index >= moved.Count - 1) break;
                    (moved[index + 1], moved[index]) = (moved[index], moved[index + 1]);
                }
            }

            return moved;
        }

        /// <summary>
        /// The order to send when approving a batch: what is already queued,
        /// then the batch behind it, in the order it was arranged.
        /// </summary>
        public static List<Guid> BatchOrder(
            IEnumerable<QueueEntry> queued, IReadOnlyList<Guid> batch)
        {
            var batchIds = new HashSet<Guid>(batch);
            var ids = new List<Guid>();

            foreach (var entry in queued)
            {
                if (!batchIds.Contains(entry.VideoId)) ids.Add(entry.VideoId);
            }

            ids.AddRange(batch);
            return ids;
        }
    }
}
