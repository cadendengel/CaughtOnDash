using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using CaughtOnDash.Worker.Services;

namespace CaughtOnDash.Worker.Mac
{
    public partial class MainWindow : Window
    {
        private const int MaxLogRows = 100;

        private readonly WorkerSession _session;
        private readonly ObservableCollection<QueueRow> _rows = new();
        private QueueSnapshot _snapshot = new();
        private readonly ThumbnailCache _thumbnails = new();
        private DispatcherTimer? _queuePollTimer;
        private bool _showingReviewQueue = true;

        public MainWindow()
        {
            InitializeComponent();

            _session = new WorkerSession();
            _session.StateChanged += OnStateChanged;
            _session.LogAppended += OnLogAppended;
            _session.QueueChanged += OnQueueChanged;

            QueueGrid.ItemsSource = _rows;
            ShowReviewQueue();
            Render(_session.State);
            _session.Log("Application started");

            if (!_session.IsConfigured)
            {
                _session.Log("Worker config is missing backend URL or API token.", Logger.LogLevel.Error);
                return;
            }

            _ = _session.RefreshQueuesAsync();

            // The worker no longer auto-starts: with an approval gate, starting
            // it before anything is approved just polls an empty queue. Choose a
            // batch and press Start Batch, or Start to run whatever is queued.
            StartQueuePolling();
        }

        /// <summary>
        /// Keep the queue roughly current without hammering the backend.
        /// </summary>
        /// <remarks>
        /// Ten seconds is a compromise: uploads arrive rarely, but a batch you
        /// just started should visibly drain. Phase 3 replaces this with pushed
        /// updates.
        /// </remarks>
        private void StartQueuePolling()
        {
            _queuePollTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(10) };
            _queuePollTimer.Tick += (_, _) => _ = _session.RefreshQueuesAsync();
            _queuePollTimer.Start();
        }

        private async void StartButton_Click(object? sender, RoutedEventArgs e)
        {
            await _session.StartAsync();
        }

        private async void StopButton_Click(object? sender, RoutedEventArgs e)
        {
            await _session.StopAsync();
        }

        private async void CancelJobButton_Click(object? sender, RoutedEventArgs e)
        {
            await _session.CancelCurrentJobAsync();
        }

        // ---- queue ----

        private void OnQueueChanged(QueueSnapshot snapshot)
        {
            Dispatcher.UIThread.Post(() =>
            {
                _snapshot = snapshot;
                RebuildRows();
            });
        }

        /// <summary>
        /// Repopulate the table, preserving ticks across a refresh.
        /// </summary>
        /// <remarks>
        /// A poll every ten seconds that silently cleared your selection would
        /// make choosing a large batch impossible.
        /// </remarks>
        private void RebuildRows()
        {
            var selected = new HashSet<Guid>();
            foreach (var row in _rows)
            {
                if (row.IsSelected)
                {
                    selected.Add(row.Entry.VideoId);
                }
            }

            var entries = _showingReviewQueue ? _snapshot.AwaitingReview : _snapshot.Queued;

            _rows.Clear();
            foreach (var entry in entries)
            {
                var row = new QueueRow(entry) { IsSelected = selected.Contains(entry.VideoId) };
                _rows.Add(row);
                _ = LoadThumbnail(row);
            }

            QueueHeading.Text = _showingReviewQueue ? "Review Queue" : "Run Queue";
            QueueCountText.Text = _rows.Count == 0
                ? (_showingReviewQueue ? "Nothing awaiting review" : "Queue empty")
                : $"{_rows.Count} video{(_rows.Count == 1 ? "" : "s")}";

            // Reordering works on both tabs: arrange the review list, tick a
            // batch, and Start Batch runs it in that order. Priority is stored
            // per video regardless of approval state, and approving does not
            // reset it, so the order you set here survives the decision.
            MoveUpButton.IsEnabled = true;
            MoveDownButton.IsEnabled = true;
            StartBatchButton.IsEnabled = _showingReviewQueue;
            RejectButton.IsEnabled = _showingReviewQueue;

            ReviewTabButton.FontWeight = _showingReviewQueue ? FontWeight.Bold : FontWeight.Normal;
            QueuedTabButton.FontWeight = _showingReviewQueue ? FontWeight.Normal : FontWeight.Bold;
        }

