using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// A queue entry plus the checkbox state the table binds to.
    /// </summary>
    /// <remarks>
    /// Selection is a property of the row rather than the grid's own selection,
    /// because the two mean different things here: the highlighted row is what
    /// Preview acts on, while the ticked rows are what a batch acts on. Keeping
    /// them separate lets you look at one video while a batch of others is
    /// already ticked.
    /// </remarks>
    public class QueueRow : INotifyPropertyChanged
    {
        private bool _isSelected;
        private object? _thumbnail;

        public QueueRow(QueueEntry entry)
        {
            Entry = entry;
        }

        public QueueEntry Entry { get; }

        public bool IsSelected
        {
            get => _isSelected;
            set
            {
                if (_isSelected == value)
                {
                    return;
                }

                _isSelected = value;
                OnPropertyChanged();
            }
        }

        /// <summary>
        /// The decoded poster frame, or null until it arrives.
        /// </summary>
        /// <remarks>
        /// Typed as object because the concrete image type differs per host
        /// (Avalonia Bitmap, WPF BitmapImage) and neither belongs in the shared
        /// core. Both frameworks' Image.Source bindings accept it.
        /// </remarks>
        public object? Thumbnail
        {
            get => _thumbnail;
            set
            {
                if (ReferenceEquals(_thumbnail, value))
                {
                    return;
                }

                _thumbnail = value;
                OnPropertyChanged();
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;

        private void OnPropertyChanged([CallerMemberName] string? name = null)
            => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));

        public static List<QueueRow> From(IEnumerable<QueueEntry> entries)
        {
            var rows = new List<QueueRow>();
            foreach (var entry in entries)
            {
                rows.Add(new QueueRow(entry));
            }
            return rows;
        }
    }
}
