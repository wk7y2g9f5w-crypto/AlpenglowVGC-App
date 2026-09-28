import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'screens/altshot_screen.dart';
import 'screens/casual_screen.dart';
import 'screens/login_screen.dart';
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
      ProfileScreen(auth: widget.auth, settings: widget.settings),
      SettingsScreen(auth: widget.auth, settings: widget.settings),
    ];
    return Scaffold(
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(
              icon: Icon(Icons.emoji_events), label: 'Tournaments'),
          NavigationDestination(icon: Icon(Icons.golf_course), label: 'Casual'),
          NavigationDestination(icon: Icon(Icons.groups), label: 'AltShot'),
          NavigationDestination(icon: Icon(Icons.person), label: 'Profile'),
          NavigationDestination(
              icon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}