        /// <summary>
        /// Fill in a row's poster frame once it arrives.
        /// </summary>
        /// <remarks>
        /// Fire-and-forget on purpose: the table must render immediately and
        /// fill in as images land, not wait on the network. The cache means the
        /// ten-second refresh re-decodes rather than re-downloads, and a failure
        /// leaves the placeholder in place.
        /// </remarks>
        private async System.Threading.Tasks.Task LoadThumbnail(QueueRow row)
        {
            var bytes = await _thumbnails.GetAsync(row.Entry.ThumbnailUrl);
            if (bytes == null)
            {
                return;
            }

            try
            {
                using var stream = new System.IO.MemoryStream(bytes);
                var bitmap = new Bitmap(stream);
                Dispatcher.UIThread.Post(() => row.Thumbnail = bitmap);
            }
            catch (Exception)
            {
                // Not an image, or one Avalonia cannot decode. The placeholder
                // stands; a broken thumbnail must not disturb the queue.
            }
        }

        private void ShowReviewQueue()
        {
            _showingReviewQueue = true;
            RebuildRows();
        }

        private void ReviewTab_Click(object? sender, RoutedEventArgs e) => ShowReviewQueue();

        private void QueuedTab_Click(object? sender, RoutedEventArgs e)
        {
            _showingReviewQueue = false;
            RebuildRows();
        }

        private async void RefreshQueueButton_Click(object? sender, RoutedEventArgs e)
            => await _session.RefreshQueuesAsync();

        private void SelectAll_Click(object? sender, RoutedEventArgs e)
        {
            foreach (var row in _rows)
            {
                row.IsSelected = true;
            }
        }

        private void ClearSelection_Click(object? sender, RoutedEventArgs e)
        {
            foreach (var row in _rows)
            {
                row.IsSelected = false;
            }
        }

        private List<Guid> SelectedIds()
        {
            var ids = new List<Guid>();
            foreach (var row in _rows)
            {
                if (row.IsSelected)
                {
                    ids.Add(row.Entry.VideoId);
                }
            }
            return ids;
        }

