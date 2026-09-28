import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_background_service/flutter_background_service.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'pages/archive_page.dart';
import 'pages/session_page.dart';
import 'service/service_core.dart';

final FlutterLocalNotificationsPlugin notifications = FlutterLocalNotificationsPlugin();

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await _initNotifications();
  await _configureService();
  runApp(const ClassHelperApp());
}

Future<void> _initNotifications() async {
  const init = AndroidInitializationSettings('@mipmap/ic_launcher');
  await notifications.initialize(
    const InitializationSettings(android: init),
    onDidReceiveNotificationResponse: (resp) => _onNotificationAction(resp.payload),
  );
}

/// 被点名通知的点击动作 → 直接触发手动速答。
void _onNotificationAction(String? payload) {
  if (payload == 'manual_alert') {
    FlutterBackgroundService().invoke('cmd', {'cmd': 'manual_alert'});
  }
}

Future<void> _configureService() async {
  final service = FlutterBackgroundService();
  await service.configure(
    androidConfiguration: AndroidConfiguration(
      onStart: serviceEntryPoint,
      isForegroundMode: true,
      autoStartOnBoot: false,
      autoStart: false,
      notificationChannelId: 'class_helper_fg',
      initialNotificationTitle: 'class-helper',
      initialNotificationContent: '服务运行中',
      foregroundServiceNotificationId: 1,
    ),
    iosConfiguration: IosConfiguration(),
  );
}

class ClassHelperApp extends StatelessWidget {
  const ClassHelperApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'class-helper',
      theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _hostCtrl = TextEditingController();
  final _tokenCtrl = TextEditingController();
  bool _starting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadPrefs();
  }

  Future<void> _loadPrefs() async {
    final prefs = await SharedPreferences.getInstance();
    _hostCtrl.text = prefs.getString(prefsHostKey) ?? '';
    _tokenCtrl.text = prefs.getString(prefsTokenKey) ?? '';
  }

  Future<void> _connect() async {
    final hostPort = _hostCtrl.text.trim();
    final token = _tokenCtrl.text.trim();
    if (hostPort.isEmpty || token.isEmpty) {
      setState(() => _error = '请填写服务器地址与 token');
      return;
    }
    setState(() {
      _starting = true;
      _error = null;
    });

    // 麦克风权限必须先在 UI isolate 申请
    final mic = await Permission.microphone.request();
    if (!mic.isGranted) {
      setState(() {
        _starting = false;
        _error = '未授予麦克风权限，无法收音';
      });
      return;
    }
    if (await Permission.notification.isDenied) {
      await Permission.notification.request(); // 告警通知
    }

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(prefsHostKey, hostPort);
    await prefs.setString(prefsTokenKey, token);

    final service = FlutterBackgroundService();
    // 把最新配置同步给服务 isolate
    service.invoke('cmd', {'cmd': 'settings', 'hostPort': hostPort, 'token': token});
    if (!await service.isRunning()) {
      try {
        await service.startService();
      } catch (e) {
        setState(() {
          _starting = false;
          _error = '启动前台服务失败: $e';
        });
        return;
      }
    }
    if (!mounted) return;
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => SessionPage(service: service)),
    );
    setState(() => _starting = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('class-helper'),
        actions: [
          IconButton(
            tooltip: '课程归档',
            icon: const Icon(Icons.folder_open),
            onPressed: () {
              final hostPort = _hostCtrl.text.trim();
              final token = _tokenCtrl.text.trim();
              if (hostPort.isEmpty) return;
              final base = hostPort.contains('://')
                  ? hostPort
                  : 'http://${hostPort.split(':')[0]}:${hostPort.contains(':') ? hostPort.split(':')[1] : '80'}';
              Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => ArchivePage(baseUrl: base, token: token),
              ));
            },
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('连接宿舍电脑（与 PC 同一网络或 Tailscale）',
                style: TextStyle(fontSize: 14, color: Colors.black54)),
            const SizedBox(height: 12),
            TextField(
              controller: _hostCtrl,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(
                labelText: '服务器地址 host:port',
                hintText: '192.168.1.100:8765',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _tokenCtrl,
              decoration: const InputDecoration(
                labelText: 'token',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 20),
            FilledButton.icon(
              onPressed: _starting ? null : _connect,
              icon: const Icon(Icons.link),
              label: Text(_starting ? '连接中...' : '连接并进入课堂'),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!, style: const TextStyle(color: Colors.red)),
            ],
            const Spacer(),
            const Text(
              '提示：首次使用请关闭本应用的电池优化，'
              '否则国产 ROM 可能在锁屏后杀掉录音服务。',
              style: TextStyle(fontSize: 12, color: Colors.black45),
            ),
          ],
        ),
      ),
    );
  }
}

/// 告警通知（UI isolate 发出；被点名时响铃+震动）。
Future<void> showAlertNotification(String question) async {
  const details = AndroidNotificationDetails(
    'class_helper_alert',
    '点名告警',
    channelDescription: '老师点名时立即通知',
    importance: Importance.max,
    priority: Priority.max,
    fullScreenIntent: true,
    enableVibration: true,
    playSound: true,
    ongoing: false,
    autoCancel: true,
  );
  await notifications.show(
    100,
    '老师点名了！速答已就绪',
    question.isEmpty ? '点开查看问题与速答要点' : question,
    const NotificationDetails(android: details),
    payload: 'manual_alert',
  );
}
