import Flutter
import UIKit
import UserNotifications

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  /// Last APNs device token as a hex string, if registration succeeded.
  static var deviceToken: String?
  private var pushChannel: FlutterMethodChannel?

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)

    let channel = FlutterMethodChannel(
      name: "com.alpenglow.vgc.app/push",
      binaryMessenger: engineBridge.binaryMessenger)
    self.pushChannel = channel
    channel.setMethodCallHandler { call, result in
      switch call.method {
      case "requestPermission":
        // Ask iOS for alert/sound/badge permission, then register with APNs.
        UNUserNotificationCenter.current().requestAuthorization(
          options: [.alert, .sound, .badge]
        ) { granted, _ in
          DispatchQueue.main.async {
            if granted {
              UIApplication.shared.registerForRemoteNotifications()
            }
            result(granted)
          }
        }
      case "registerForPush":
        UIApplication.shared.registerForRemoteNotifications()
        result(nil)
      case "getToken":
        result(Self.deviceToken)
      default:
        result(FlutterMethodNotImplemented)
      }
    }
    // If APNs answered before the engine was ready, deliver now.
    if let token = Self.deviceToken {
      channel.invokeMethod("onToken", arguments: token)
    }
  }

  override func application(
    _ application: UIApplication,
    didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
  ) {
    super.application(
      application,
      didRegisterForRemoteNotificationsWithDeviceToken: deviceToken)
    let hex = deviceToken.map { String(format: "%02.2hhx", $0) }.joined()
    Self.deviceToken = hex
    pushChannel?.invokeMethod("onToken", arguments: hex)
  }

  override func application(
    _ application: UIApplication,
    didFailToRegisterForRemoteNotificationsWithError error: Error
  ) {
    super.application(
      application,
      didFailToRegisterForRemoteNotificationsWithError: error)
    pushChannel?.invokeMethod(
      "onTokenError", arguments: error.localizedDescription)
  }
}