        /// <summary>Open the highlighted video so it can be judged before approving.</summary>
        private void Preview_Click(object? sender, RoutedEventArgs e)
        {
            var row = QueueGrid.SelectedItem as QueueRow
                      ?? (_rows.Count > 0 ? _rows[0] : null);

            if (row == null || string.IsNullOrWhiteSpace(row.Entry.VideoUrl))
            {
                _session.Log("Nothing to preview -- that video has no playback URL.",
                    Logger.LogLevel.Warning);
                return;
            }

            try
            {
                Process.Start(new ProcessStartInfo(row.Entry.VideoUrl) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                _session.Log($"Could not open the video: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        private async void MoveUp_Click(object? sender, RoutedEventArgs e) => await Move(-1);

        private async void MoveDown_Click(object? sender, RoutedEventArgs e) => await Move(1);

        /// <summary>
        /// Move the ticked rows one place, then send the whole order to the
        /// backend -- priority lives server-side so every host agrees on it.
        /// </summary>
        private async System.Threading.Tasks.Task Move(int direction)
        {
            var order = new List<QueueRow>(_rows);
            var indexes = new List<int>();
            for (var i = 0; i < order.Count; i++)
            {
                if (order[i].IsSelected)
                {
                    indexes.Add(i);
                }
            }

            if (indexes.Count == 0)
            {
                _session.Log("Tick a row first.", Logger.LogLevel.Warning);
                return;
            }

            // Walk from the edge the rows are moving toward, so a block of
            // adjacent selections shifts together instead of collapsing.
            if (direction < 0)
            {
                foreach (var index in indexes)
                {
                    if (index == 0) break;
                    (order[index - 1], order[index]) = (order[index], order[index - 1]);
                }
            }
            else
            {
                indexes.Reverse();
                foreach (var index in indexes)
                {
                    if (index >= order.Count - 1) break;
                    (order[index + 1], order[index]) = (order[index], order[index + 1]);
                }
            }

            _rows.Clear();
            foreach (var row in order)
            {
                _rows.Add(row);
            }

            await _session.ReorderAsync(GlobalOrder(order));
        }

        /// <summary>Shared with the WPF host so the two cannot disagree.</summary>
        private List<Guid> GlobalOrder(IReadOnlyList<QueueRow> reordered)
        {
            var ids = new List<Guid>();
            foreach (var row in reordered) ids.Add(row.Entry.VideoId);

            return QueueOrdering.GlobalOrder(
                _snapshot.Queued, _snapshot.AwaitingReview, ids, _showingReviewQueue);
        }

        private async void StartBatch_Click(object? sender, RoutedEventArgs e)
        {
            var ids = SelectedIds();
            if (ids.Count == 0)
            {
                _session.Log("Tick the videos you want to run first.", Logger.LogLevel.Warning);
                return;
            }

            StartBatchButton.IsEnabled = false;
            try
            {
                await _session.StartBatchAsync(ids);
            }
            finally
            {
                StartBatchButton.IsEnabled = _showingReviewQueue;
            }
        }

        private async void Reject_Click(object? sender, RoutedEventArgs e)
        {
            var ids = SelectedIds();
            if (ids.Count == 0)
            {
                _session.Log("Tick the videos you want to reject first.", Logger.LogLevel.Warning);
                return;
            }

            await _session.RejectAsync(ids);
        }

        // ---- status ----

        private void OnStateChanged(WorkerSessionState state)
        {
            Dispatcher.UIThread.Post(() => Render(state));
        }

        private void Render(WorkerSessionState state)
        {
            StatusText.Text = state.Status;
            StatusText.Foreground = state.Status switch
            {
                "Processing" => Brushes.RoyalBlue,
                "Idle" => Brushes.Gray,
                "Error" => Brushes.Red,
                "Missing config" => Brushes.Red,
                _ => Brushes.Gray,
            };

            BackendUrlDisplay.Text = state.BackendUrl;
            LastHeartbeatText.Text = state.LastHeartbeatDisplay;
            CurrentJobText.Text = state.CurrentJob;
            StageText.Text = state.Stage;
            JobProgressBar.Value = state.Progress;
            ProgressText.Text = state.ProgressDisplay;

            JobDetailsText.Text = state.CurrentJob == "None"
                ? "No active job"
                : $"{state.CurrentJob}\nStage: {state.Stage}\nProgress: {state.ProgressDisplay}";

            // Show the progress card only while a job is actually running.
            var isProcessing = state.Status == "Processing";
            ProcessingPanel.IsVisible = isProcessing;
            IdlePanel.IsVisible = !isProcessing;
            IdleText.Text = state.IsConfigured
                ? state.CanStop ? "Waiting for a job." : "Worker stopped."
                : "Worker is not configured.";

            StartButton.IsEnabled = state.CanStart;
            StopButton.IsEnabled = state.CanStop;
            CancelJobButton.IsEnabled = state.CanCancelJob;
        }

        private void OnLogAppended(WorkerLogEntry entry)
        {
            Dispatcher.UIThread.Post(() =>
            {
                var row = new TextBlock
                {
                    Text = entry.Display,
                    TextWrapping = TextWrapping.Wrap,
                    Foreground = entry.Level switch
                    {
                        Logger.LogLevel.Error => Brushes.Red,
                        Logger.LogLevel.Warning => Brushes.Orange,
                        _ => Brushes.Black,
                    },
                };

                LogListBox.Items.Add(row);

                while (LogListBox.Items.Count > MaxLogRows)
                {
                    LogListBox.Items.RemoveAt(0);
                }

                // Follow the newest entry. Without this the list stays pinned at
                // the top while entries scroll past below the fold, which is
                // useless during a job. The WPF host has always done this; the
                // Avalonia port did not.
                LogListBox.ScrollIntoView(LogListBox.Items.Count - 1);
            });
        }
    }
}
