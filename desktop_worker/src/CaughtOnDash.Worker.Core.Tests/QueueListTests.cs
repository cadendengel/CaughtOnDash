using System;
using System.Collections.Generic;
using System.Linq;
using CaughtOnDash.Worker.Models;
using CaughtOnDash.Worker.Services;
using Xunit;

namespace CaughtOnDash.Worker.Core.Tests
{
    /// <summary>
    /// The one list replaces two tabs, so what it groups, labels and offers
    /// is the whole of what the window says about the queue.
    /// </summary>
    public class QueueListTests
    {
        private static QueueEntry Entry(string title, int progress = 0)
            => new() { VideoId = Guid.NewGuid(), Title = title, AnalysisProgress = progress };

        private static QueueSnapshot Snapshot()
        {
            var running = Entry("I-35 cut-in", progress: 62);
            return new QueueSnapshot
            {
                Running = new() { running },
                Queued = new() { Entry("snow"), Entry("low sun"), Entry("bridge") },
                AwaitingReview = new() { Entry("wrong way"), Entry("stop and go") },
                Failed = new() { Entry("probe") },
            };
        }

        [Fact]
        public void GroupsRunInTheOrderThingsHappen()
        {
            var rows = QueueList.Build(Snapshot());

            Assert.Equal(
                new[] { "I-35 cut-in", "snow", "low sun", "bridge", "wrong way", "stop and go", "probe" },
                rows.Select(r => r.Entry.Title));
            Assert.Equal(
                new[] { "Running 62%", "Queued #1", "Queued #2", "Queued #3", "Needs review", "Needs review", "Failed" },
                rows.Select(r => r.StatusLabel));
            Assert.Equal("Queued · 3 — runs top to bottom", rows[1].GroupHeader);
            Assert.Equal("Needs review · 2 — nothing runs until you start it", rows[4].GroupHeader);
        }

        [Fact]
        public void AVideoInTwoGroupsIsListedOnceWhereItGotTo()
        {
            // Approved on another host between the backend's two queries.
            var snapshot = Snapshot();
            var moved = snapshot.AwaitingReview[0];
            snapshot.Queued.Add(moved);

            var rows = QueueList.Build(snapshot);

            Assert.Single(rows, r => r.Entry.VideoId == moved.VideoId);
            Assert.Equal(QueueGroup.Queued, rows.Single(r => r.Entry.VideoId == moved.VideoId).Group);
            Assert.Equal("Needs review · 1 — nothing runs until you start it",
                rows.First(r => r.Group == QueueGroup.Review).GroupHeader);
            Assert.Equal("Queued #4", rows.Single(r => r.Entry.VideoId == moved.VideoId).StatusLabel);
        }

        [Fact]
        public void AStuckJobSaysSoInsteadOfAFrozenPercentage()
        {
            var snapshot = Snapshot();
            snapshot.Stuck.Add(snapshot.Running[0].VideoId);

            Assert.Equal("Stuck", QueueList.Build(snapshot)[0].StatusLabel);
        }

        [Fact]
        public void SummaryCountsEachGroup()
        {
            Assert.Equal("7 videos · 1 running · 3 queued · 2 need review · 1 failed",
                QueueList.Summary(QueueList.Build(Snapshot())));
            Assert.Equal("Nothing to do", QueueList.Summary(new List<QueueRow>()));
        }

        private static List<QueueRow> Tick(params string[] titles)
        {
            var rows = QueueList.Build(Snapshot());
            foreach (var row in rows.Where(r => titles.Contains(r.Entry.Title)))
            {
                row.IsSelected = true;
            }
            return rows;
        }

        [Fact]
        public void ReviewSelectionOffersQueueSkipAndMove()
        {
            var selection = QueueSelection.For(Tick("wrong way", "stop and go"));

            Assert.Equal("2 selected · both need review", selection.Summary);
            Assert.True(selection.CanStart && selection.CanSkip && selection.CanMove);
            Assert.Equal("Queue 2 for analysis", selection.StartLabel);
            Assert.Equal(QueueGroup.Review, selection.MoveGroup);
            Assert.Equal(2, selection.DecisionIds.Count);
        }

        [Fact]
        public void FailedSelectionOffersRetry()
        {
            var selection = QueueSelection.For(Tick("probe"));

            Assert.Equal("1 selected · failed", selection.Summary);
            Assert.Equal("Retry 1", selection.StartLabel);
            Assert.False(selection.CanMove);
        }

        [Fact]
        public void QueuedSelectionOnlyReorders()
        {
            // Already decided: starting again or skipping from here is not offered.
            var selection = QueueSelection.For(Tick("snow", "low sun", "bridge"));

            Assert.Equal("3 selected · all queued", selection.Summary);
            Assert.True(selection.CanMove);
            Assert.False(selection.CanStart || selection.CanSkip);
            Assert.Empty(selection.DecisionIds);
        }

        [Fact]
        public void MixedSelectionActsOnNothing()
        {
            var selection = QueueSelection.For(Tick("snow", "wrong way"));

            Assert.False(selection.CanMove || selection.CanStart || selection.CanSkip);
            Assert.Contains("mixed", selection.Summary);
        }

        [Fact]
        public void ReviewAndFailedCanBeStartedTogether()
        {
            var selection = QueueSelection.For(Tick("wrong way", "probe"));

            Assert.True(selection.CanStart && selection.CanSkip);
            Assert.False(selection.CanMove);
            Assert.Equal("Queue 2 for analysis", selection.StartLabel);
        }

        [Fact]
        public void RunningSelectionOffersNothingHere()
        {
            // Cancelling lives on the Now running card, which knows the job.
            var selection = QueueSelection.For(Tick("I-35 cut-in"));

            Assert.False(selection.CanMove || selection.CanStart || selection.CanSkip);
        }

        [Fact]
        public void NothingTickedSaysHowToStart()
            => Assert.Equal("Tick videos to act on them", QueueSelection.For(QueueList.Build(Snapshot())).Summary);

        [Fact]
        public void MovingABlockShiftsItTogether()
        {
            var ids = Enumerable.Range(0, 4).Select(_ => Guid.NewGuid()).ToList();
            var ticked = new HashSet<Guid> { ids[1], ids[2] };

            Assert.Equal(new[] { ids[1], ids[2], ids[0], ids[3] }, QueueOrdering.Move(ids, ticked, -1));
            Assert.Equal(new[] { ids[0], ids[3], ids[1], ids[2] }, QueueOrdering.Move(ids, ticked, 1));
            Assert.Equal(ids, QueueOrdering.Move(ids, new HashSet<Guid> { ids[0] }, -1));
        }
    }
}
