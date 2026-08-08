using System;
using System.Collections.Generic;
using System.Linq;
using CaughtOnDash.Worker.Models;
using CaughtOnDash.Worker.Services;
using Xunit;

namespace CaughtOnDash.Worker.Core.Tests
{
    /// <summary>
    /// Getting this wrong is invisible: the rows look right and the queue runs
    /// in the wrong order minutes later, which is precisely the kind of defect
    /// nobody traces back to a reorder click.
    /// </summary>
    public class QueueOrderingTests
    {
        private static readonly Guid Q1 = new("11111111-1111-1111-1111-111111111111");
        private static readonly Guid Q2 = new("22222222-2222-2222-2222-222222222222");
        private static readonly Guid R1 = new("aaaaaaaa-1111-1111-1111-111111111111");
        private static readonly Guid R2 = new("bbbbbbbb-2222-2222-2222-222222222222");
        private static readonly Guid R3 = new("cccccccc-3333-3333-3333-333333333333");

        private static List<QueueEntry> Entries(params Guid[] ids)
            => ids.Select(id => new QueueEntry { VideoId = id }).ToList();

        [Fact]
        public void ReorderingTheReviewListKeepsItBehindTheRunQueue()
        {
            // The whole point: priorities descend from list length, so a review
            // list sent on its own would be numbered into the run queue's band
            // and interleave with work already approved.
            var order = QueueOrdering.GlobalOrder(
                queued: Entries(Q1, Q2),
                awaitingReview: Entries(R1, R2, R3),
                reordered: new[] { R3, R1, R2 },
                reorderedIsReview: true);

            Assert.Equal(new[] { Q1, Q2, R3, R1, R2 }, order);
        }

        [Fact]
        public void ReorderingTheRunQueueLeavesTheReviewListBehindIt()
        {
            var order = QueueOrdering.GlobalOrder(
                queued: Entries(Q1, Q2),
                awaitingReview: Entries(R1, R2),
                reordered: new[] { Q2, Q1 },
                reorderedIsReview: false);

            Assert.Equal(new[] { Q2, Q1, R1, R2 }, order);
        }

        [Fact]
        public void AVideoThatMovedBetweenTabsIsNotListedTwice()
        {
            // Between refreshes a video can be approved elsewhere and appear in
            // both snapshots. A duplicate id would give it two priorities, and
            // the backend would apply the later one -- silently undoing part of
            // the order that was just set.
            var order = QueueOrdering.GlobalOrder(
                queued: Entries(Q1, R1),
                awaitingReview: Entries(R1, R2),
                reordered: new[] { R2, R1 },
                reorderedIsReview: true);

            Assert.Equal(order.Count, order.Distinct().Count());
            // R1 sits in the queued band, because that is where it now is. The
            // review reorder still applies to everything else.
            Assert.Equal(new[] { Q1, R1, R2 }, order);
        }

        [Fact]
        public void AnEmptyOtherTabIsFine()
        {
            var order = QueueOrdering.GlobalOrder(
                queued: Entries(),
                awaitingReview: Entries(R1, R2),
                reordered: new[] { R2, R1 },
                reorderedIsReview: true);

            Assert.Equal(new[] { R2, R1 }, order);
        }

        [Fact]
        public void ApprovingABatchPutsItBehindWhatIsAlreadyQueued()
        {
            // Approving is not a request to jump the queue.
            var order = QueueOrdering.BatchOrder(Entries(Q1, Q2), new[] { R2, R1 });

            Assert.Equal(new[] { Q1, Q2, R2, R1 }, order);
        }

        [Fact]
        public void ABatchAlreadyInTheQueueIsNotDuplicated()
        {
            // Re-approving something already queued must move it, not list it
            // twice -- the second priority would win and undo the move.
            var order = QueueOrdering.BatchOrder(Entries(Q1, R1, Q2), new[] { R1, R2 });

            Assert.Equal(new[] { Q1, Q2, R1, R2 }, order);
        }

        [Fact]
        public void AnEmptyQueueLeavesTheBatchOrderIntact()
        {
            var order = QueueOrdering.BatchOrder(Entries(), new[] { R3, R1, R2 });

            Assert.Equal(new[] { R3, R1, R2 }, order);
        }
    }
}
