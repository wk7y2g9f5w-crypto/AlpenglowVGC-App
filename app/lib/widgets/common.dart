import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

/// Small shared widgets and helpers.
class StatusChip extends StatelessWidget {
  final String status;

  const StatusChip({super.key, required this.status});

  Color get _color {
    switch (status.toLowerCase()) {
      case 'open':
      case 'registration_open':
      case 'upcoming':
        return Colors.green;
      case 'in_progress':
      case 'live':
        return Colors.blue;
      case 'completed':
      case 'closed':
        return Colors.grey;
      default:
        return Colors.orange;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Chip(
      label: Text(
        status.replaceAll('_', ' ').toUpperCase(),
        style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold),
      ),
      backgroundColor: _color.withValues(alpha: 0.15),
      side: BorderSide(color: _color),
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
    );
  }
}

String formatLocal(DateTime utc, {bool withTime = true}) {
  final local = utc.toLocal();
  return withTime
      ? DateFormat('MMM d, h:mm a').format(local)
      : DateFormat('MMM d, yyyy').format(local);
}

String formatDateRange(String? start, String? end) {
  if (start == null || start.isEmpty) return 'Dates TBD';
  if (end == null || end.isEmpty || end == start) return start;
  return '$start → $end';
}

void showSnack(BuildContext context, String message, {bool error = false}) {
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(
      content: Text(message),
      backgroundColor: error ? Colors.red.shade700 : null,
    ),
  );
}

/// A generic "loadable" page body driven by a future.
class AsyncBody<T> extends StatelessWidget {
  final Future<T> future;
  final Widget Function(BuildContext, T) builder;
  final Future<void> Function()? onRefresh;

  const AsyncBody({
    super.key,
    required this.future,
    required this.builder,
    this.onRefresh,
  });

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<T>(
      future: future,
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError) {
          final errorBody = _error(context, snap.error);
          return onRefresh == null
              ? errorBody
              : RefreshIndicator(
                  onRefresh: onRefresh!,
                  child: SingleChildScrollView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    child: SizedBox(
                      height: MediaQuery.of(context).size.height * 0.6,
                      child: errorBody,
                    ),
                  ),
                );
        }
        final data = snap.data as T;
        final content = builder(context, data);
        if (onRefresh != null) {
          return RefreshIndicator(onRefresh: onRefresh!, child: content);
        }
        return content;
      },
    );
  }

  Widget _error(BuildContext context, Object? error) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off, size: 48, color: Colors.grey),
            const SizedBox(height: 12),
            Text(
              'Couldn\'t load data.\n${error ?? ''}',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.grey),
            ),
            const SizedBox(height: 12),
            if (onRefresh != null)
              ElevatedButton(
                onPressed: () => onRefresh!(),
                child: const Text('Retry'),
              ),
          ],
        ),
      ),
    );
  }
}

/// Human-friendly tee time start, shared by the Casual and AltShot tabs.
String formatTeeTimeWhen(String iso) {
  if (iso.isEmpty) return 'Time TBD';
  try {
    final dt = DateTime.parse(iso).toLocal();
    return DateFormat('EEE, MMM d · h:mm a').format(dt);
  } catch (_) {
    return iso;
  }
}
