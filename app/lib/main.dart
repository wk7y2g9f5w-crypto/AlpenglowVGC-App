import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'screens/altshot_screen.dart';
import 'screens/casual_screen.dart';
import 'screens/login_screen.dart';
import 'screens/matchplay_screen.dart';
import 'screens/profile_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/tournaments_screen.dart';
import 'services/auth.dart';
import 'services/push.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final settings = await SettingsService.load();
  final auth = AuthService(const FlutterSecureStorage());
  await auth.load();

  runApp(AlpenglowApp(auth: auth, settings: settings));
}

/// Root widget: login gate + bottom-nav shell.
class AlpenglowApp extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const AlpenglowApp({super.key, required this.auth, required this.settings});

  @override
  State<AlpenglowApp> createState() => _AlpenglowAppState();
}

class _AlpenglowAppState extends State<AlpenglowApp> {
  bool _wasLoggedIn = false;

  @override
  void initState() {
    super.initState();
    _wasLoggedIn = widget.auth.isLoggedIn;
    widget.auth.addListener(_onAuthChanged);
    // Returning session (token in secure storage): make sure this
    // device's push token is registered with the server.
    if (_wasLoggedIn) {
      PushService.onLogin(widget.auth, widget.settings);
    }
  }

  @override
  void dispose() {
    widget.auth.removeListener(_onAuthChanged);
    super.dispose();
  }

  void _onAuthChanged() {
    final now = widget.auth.isLoggedIn;
    if (now && !_wasLoggedIn) {
      // Fresh login: ask for notification permission once, then
      // register this device for pushes.
      PushService.onLogin(widget.auth, widget.settings);
    } else if (!now && _wasLoggedIn) {
      PushService.onLogout(widget.auth, widget.settings);
    }
    _wasLoggedIn = now;
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Alpenglow VGC',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.green),
        useMaterial3: true,
      ),
      home: widget.auth.isLoggedIn
          ? HomeShell(auth: widget.auth, settings: widget.settings)
          : LoginScreen(auth: widget.auth, settings: widget.settings),
    );
  }
}

class HomeShell extends StatefulWidget {
  final AuthService auth;
  final SettingsService settings;

  const HomeShell({super.key, required this.auth, required this.settings});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final pages = [
      TournamentsScreen(auth: widget.auth, settings: widget.settings),
      CasualScreen(auth: widget.auth, settings: widget.settings),
      AltShotScreen(auth: widget.auth, settings: widget.settings),
      MatchplayScreen(auth: widget.auth, settings: widget.settings),
      ProfileScreen(auth: widget.auth, settings: widget.settings),
      SettingsScreen(auth: widget.auth, settings: widget.settings),
    ];
    return Scaffold(
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: _ScrollableNavBar(
        selectedIndex: _index,
        onSelected: (i) => setState(() => _index = i),
        items: const [
          _NavItem(icon: Icons.emoji_events, label: 'Tournaments'),
          _NavItem(icon: Icons.golf_course, label: 'Casual'),
          _NavItem(icon: Icons.groups, label: 'AltShot'),
          _NavItem(icon: Icons.sports_golf, label: 'Matchplay'),
          _NavItem(icon: Icons.person, label: 'Profile'),
          _NavItem(icon: Icons.settings, label: 'Settings'),
        ],
      ),
    );
  }
}

class _NavItem {
  final IconData icon;
  final String label;

  const _NavItem({required this.icon, required this.label});
}

/// Bottom tab bar that scrolls horizontally, so every tab keeps its full
/// label instead of being squeezed (Material 3 NavigationBar has no
/// scroll support). Keeps the same pill-indicator look.
class _ScrollableNavBar extends StatelessWidget {
  final int selectedIndex;
  final ValueChanged<int> onSelected;
  final List<_NavItem> items;

  const _ScrollableNavBar({
    required this.selectedIndex,
    required this.onSelected,
    required this.items,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return SafeArea(
      top: false,
      child: Container(
        // 1/16in (~10pt) lift so the bar clears the iPhone swipe-up zone.
        margin: const EdgeInsets.only(bottom: 10),
        decoration: BoxDecoration(
          color: scheme.surface,
          border: Border(top: BorderSide(color: scheme.outlineVariant)),
        ),
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: Row(
            children: [
              for (var i = 0; i < items.length; i++) _tab(scheme, i),
            ],
          ),
        ),
      ),
    );
  }

  Widget _tab(ColorScheme scheme, int i) {
    final selected = i == selectedIndex;
    final item = items[i];
    return InkWell(
      onTap: () => onSelected(i),
      child: SizedBox(
        width: 104,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
                decoration: selected
                    ? BoxDecoration(
                        color: scheme.secondaryContainer,
                        borderRadius: BorderRadius.circular(16),
                      )
                    : null,
                child: Icon(item.icon,
                    color: selected
                        ? scheme.onSecondaryContainer
                        : scheme.onSurfaceVariant),
              ),
              const SizedBox(height: 4),
              Text(
                item.label,
                maxLines: 1,
                style: TextStyle(
                  fontSize: 12,
                  color:
                      selected ? scheme.onSurface : scheme.onSurfaceVariant,
                  fontWeight:
                      selected ? FontWeight.w600 : FontWeight.normal,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
