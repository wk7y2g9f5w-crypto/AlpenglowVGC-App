// Basic smoke test: the login screen renders its branding when no
// OAuth configuration is present (setup hint path).

import 'package:alpenglow_vgc/main.dart';
import 'package:alpenglow_vgc/services/auth.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('Login screen shows setup hint without OAuth config',
      (WidgetTester tester) async {
    SharedPreferences.setMockInitialValues({});
    final settings = await SettingsService.load();
    final auth = AuthService(const FlutterSecureStorage());

    await tester.pumpWidget(AlpenglowApp(auth: auth, settings: settings));
    await tester.pump();

    expect(find.text('Alpenglow VGC'), findsOneWidget);
    expect(find.text('Setup needed'), findsOneWidget);
  });
}
