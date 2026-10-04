import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../services/auth.dart';
import '../util/web_support.dart';

/// "Add to Home Screen" onboarding hint for the PWA.
///
/// Web-only: returns an empty box on native (kIsWeb false) and when the page
/// already runs as an installed PWA (standalone display mode). Dismissal is
/// persisted in shared preferences.
class InstallPrompt extends StatefulWidget {
  final SettingsService settings;

  const InstallPrompt({super.key, required this.settings});

  @override
  State<InstallPrompt> createState() => _InstallPromptState();
}

class _InstallPromptState extends State<InstallPrompt> {
  bool _dismissed = false;

  @override
  void initState() {
    super.initState();
    _dismissed = widget.settings.installPromptDismissed;
  }

  @override
  Widget build(BuildContext context) {
    // Native: never show. Installed PWA or already dismissed: never show.
    if (!kIsWeb || isStandaloneDisplayMode || _dismissed) {
      return const SizedBox.shrink();
    }
    final isIos = defaultTargetPlatform == TargetPlatform.iOS;
    final instructions = isIos
        ? 'Tap the Share button, then \u201cAdd to Home Screen\u201d.'
        : 'Tap \u22ee, then \u201cAdd to Home screen\u201d (or \u201cInstall app\u201d).';
    return Card(
      color: Colors.green.shade50,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.add_to_home_screen, color: Colors.green),
                SizedBox(width: 8),
                Text('Install Alpenglow VGC',
                    style: TextStyle(fontWeight: FontWeight.bold)),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              'Get the app on your home screen for fullscreen mode and '
              'notifications. $instructions',
            ),
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: () {
                  widget.settings.setInstallPromptDismissed(true);
                  setState(() => _dismissed = true);
                },
                child: const Text('Not now'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
