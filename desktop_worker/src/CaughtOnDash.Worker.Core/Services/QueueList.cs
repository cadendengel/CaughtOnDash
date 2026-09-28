using System;
using System.Collections.Generic;
using System.Linq;
using CaughtOnDash.Worker.Models;
using Newtonsoft.Json;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>Where a video sits in the one queue list, in display order.</summary>
    public enum QueueGroup
    {
        Running,
        Queued,
        Review,
        Failed,
    }

    /// <summary>The backend's answer to GET worker/jobs/board/.</summary>
    public class QueueBoard
    {
        [JsonProperty("running")]
        public List<QueueEntry> Running { get; set; } = new();

        [JsonProperty("queued")]
        public List<QueueEntry> Queued { get; set; } = new();

        [JsonProperty("review")]
        public List<QueueEntry> Review { get; set; } = new();

        [JsonProperty("failed")]
        public List<QueueEntry> Failed { get; set; } = new();

        /// <summary>Running videos whose worker has gone quiet.</summary>
        [JsonProperty("stuck")]
        public List<Guid> Stuck { get; set; } = new();
    }

    /// <summary>
    /// Turns the queue snapshot into the single grouped list the window shows.
    /// </summary>
    /// <remarks>
    /// The window used to toggle between "Not started" and "Queued", so what was
    /// waiting on you and what was about to run were never on screen together,
    /// and running and failed videos were not listed at all. One list, grouped
    /// in the order things happen -- running, queued, needs review, failed --
    /// shows the whole state at a glance. It lives here rather than in a host
    /// so the WPF and Mac windows group and label things identically.
    /// </remarks>
    public static class QueueList
    {
        /// <summary>
        /// Rows in display order. A video listed in two groups -- its state
        /// changed between the backend's queries -- appears once, in the first,
        /// which is the further along of the two.
        /// </summary>
        public static List<QueueRow> Build(QueueSnapshot snapshot)
        {
            var rows = new List<QueueRow>();
            var seen = new HashSet<Guid>();

            void Add(QueueGroup group, IEnumerable<QueueEntry> entries)
            {
                var entryList = entries.Where(e => !seen.Contains(e.VideoId)).ToList();
                for (var i = 0; i < entryList.Count; i++)
                {
                    var entry = entryList[i];
                    seen.Add(entry.VideoId);
                    rows.Add(new QueueRow(entry)
                    {
                        Group = group,
                        GroupHeader = Header(group, entryList.Count),
                        StatusLabel = Status(group, entry, i, snapshot.Stuck.Contains(entry.VideoId)),
                    });
                }
            }

            Add(QueueGroup.Running, snapshot.Running);
            Add(QueueGroup.Queued, snapshot.Queued);
            Add(QueueGroup.Review, snapshot.AwaitingReview);
            Add(QueueGroup.Failed, snapshot.Failed);
            return rows;
        }

        /// <summary>The band above each group. Doubles as the grouping key.</summary>
        public static string Header(QueueGroup group, int count) => group switch
        {
            QueueGroup.Running => $"Running · {count}",
            QueueGroup.Queued => $"Queued · {count} — runs top to bottom",
            QueueGroup.Review => $"Needs review · {count} — nothing runs until you start it",
            _ => $"Failed · {count}",
        };

        private static string Status(QueueGroup group, QueueEntry entry, int index, bool stuck) => group switch
        {
            QueueGroup.Running => stuck ? "Stuck" : $"Running {entry.AnalysisProgress}%",
            QueueGroup.Queued => $"Queued #{index + 1}",
            QueueGroup.Review => "Needs review",
            _ => "Failed",
        };

        /// <summary>"8 videos · 1 running · 3 queued · 3 need review · 1 failed".</summary>
        public static string Summary(IReadOnlyCollection<QueueRow> rows)
        {
            if (rows.Count == 0)
            {
                return "Nothing to do";
            }

            var parts = new List<string> { Plural(rows.Count, "video") };
            void Count(QueueGroup group, string label)
            {
                var n = rows.Count(r => r.Group == group);
                if (n > 0) parts.Add($"{n} {label}");
            }

            Count(QueueGroup.Running, "running");
            Count(QueueGroup.Queued, "queued");
            Count(QueueGroup.Review, "need review");
            Count(QueueGroup.Failed, "failed");
            return string.Join(" · ", parts);
        }

        internal static string Plural(int n, string noun) => n == 1 ? $"1 {noun}" : $"{n} {noun}s";
    }

    /// <summary>
    /// What the action bar offers for the ticked rows.
    /// </summary>
    /// <remarks>
    /// The bar shows only the actions that make sense for what is ticked,
    /// rather than a row of buttons where half do nothing. Actions apply to one
    /// kind of video at a time, with one exception: videos needing review and
    /// failed ones can be started or skipped together, since both are waiting
    /// on the same decision.
    /// </remarks>
    public class QueueSelection
    {
        public string Summary { get; private init; } = "";
        public bool CanMove { get; private init; }
        public bool CanSkip { get; private init; }
        public bool CanStart { get; private init; }
        public string StartLabel { get; private init; } = "";

        /// <summary>The group Up/Down reorders, when CanMove.</summary>
        public QueueGroup? MoveGroup { get; private init; }

        /// <summary>The ticked videos Start and Skip act on.</summary>
        public List<Guid> DecisionIds { get; private init; } = new();

        public static QueueSelection For(IEnumerable<QueueRow> rows)
        {
            var ticked = rows.Where(r => r.IsSelected).ToList();
            if (ticked.Count == 0)
            {
                return new QueueSelection { Summary = "Tick videos to act on them" };
            }

            var groups = ticked.Select(r => r.Group).Distinct().ToList();
            var summary = $"{ticked.Count} selected · {Describe(groups, ticked.Count)}";
            var undecided = groups.All(g => g == QueueGroup.Review || g == QueueGroup.Failed);
            var single = groups.Count == 1 ? groups[0] : (QueueGroup?)null;

            if (!undecided)
            {
                var movable = single == QueueGroup.Queued;
                return new QueueSelection
                {
                    Summary = single == null ? $"{ticked.Count} selected · mixed — tick one kind to act on it" : summary,
                    CanMove = movable,
                    MoveGroup = movable ? QueueGroup.Queued : null,
                };
            }

            return new QueueSelection
            {
                Summary = summary,
                CanMove = single == QueueGroup.Review,
                MoveGroup = single == QueueGroup.Review ? QueueGroup.Review : null,
                CanSkip = true,
                CanStart = true,
                StartLabel = single == QueueGroup.Failed
                    ? $"Retry {ticked.Count}"
                    : $"Queue {ticked.Count} for analysis",
                DecisionIds = ticked.Select(r => r.Entry.VideoId).ToList(),
            };
        }

        private static string Describe(List<QueueGroup> groups, int count)
        {
            if (groups.Count > 1)
            {
                return "need review or failed";
            }

            var all = count == 1 ? "" : count == 2 ? "both " : "all ";
            return groups[0] switch
            {
                QueueGroup.Running => count == 1 ? "running" : $"{all}running",
                QueueGroup.Queued => count == 1 ? "queued" : $"{all}queued",
                QueueGroup.Review => count == 1 ? "needs review" : $"{all}need review",
                _ => count == 1 ? "failed" : $"{all}failed",
            };
        }
    }
}
