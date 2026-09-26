import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'screens/login_screen.dart';
import 'screens/profile_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/tournaments_screen.dart';
import 'services/auth.dart';

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
  @override
  void initState() {
    super.initState();
    widget.auth.addListener(_onAuthChanged);
  }

  @override
  void dispose() {
    widget.auth.removeListener(_onAuthChanged);
    super.dispose();
  }

  void _onAuthChanged() {
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
          NavigationDestination(icon: Icon(Icons.person), label: 'Profile'),
          NavigationDestination(
              icon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}
