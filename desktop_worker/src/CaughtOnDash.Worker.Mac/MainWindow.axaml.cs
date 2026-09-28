using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Linq;
using Avalonia.Collections;
using Avalonia.Controls;
using Avalonia.Data;
using Avalonia.Data.Converters;
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

        public MainWindow()
        {
            InitializeComponent();

            _session = new WorkerSession();
            _session.StateChanged += OnStateChanged;
            _session.LogAppended += OnLogAppended;
            _session.QueueChanged += OnQueueChanged;

            // One list, grouped by band. Groups appear in the order their first
            // row does, which QueueList.Build makes the order things happen:
            // running, queued, needs review, failed.
            var view = new DataGridCollectionView(_rows);
            view.GroupDescriptions.Add(new DataGridPathGroupDescription(nameof(QueueRow.GroupHeader)));
            QueueGrid.ItemsSource = view;

            RebuildRows();
            Render(_session.State);
            _session.Log("Application started");

            if (!_session.IsConfigured)
            {
                _session.Log("Worker config is missing backend URL or API token.", Logger.LogLevel.Error);
                return;
            }

            _ = _session.RefreshQueuesAsync();

            // The worker never connects on its own: nothing runs until you press
            // Connect, or queue a batch.
            StartQueuePolling();
        }

        /// <summary>
        /// Keep the queue roughly current without hammering the backend.
        /// </summary>
        /// <remarks>
        /// Ten seconds is a compromise: uploads arrive rarely, but a batch you
        /// just started should visibly drain. Each poll is one request.
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
        /// Repopulate the list, preserving ticks and the highlighted row across
        /// a refresh.
        /// </summary>
        /// <remarks>
        /// A poll every ten seconds that silently cleared your selection would
        /// make choosing a large batch impossible.
        /// </remarks>
        private void RebuildRows()
        {
            var selected = new HashSet<Guid>(_rows.Where(r => r.IsSelected).Select(r => r.Entry.VideoId));
            var highlighted = (QueueGrid.SelectedItem as QueueRow)?.Entry.VideoId;

            foreach (var row in _rows)
            {
                row.PropertyChanged -= OnRowChanged;
            }
            _rows.Clear();

            foreach (var row in QueueList.Build(_snapshot))
            {
                row.IsSelected = selected.Contains(row.Entry.VideoId);
                row.PropertyChanged += OnRowChanged;
                _rows.Add(row);
                _ = LoadThumbnail(row);
            }

            QueueGrid.SelectedItem = _rows.FirstOrDefault(r => r.Entry.VideoId == highlighted);
            QueueCountText.Text = QueueList.Summary(_rows);
            EmptyText.IsVisible = _rows.Count == 0;
            UpdateActionBar();
        }

        private void OnRowChanged(object? sender, PropertyChangedEventArgs e)
        {
            if (e.PropertyName == nameof(QueueRow.IsSelected))
            {
                UpdateActionBar();
            }
        }

        /// <summary>Show only the actions that make sense for what is ticked.</summary>
        private void UpdateActionBar()
        {
            var selection = QueueSelection.For(_rows);
            SelectionText.Text = selection.Summary;
            MoveUpButton.IsVisible = selection.CanMove;
            MoveDownButton.IsVisible = selection.CanMove;
            RejectButton.IsVisible = selection.CanSkip;
            StartBatchButton.IsVisible = selection.CanStart;
            StartBatchButton.Content = selection.StartLabel;
            PreviewButton.IsEnabled = _rows.Count > 0;
        }

        private static readonly IValueConverter TickedTint = new FuncValueConverter<bool, IBrush>(
            ticked => ticked ? SolidColorBrush.Parse("#FFFBEB") : Brushes.Transparent);

        /// <summary>Tint ticked rows: they are what a batch acts on.</summary>
        private void QueueGrid_LoadingRow(object? sender, DataGridRowEventArgs e)
        {
            e.Row.Bind(DataGridRow.BackgroundProperty,
                new Binding(nameof(QueueRow.IsSelected)) { Converter = TickedTint });
        }

        /// <summary>
        /// Colour each group's band. Set in code because Avalonia has no data
        /// triggers. The group is looked up by the band's title, and set again
        /// whenever a recycled band is handed another group -- at LoadingRowGroup
        /// the band's group is not always attached yet.
        /// </summary>
        private void QueueGrid_LoadingRowGroup(object? sender, DataGridRowGroupHeaderEventArgs e)
        {
            var header = e.RowGroupHeader;
            ColourBand(header);
            header.DataContextChanged -= OnBandContextChanged;
            header.DataContextChanged += OnBandContextChanged;
        }

        private void OnBandContextChanged(object? sender, EventArgs e)
        {
            if (sender is DataGridRowGroupHeader header)
            {
                ColourBand(header);
            }
        }

        private void ColourBand(DataGridRowGroupHeader header)
        {
            if (header.DataContext is not DataGridCollectionViewGroup group)
            {
                return;
            }

            var first = _rows.FirstOrDefault(r => Equals(r.GroupHeader, group.Key));
            if (first != null)
            {
                header.Background = GroupColour.ForBand(first.Group, "band");
                header.Foreground = GroupColour.ForBand(first.Group, "bandInk");
            }
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

        private async void RefreshQueueButton_Click(object? sender, RoutedEventArgs e)
            => await _session.RefreshQueuesAsync();

        private async void ResetStaleButton_Click(object? sender, RoutedEventArgs e)
        {
            ResetStaleButton.IsEnabled = false;
            try
            {
                await _session.ResetStaleJobsAsync();
            }
            finally
            {
                ResetStaleButton.IsEnabled = true;
            }
        }

        private async void RequeueOutdatedButton_Click(object? sender, RoutedEventArgs e)
        {
            RequeueOutdatedButton.IsEnabled = false;
            try
            {
                // Result is logged to the Activity Log by the session; disabling
                // the button while it runs is enough feedback here.
                await _session.RequeueOutdatedAsync();
            }
            finally
            {
                RequeueOutdatedButton.IsEnabled = true;
            }
        }

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

        /// <summary>Open the highlighted video so it can be judged before starting it.</summary>
        private void Preview_Click(object? sender, RoutedEventArgs e)
        {
            var row = QueueGrid.SelectedItem as QueueRow
                      ?? _rows.FirstOrDefault(r => r.IsSelected)
                      ?? _rows.FirstOrDefault();

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
        /// Move the ticked rows one place within their group, then send the
        /// whole order to the backend -- priority lives server-side so every
        /// host agrees on it.
        /// </summary>
        private async System.Threading.Tasks.Task Move(int direction)
        {
            var selection = QueueSelection.For(_rows);
            if (selection.MoveGroup is not QueueGroup group)
            {
                _session.Log("Tick queued videos, or videos needing review, to reorder them.",
                    Logger.LogLevel.Warning);
                return;
            }

            var isReview = group == QueueGroup.Review;
            var entries = isReview ? _snapshot.AwaitingReview : _snapshot.Queued;
            var ticked = new HashSet<Guid>(_rows.Where(r => r.IsSelected).Select(r => r.Entry.VideoId));
            var order = QueueOrdering.Move(entries.Select(e => e.VideoId).ToList(), ticked, direction);

            // Show the new order now rather than after the round trip.
            var byId = entries.ToDictionary(e => e.VideoId);
            var reordered = order.Select(id => byId[id]).ToList();
            if (isReview) _snapshot.AwaitingReview = reordered; else _snapshot.Queued = reordered;
            RebuildRows();

            // Shared with the WPF host so the two cannot disagree: send one order
            // spanning both groups, or reordering one renumbers it into the
            // other's priority band.
            await _session.ReorderAsync(QueueOrdering.GlobalOrder(
                _snapshot.Queued, _snapshot.AwaitingReview, order, isReview));
        }

        /// <summary>
        /// Queue the ticked videos for analysis. Retrying a failed video is the
        /// same request: approving puts it back in the queue.
        /// </summary>
        private async void StartBatch_Click(object? sender, RoutedEventArgs e)
        {
            var ids = QueueSelection.For(_rows).DecisionIds;
            if (ids.Count == 0)
            {
                _session.Log("Tick videos needing review, or failed ones, to start them.",
                    Logger.LogLevel.Warning);
                return;
            }

            StartBatchButton.IsEnabled = false;
            try
            {
                await _session.StartBatchAsync(ids);
            }
            finally
            {
                StartBatchButton.IsEnabled = true;
            }
        }

        private async void Reject_Click(object? sender, RoutedEventArgs e)
        {
            var ids = QueueSelection.For(_rows).DecisionIds;
            if (ids.Count == 0)
            {
                _session.Log("Tick videos needing review, or failed ones, to skip them.",
                    Logger.LogLevel.Warning);
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
            // The pill: green while heartbeats land, red when they are refused
            // or something is wrong, grey when disconnected.
            var (pill, ink, dot) = state.HeartbeatOk switch
            {
                true => ("#DCFCE7", "#14532D", "#15803D"),
                false => ("#FEE2E2", "#7F1D1D", "#B91C1C"),
                _ when !state.IsConfigured || state.Status == "Error" => ("#FEE2E2", "#7F1D1D", "#B91C1C"),
                _ => ("#EEF0F3", "#57606A", "#8C959F"),
            };
            ConnectionPill.Background = SolidColorBrush.Parse(pill);
            ToolTip.SetTip(ConnectionPill, state.ConnectionTooltip);
            ConnectionDot.Fill = SolidColorBrush.Parse(dot);
            StatusText.Foreground = SolidColorBrush.Parse(ink);
            LastHeartbeatText.Foreground = SolidColorBrush.Parse(ink);
            StatusText.Text = state.Status;
            LastHeartbeatText.Text = state.LastHeartbeat.HasValue
                ? $"· heartbeat {state.LastHeartbeatDisplay}"
                : "";

            BackendUrlDisplay.Text = state.BackendUrl;
            CurrentJobText.Text = state.CurrentJob;
            StageText.Text = state.Stage;
            JobProgressBar.Value = state.Progress;
            ProgressText.Text = state.ProgressDisplay;

            // Show the progress card only while a job is actually running. A bar
            // sitting at 0% reads as stuck; nothing there reads as nothing running.
            var isProcessing = state.Status == "Processing";
            ProcessingPanel.IsVisible = isProcessing;
            IdleText.IsVisible = !isProcessing;
            IdleText.Text = state.IsConfigured
                ? state.CanStop ? "Connected, waiting for a job." : "Not connected. Nothing runs until you connect."
                : "Worker is not configured.";

            // One switch: Connect while disconnected, Disconnect while connected.
            StartButton.IsEnabled = state.CanStart;
            StopButton.IsEnabled = state.CanStop;
            StartButton.IsVisible = !state.CanStop;
            StopButton.IsVisible = state.CanStop;
            CancelJobButton.IsEnabled = state.CanCancelJob;
        }

        /// <summary>Is the log scrolled to the newest entry?</summary>
        /// <remarks>
        /// Within a couple of pixels: an exact comparison never matches, because
        /// the offset lands on fractional values as rows of different heights are
        /// added, and the follow would switch itself off at random.
        /// </remarks>
        private bool IsScrolledToBottom()
        {
            var scroll = LogListBox.Scroll;
            if (scroll == null)
            {
                // No scrollbar yet -- the list is shorter than its viewport, so
                // the newest entry is visible by definition.
                return true;
            }

            var remaining = scroll.Extent.Height - scroll.Viewport.Height - scroll.Offset.Y;
            return remaining <= 2.0;
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
                        Logger.LogLevel.Error => Brushes.Firebrick,
                        Logger.LogLevel.Warning => Brushes.DarkOrange,
                        _ => Brushes.Black,
                    },
                };

                // Whether to follow the newest entry is decided BEFORE the add,
                // because adding a row is what makes "at the bottom" false.
                var follow = IsScrolledToBottom();

                LogListBox.Items.Add(row);

                // Follow only when the reader is already at the bottom. Following
                // unconditionally meant scrolling up to read something was
                // impossible while a job ran -- the list yanked itself back down
                // on the next line, and lines arrive several times a second.
                // Scrolling up now pauses the follow; scrolling back to the
                // bottom resumes it, which is how a terminal tail behaves.
                //
                // Scroll before trimming, the order the WPF host has always used
                // and never crashed with. Trimming first leaves the virtualizing
                // panel briefly inconsistent and ScrollIntoView, which runs a
                // synchronous layout pass, walks straight into it -- an unhandled
                // exception on the UI thread that aborted the process and lost 18
                // queued jobs to a scroll position. Guarded as well: failing to
                // scroll is cosmetic, failing to analyze is not.
                if (follow)
                {
                    try
                    {
                        LogListBox.ScrollIntoView(LogListBox.Items.Count - 1);
                    }
                    catch (InvalidOperationException)
                    {
                        // Mid-relayout. The next line scrolls anyway.
                    }
                }

                while (LogListBox.Items.Count > MaxLogRows)
                {
                    LogListBox.Items.RemoveAt(0);
                }
            });
        }
    }
}
